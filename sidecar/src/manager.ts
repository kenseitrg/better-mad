/**
 * ChatManager (handoff M3.3 §6.2) — owns the single long-lived AgentSession.
 *
 * Lifted from pwb's server/agent/manager.ts (PersonaManager) with the
 * multi-persona map, daily rotation, and DB context composition dropped:
 * one agent, sessions rotate only via /new.
 *
 * Responsibilities:
 * - boot: create/resume the workspace session (history survives restart)
 * - one in-flight turn (BusyError → HTTP 409); busy keys off prompt()
 *   resolution — `agent_end` is per-low-level-run telemetry only (trap T2)
 * - fan session events out to N SSE listeners as plain JSON payloads
 * - watchdog aborts hung provider calls so a dead turn can never wedge
 *   the busy flag (pwb live finding 2026-09-29)
 * - history replay for UI reload; /new swaps the session (old file stays)
 */
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import {
	createChatSession,
	historyForClient,
	type ChatSessionRuntime,
} from "./session.ts";

/** JSON payloads sent to SSE clients (pwb's ClientEvent union, verbatim). */
export type ClientEvent =
	| { type: "text_delta"; delta: string }
	| { type: "agent_start" }
	| { type: "agent_end" }
	| { type: "tool_execution_start"; toolName: string }
	| { type: "tool_execution_end"; toolName: string; isError: boolean }
	| { type: "error"; message: string }
	| { type: "stopped" }
	/** The session was swapped for a fresh one (the "New chat" route) —
	 *  clients should clear the transcript. */
	| { type: "reset" };

export class BusyError extends Error {
	constructor() {
		super("already processing a turn");
	}
}

/**
 * Watchdog for one agent turn (lifted from pwb's manager.ts verbatim): a
 * hung provider call must never wedge the chat — after the timeout the
 * session is aborted via the pi SDK's supported cancellation path.
 */
export const DEFAULT_PROMPT_TIMEOUT_MS = 5 * 60_000;

export async function runTurnWithWatchdog(opts: {
	prompt: () => Promise<void>;
	abort: () => Promise<void>;
	timeoutMs: number;
	onTimeout?: () => void;
}): Promise<{ timedOut: boolean }> {
	let timedOut = false;
	const timer = setTimeout(() => {
		timedOut = true;
		opts.onTimeout?.();
		void opts.abort().catch(() => {});
	}, opts.timeoutMs);
	timer.unref?.();
	try {
		await opts.prompt();
	} catch (err) {
		// A rejection arriving after (because of) our abort is the expected
		// consequence of the watchdog — swallow it. Genuine provider failures
		// (timeout not fired) still propagate to the caller.
		if (!timedOut) throw err;
	} finally {
		clearTimeout(timer);
	}
	return { timedOut };
}

type Listener = (event: ClientEvent) => void;

interface ChatState {
	runtime: ChatSessionRuntime;
	listeners: Set<Listener>;
	inFlight: Promise<void> | undefined;
	/** Set by stop() while a turn is in flight — send() reports it as "stopped". */
	stopRequested: boolean;
	/** True while a session swap is in progress — send/new refuse (BusyError). */
	resetting: boolean;
}

export class ChatManager {
	private state: ChatState;
	private promptTimeoutMs = DEFAULT_PROMPT_TIMEOUT_MS;
	/** Creates a FRESH session (empty file) — the /new path. */
	private newRuntime: (() => Promise<ChatSessionRuntime>) | undefined;

	private constructor(state: ChatState) {
		this.state = state;
		this.wireEvents();
	}

	/** Boot the workspace chat session. Call once at sidecar start. */
	static async create(
		workspace: string,
		modelSpec?: string,
		opts: { promptTimeoutMs?: number } = {},
	): Promise<ChatManager> {
		const runtime = await createChatSession(workspace, modelSpec);
		const manager = new ChatManager({
			runtime,
			listeners: new Set(),
			inFlight: undefined,
			stopRequested: false,
			resetting: false,
		});
		manager.promptTimeoutMs = opts.promptTimeoutMs ?? DEFAULT_PROMPT_TIMEOUT_MS;
		manager.newRuntime = () => createChatSession(workspace, modelSpec, { fresh: true });
		// stderr: stdout belongs to the SIDECAR_READY handshake (one line, §6.4).
		console.error(`[chat] session ready (${runtime.modelId}, ${runtime.session.sessionFile ?? "no file"})`);
		return manager;
	}

	/** @internal test seam: a manager over one stubbed session state. */
	static forTest(
		runtime: ChatSessionRuntime,
		promptTimeoutMs?: number,
		opts: { newRuntime?: () => Promise<ChatSessionRuntime> } = {},
	): ChatManager {
		const manager = new ChatManager({
			runtime,
			listeners: new Set(),
			inFlight: undefined,
			stopRequested: false,
			resetting: false,
		});
		manager.promptTimeoutMs = promptTimeoutMs ?? DEFAULT_PROMPT_TIMEOUT_MS;
		manager.newRuntime = opts.newRuntime;
		return manager;
	}

	/** Subscribe session events → ClientEvent broadcasts (trap T3: only
	 * text_delta passes through from message_update). Re-run on /new —
	 * subscriptions bind to the session INSTANCE (trap T4). */
	private wireEvents(): void {
		this.state.runtime.session.subscribe((event) => {
			switch (event.type) {
				case "message_update":
					if (event.assistantMessageEvent.type === "text_delta") {
						this.broadcast({ type: "text_delta", delta: event.assistantMessageEvent.delta });
					}
					break;
				case "agent_start":
					this.broadcast({ type: "agent_start" });
					break;
				case "agent_end":
					// Per low-level run (may be followed by a retry) — display
					// telemetry only; the busy flag releases on prompt() resolution.
					this.broadcast({ type: "agent_end" });
					break;
				case "tool_execution_start":
					this.broadcast({ type: "tool_execution_start", toolName: event.toolName });
					break;
				case "tool_execution_end":
					this.broadcast({
						type: "tool_execution_end",
						toolName: event.toolName,
						isError: event.isError,
					});
					break;
				default:
					break;
			}
		});
	}

	private broadcast(event: ClientEvent): void {
		for (const listener of this.state.listeners) {
			try {
				listener(event);
			} catch (err) {
				console.error("listener failed:", err);
			}
		}
	}

	/**
	 * Abort the in-flight turn (user-facing "stop"). False when idle — the
	 * route turns that into 409. The aborted prompt settles (usually
	 * rejected); send() sees `stopRequested` and broadcasts the calm
	 * "stopped" event instead of an error.
	 */
	stop(): boolean {
		const state = this.state;
		// During a session swap there is no live turn to abort (and the session
		// being disposed would swallow it) — report idle instead.
		if (!state.inFlight || state.resetting) return false;
		state.stopRequested = true;
		// The pi SDK's supported cancellation path (same as the watchdog and
		// dispose use); the in-flight send() observes it and settles.
		void state.runtime.session.abort().catch(() => {});
		return true;
	}

	isBusy(): boolean {
		return this.state.inFlight !== undefined;
	}

	subscribe(listener: Listener): () => void {
		this.state.listeners.add(listener);
		return () => this.state.listeners.delete(listener);
	}

	history(): Array<{ role: "user" | "assistant"; text: string }> {
		return historyForClient(this.state.runtime.session);
	}

	/** Resolved model id for /health + the READY line. */
	get modelId(): string {
		return this.state.runtime.modelId;
	}

	/** Session file path, or null before the first message (trap T11). */
	get sessionFile(): string | null {
		return this.state.runtime.session.sessionFile ?? null;
	}

	/**
	 * Swap the session for a fresh one (new session file) and broadcast
	 * {type:"reset"}. The old session is disposed AFTER the new session's
	 * events are wired (trap T4, pwb swapSession order); its file remains
	 * on disk (UX §8). Refuses with BusyError while a turn (or another
	 * swap) is in flight.
	 */
	async new(): Promise<void> {
		const state = this.state;
		if (state.inFlight || state.resetting) throw new BusyError();
		if (!this.newRuntime) throw new Error("reset unavailable: no session factory");
		state.resetting = true;
		try {
			const old = state.runtime;
			state.runtime = await this.newRuntime();
			this.wireEvents();
			old.session.dispose();
			this.broadcast({ type: "reset" });
			console.error(`[chat] session rotated → ${state.runtime.session.sessionFile ?? "no file"}`);
		} finally {
			state.resetting = false;
		}
	}

	/**
	 * Start a user turn. Resolves when the full agent run (all tool rounds)
	 * finishes; streaming output arrives via subscribe() listeners.
	 *
	 * The busy check and in-flight registration are synchronous, so callers can
	 * catch BusyError without awaiting (and no double-submission race is
	 * possible). The returned promise never rejects — failures are broadcast as
	 * error events (trap T1: this is the only prompt path; never re-entrant).
	 */
	send(text: string): Promise<void> {
		const state = this.state;
		if (state.inFlight || state.resetting) throw new BusyError();
		state.stopRequested = false;

		const run = (async () => {
			try {
				const { timedOut } = await runTurnWithWatchdog({
					prompt: () => state.runtime.session.prompt(text).then(() => {}),
					abort: () => state.runtime.session.abort(),
					timeoutMs: this.promptTimeoutMs,
					onTimeout: () => {
						console.error(`[chat] prompt exceeded ${this.promptTimeoutMs} ms — aborting the run`);
						this.broadcast({
							type: "error",
							message: "The model took too long to respond — the turn was aborted. Please try again.",
						});
					},
				});
				if (state.stopRequested) {
					// User stop: report it calmly (the rejection IS the stop-abort
					// settling — expected, not an error).
					this.broadcast({ type: "stopped" });
				} else if (timedOut) {
					// The watchdog already broadcast the error event via onTimeout.
				}
			} catch (err) {
				if (state.stopRequested) {
					this.broadcast({ type: "stopped" });
				} else {
					const message = err instanceof Error ? err.message : String(err);
					console.error(`[chat] prompt failed:`, message);
					this.broadcast({ type: "error", message });
				}
			} finally {
				state.inFlight = undefined;
				state.stopRequested = false;
			}
		})();
		state.inFlight = run;
		return run;
	}

	async dispose(): Promise<void> {
		const state = this.state;
		// Actively unwedge a hung turn so shutdown does not stall on it
		// (the pi SDK abort cancels the in-flight model call; dispose() on the
		// session aborts in-flight work too — trap T5).
		if (state.inFlight) void state.runtime.session.abort().catch(() => {});
		await state.inFlight?.catch(() => {});
		state.runtime.session.dispose();
		state.listeners.clear();
	}
}
