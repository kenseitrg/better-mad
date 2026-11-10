/**
 * Shared AgentSession stubs for the sidecar tests (session-stubbed per
 * handoff §11 — no network, no model, no ~/.pi). Mirrors pwb's test stubs.
 */
import type { AgentSession } from "@earendil-works/pi-coding-agent";

type StubListener = (event: {
	type: string;
	assistantMessageEvent?: { type: string; delta?: string };
	toolName?: string;
	isError?: boolean;
}) => void;

/** A stub session whose prompt emits a scripted happy turn. */
export function happySession(): {
	session: AgentSession;
	promptCalls: string[];
	disposed: () => boolean;
} {
	let disposed = false;
	const promptCalls: string[] = [];
	const session = {
		prompt: (text: string) => {
			promptCalls.push(text);
			const emit = session.subscribeListeners;
			emit?.({ type: "agent_start" });
			emit?.({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "hello" } });
			emit?.({ type: "tool_execution_start", toolName: "write" });
			emit?.({ type: "tool_execution_end", toolName: "write", isError: false });
			emit?.({ type: "agent_end" });
			return Promise.resolve();
		},
		abort: () => Promise.resolve(),
		subscribeListeners: undefined as StubListener | undefined,
		subscribe: (listener: StubListener) => {
			session.subscribeListeners = listener;
			return () => {
				session.subscribeListeners = undefined;
			};
		},
		dispose: () => {
			disposed = true;
		},
		messages: [],
		sessionFile: undefined,
	} as unknown as AgentSession & { subscribeListeners: StubListener | undefined };
	return { session, promptCalls, disposed: () => disposed };
}

/** A stub session whose prompt hangs until abort() fires (like the real SDK,
 *  where abort is the supported cancellation path). */
export function hungSession(): { session: AgentSession; aborted: () => boolean } {
	let abortCalled = false;
	let settle!: (err?: Error) => void;
	const promise = new Promise<void>((resolve, reject) => {
		settle = (err) => (err ? reject(err) : resolve());
	});
	const session = {
		prompt: () => promise,
		abort: () => {
			abortCalled = true;
			settle(new Error("aborted"));
			return Promise.resolve();
		},
		subscribe: () => () => {},
		dispose: () => {},
		messages: [],
		sessionFile: undefined,
	} as unknown as AgentSession;
	return { session, aborted: () => abortCalled };
}
