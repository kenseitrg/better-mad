/**
 * Sidecar HTTP transport (handoff M3.3 §6.3): plain node:http serving the
 * 6-route chat contract — the same paths the SPA will call; the hub proxies
 * them verbatim (dumb same-path passthrough). Ported from pwb's
 * server/routes/chat.ts (endpoint semantics + openEventStream) to node:http.
 *
 * No auth, no CORS (single origin: everything reaches the SPA through the hub).
 * Unknown path → 404; malformed JSON → 400; bodies capped at 1 MB → 413.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { BusyError, ChatManager, type ClientEvent } from "./manager.ts";

const HEARTBEAT_MS = 25_000;
const BODY_LIMIT_BYTES = 1_000_000;

export interface ChatServer {
	server: Server;
	/** The bound port (0 → the ephemeral port the kernel picked). */
	port: number;
	/** Stop accepting connections and close (does not touch the manager). */
	close: () => void;
}

/** Bind the chat transport on 127.0.0.1. Resolves once listening. */
export function createChatServer(manager: ChatManager, port: number): Promise<ChatServer> {
	const server = createServer((req, res) => {
		void route(manager, req, res);
	});
	return new Promise((resolve, reject) => {
		server.once("error", reject);
		server.listen(port, "127.0.0.1", () => {
			resolve({
				server,
				port: (server.address() as { port: number }).port,
				close: () => {
					server.close();
					server.closeAllConnections();
				},
			});
		});
	});
}

async function route(manager: ChatManager, req: IncomingMessage, res: ServerResponse): Promise<void> {
	const url = new URL(req.url ?? "/", "http://127.0.0.1");
	const path = url.pathname;
	const method = req.method ?? "GET";
	try {
		if (method === "GET" && path === "/health") {
			return json(res, 200, {
				ok: true,
				busy: manager.isBusy(),
				model: manager.modelId,
				session_file: manager.sessionFile,
			});
		}
		if (method === "GET" && path === "/api/chat/history") {
			return json(res, 200, { messages: manager.history() });
		}
		if (method === "GET" && path === "/api/chat/events") {
			return openEventStream(manager, res);
		}
		if (method === "POST" && path === "/api/chat/send") {
			const body = await readJsonBody(req, res);
			if (body === undefined) return; // response already sent (400/413)
			const text = typeof body.text === "string" ? body.text.trim() : "";
			if (text.length === 0) {
				return json(res, 400, { error: "text must be a non-empty string" });
			}
			try {
				// Fire-and-forget: the response returns immediately; streaming
				// happens over SSE. The busy check is synchronous (a BusyError
				// throws HERE, before any promise exists — pwb's route shape); the
				// returned promise never rejects (failures go out as SSE errors).
				void manager.send(text);
				return json(res, 202, { started: true });
			} catch (err) {
				if (err instanceof BusyError) {
					return json(res, 409, { error: "already processing a turn" });
				}
				throw err;
			}
		}
		if (method === "POST" && path === "/api/chat/stop") {
			if (!manager.stop()) {
				return json(res, 409, { error: "not processing a turn" });
			}
			return json(res, 202, { stopping: true });
		}
		if (method === "POST" && path === "/api/chat/new") {
			try {
				// Swap in a fresh session file; listeners get {type:"reset"} over
				// SSE and clear the transcript. The old file stays on disk.
				await manager.new();
				return json(res, 202, { reset: true });
			} catch (err) {
				if (err instanceof BusyError) {
					return json(res, 409, { error: "already processing a turn" });
				}
				throw err;
			}
		}
		return json(res, 404, { error: `not found: ${method} ${path}` });
	} catch (err) {
		// Last-resort guard: a route bug must not crash the sidecar process.
		const message = err instanceof Error ? err.message : String(err);
		console.error(`[http] ${method} ${path} failed:`, message);
		if (!res.writableEnded) json(res, 500, { error: message });
	}
}

function json(res: ServerResponse, status: number, payload: unknown): void {
	if (res.writableEnded) return;
	res.writeHead(status, { "content-type": "application/json" });
	res.end(JSON.stringify(payload));
}

/** Collect + parse a JSON request body (1 MB cap → 413; bad JSON → 400).
 * Returns undefined when a response has been sent. */
async function readJsonBody(req: IncomingMessage, res: ServerResponse): Promise<Record<string, unknown> | undefined> {
	const chunks: Buffer[] = [];
	let size = 0;
	for await (const chunk of req) {
		size += (chunk as Buffer).length;
		if (size > BODY_LIMIT_BYTES) {
			json(res, 413, { error: "request body too large" });
			return undefined;
		}
		chunks.push(chunk as Buffer);
	}
	try {
		const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8") || "null");
		if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
			throw new Error("body must be a JSON object");
		}
		return parsed as Record<string, unknown>;
	} catch (err) {
		json(res, 400, { error: `invalid JSON body: ${err instanceof Error ? err.message : err}` });
		return undefined;
	}
}

/** SSE writer — pwb's openEventStream ported to a bare ServerResponse:
 * headers immediately, retry line, per-client heartbeat, unsubscribe +
 * clearInterval on close (trap T8). Payloads are ClientEvent JSON. */
function openEventStream(manager: ChatManager, res: ServerResponse): void {
	res.writeHead(200, {
		"content-type": "text/event-stream",
		"cache-control": "no-cache",
		connection: "keep-alive",
		// Same-origin proxying only; no cross-origin replay caches.
		"x-accel-buffering": "no",
	});
	res.write(`retry: 2000\n\n`);

	const send = (payload: ClientEvent) => {
		if (!res.writableEnded) {
			res.write(`data: ${JSON.stringify(payload)}\n\n`);
		}
	};

	const unsubscribe = manager.subscribe(send);
	const heartbeat = setInterval(() => {
		if (!res.writableEnded) res.write(`: ping\n\n`);
	}, HEARTBEAT_MS);

	const close = () => {
		clearInterval(heartbeat);
		unsubscribe();
	};
	res.on("close", close);
}
