/**
 * Sidecar HTTP contract tests (handoff M3.3 §4, §11) — a real node:http
 * server on an ephemeral port behind a forTest-stubbed ChatManager.
 * Session-stubbed: no network, no model, no ~/.pi.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatManager } from "../src/manager.ts";
import { createChatServer, type ChatServer } from "../src/server.ts";
import type { ChatSessionRuntime } from "../src/session.ts";
import { happySession, hungSession } from "./stubs.ts";

const servers: ChatServer[] = [];

async function boot(
	session: ChatSessionRuntime["session"],
	opts: { timeoutMs?: number; newRuntime?: () => Promise<ChatSessionRuntime> } = {},
): Promise<string> {
	const manager = ChatManager.forTest(
		{ session, modelId: "stub/test" },
		opts.timeoutMs,
		opts.newRuntime ? { newRuntime: opts.newRuntime } : {},
	);
	const server = await createChatServer(manager, 0);
	servers.push(server);
	return `http://127.0.0.1:${server.port}`;
}

afterEach(async () => {
	let server = servers.pop();
	while (server) {
		server.close();
		server = servers.pop();
	}
});

/** Open an SSE reader over a fetch Response; frames are yielded one at a
 * time (a frame ends at a blank line). One reader per response — a second
 * getReader() on the same body throws (locked stream). */
function sseReader(res: Response): { next: () => Promise<string> } {
	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buf = "";
	async function next(): Promise<string> {
		for (;;) {
			const idx = buf.indexOf("\n\n");
			if (idx >= 0) {
				const frame = buf.slice(0, idx);
				buf = buf.slice(idx + 2);
				return frame;
			}
			const { value, done } = await reader.read();
			if (done) throw new Error("SSE stream ended unexpectedly");
			buf += decoder.decode(value, { stream: true });
		}
	}
	return { next };
}

async function readEvents(sse: { next: () => Promise<string> }, want: number): Promise<unknown[]> {
	const events: unknown[] = [];
	while (events.length < want) {
		const frame = await sse.next();
		if (frame.startsWith("data: ")) events.push(JSON.parse(frame.slice("data: ".length)));
	}
	return events;
}

describe("REST contract", () => {
	it("POST /api/chat/send: 202 for valid text", async () => {
		const base = await boot(happySession().session);
		const res = await fetch(`${base}/api/chat/send`, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({ text: "hi" }),
		});
		expect(res.status).toBe(202);
		expect(await res.json()).toEqual({ started: true });
	});

	it("POST /api/chat/send: 400 for empty or non-string text", async () => {
		const base = await boot(happySession().session);
		for (const body of [JSON.stringify({ text: "" }), JSON.stringify({ text: 123 }), JSON.stringify({})]) {
			const res = await fetch(`${base}/api/chat/send`, { method: "POST", body });
			expect(res.status).toBe(400);
		}
	});

	it("POST /api/chat/send: 400 for malformed JSON, 413 over the 1 MB cap", async () => {
		const base = await boot(happySession().session);
		const bad = await fetch(`${base}/api/chat/send`, { method: "POST", body: "{oops" });
		expect(bad.status).toBe(400);
		const huge = await fetch(`${base}/api/chat/send`, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify({ text: "x".repeat(1_100_000) }),
		});
		expect(huge.status).toBe(413);
	});

	it("POST /api/chat/send: 409 when a turn is already in flight; stop → 202", async () => {
		const base = await boot(hungSession().session);
		const first = await fetch(`${base}/api/chat/send`, {
			method: "POST",
			body: JSON.stringify({ text: "first" }),
		});
		expect(first.status).toBe(202);
		const second = await fetch(`${base}/api/chat/send`, {
			method: "POST",
			body: JSON.stringify({ text: "second" }),
		});
		expect(second.status).toBe(409);
		expect(await second.json()).toEqual({ error: "already processing a turn" });
		const stop = await fetch(`${base}/api/chat/stop`, { method: "POST" });
		expect(stop.status).toBe(202);
		expect(await stop.json()).toEqual({ stopping: true });
	});

	it("POST /api/chat/stop: 409 when idle", async () => {
		const base = await boot(happySession().session);
		const res = await fetch(`${base}/api/chat/stop`, { method: "POST" });
		expect(res.status).toBe(409);
		expect(await res.json()).toEqual({ error: "not processing a turn" });
	});

	it("POST /api/chat/new: 202 + reset when idle, 409 while busy", async () => {
		const base = await boot(happySession().session, {
			newRuntime: () => Promise.resolve({ session: happySession().session, modelId: "stub/test2" }),
		});
		const reset = await fetch(`${base}/api/chat/new`, { method: "POST" });
		expect(reset.status).toBe(202);
		expect(await reset.json()).toEqual({ reset: true });

		const hung = hungSession();
		const busyBase = await boot(hung.session);
		await fetch(`${busyBase}/api/chat/send`, { method: "POST", body: JSON.stringify({ text: "x" }) });
		const busy = await fetch(`${busyBase}/api/chat/new`, { method: "POST" });
		expect(busy.status).toBe(409);
		await fetch(`${busyBase}/api/chat/stop`, { method: "POST" });
	});

	it("GET /health: ok/busy/model/session_file", async () => {
		const base = await boot(happySession().session);
		const res = await fetch(`${base}/health`);
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({
			ok: true,
			busy: false,
			model: "stub/test",
			session_file: null,
		});
	});

	it("GET /api/chat/history: messages projection", async () => {
		const { session } = happySession();
		(session as unknown as { messages: unknown[] }).messages = [
			{ role: "user", content: "hi" },
			{ role: "assistant", content: [{ type: "text", text: "hello" }] },
		];
		const base = await boot(session);
		const res = await fetch(`${base}/api/chat/history`);
		expect(res.status).toBe(200);
		expect(await res.json()).toEqual({
			messages: [
				{ role: "user", text: "hi" },
				{ role: "assistant", text: "hello" },
			],
		});
	});

	it("unknown path → 404", async () => {
		const base = await boot(happySession().session);
		for (const [method, path] of [
			["GET", "/nope"],
			["POST", "/api/chat/unknown"],
			["DELETE", "/api/chat/history"],
		] as const) {
			const res = await fetch(`${base}${path}`, { method });
			expect(res.status).toBe(404);
			expect((await res.json()).error).toContain(path);
		}
	});
});

describe("SSE", () => {
	it("two listeners both receive the same broadcasts", async () => {
		const base = await boot(happySession().session);
		const sse1 = await fetch(`${base}/api/chat/events`);
		const sse2 = await fetch(`${base}/api/chat/events`);
		expect(sse1.status).toBe(200);
		expect(sse1.headers.get("content-type")).toContain("text/event-stream");
		expect(sse1.headers.get("cache-control")).toBe("no-cache");
		expect(sse1.headers.get("x-accel-buffering")).toBe("no");

		await fetch(`${base}/api/chat/send`, {
			method: "POST",
			body: JSON.stringify({ text: "hi" }),
		});

		// retry line + 5 event frames per listener
		for (const [res, tag] of [
			[sse1, "listener 1"],
			[sse2, "listener 2"],
		] as const) {
			const sse = sseReader(res);
			expect(await sse.next()).toBe("retry: 2000");
			const events = await readEvents(sse, 5);
			expect(events.map((e) => (e as { type: string }).type)).toEqual([
				"agent_start",
				"text_delta",
				"tool_execution_start",
				"tool_execution_end",
				"agent_end",
			]);
		}
	});

	it("heartbeat pings every 25 s and the interval clears on close", async () => {
		const base = await boot(happySession().session);
		vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
		try {
			const controller = new AbortController();
			const sseRes = await fetch(`${base}/api/chat/events`, { signal: controller.signal });
			const sse = sseReader(sseRes);
			expect(await sse.next()).toBe("retry: 2000"); // immediate headers + retry
			expect(vi.getTimerCount()).toBe(1); // exactly the heartbeat
			vi.advanceTimersByTime(25_000);
			expect(await sse.next()).toBe(": ping");
			controller.abort();
			const deadline = Date.now() + 2000;
			while (vi.getTimerCount() > 0 && Date.now() < deadline) {
				await new Promise((resolve) => setTimeout(resolve, 5));
			}
			expect(vi.getTimerCount()).toBe(0); // clearInterval ran on close
		} finally {
			vi.useRealTimers();
		}
	});
});
