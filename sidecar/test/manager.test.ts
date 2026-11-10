/**
 * ChatManager tests (handoff M3.3 §11) — session-stubbed via the forTest
 * seam, exactly like pwb's manager.test.ts / manager.watchdog.test.ts.
 * No network, no model, no ~/.pi.
 */
import { describe, expect, it } from "vitest";
import { BusyError, ChatManager } from "../src/manager.ts";
import type { ClientEvent } from "../src/manager.ts";
import type { ChatSessionRuntime } from "../src/session.ts";
import { happySession, hungSession } from "./stubs.ts";

function runtime(session: ChatSessionRuntime["session"], modelId = "stub/test"): ChatSessionRuntime {
	return { session, modelId };
}

/** Collect broadcasts while awaiting the turn's completion. */
async function collect(manager: ChatManager, run: () => Promise<unknown>): Promise<ClientEvent[]> {
	const events: ClientEvent[] = [];
	const unsubscribe = manager.subscribe((event) => events.push(event));
	try {
		await run();
	} finally {
		unsubscribe();
	}
	return events;
}

describe("ChatManager.send", () => {
	it("happy path broadcasts the full ClientEvent sequence", async () => {
		const { session } = happySession();
		const manager = ChatManager.forTest(runtime(session));
		const events = await collect(manager, () => manager.send("hi"));
		expect(events.map((e) => e.type)).toEqual([
			"agent_start",
			"text_delta",
			"tool_execution_start",
			"tool_execution_end",
			"agent_end",
		]);
		expect(events.find((e) => e.type === "text_delta")).toEqual({ type: "text_delta", delta: "hello" });
		expect(manager.isBusy()).toBe(false);
	});

	it("second send while busy throws BusyError synchronously", async () => {
		const { session } = hungSession();
		const manager = ChatManager.forTest(runtime(session));
		const run = manager.send("first");
		expect(() => manager.send("second")).toThrow(BusyError);
		manager.stop();
		await run;
	});

	it("stop while idle returns false", () => {
		const { session } = happySession();
		const manager = ChatManager.forTest(runtime(session));
		expect(manager.stop()).toBe(false);
	});

	it("stop mid-turn aborts and broadcasts stopped, not error", async () => {
		const { session, aborted } = hungSession();
		const manager = ChatManager.forTest(runtime(session));
		const run = manager.send("first");
		expect(manager.stop()).toBe(true);
		const events = await collect(manager, () => run);
		expect(aborted()).toBe(true);
		expect(events.map((e) => e.type)).toEqual(["stopped"]);
	});

	it("watchdog timeout aborts, broadcasts error, releases busy", async () => {
		// The stub settles when abort() fires — like the real SDK, where abort
		// is the supported cancellation path (pwb's watchdog test stub).
		const { session, aborted } = hungSession();
		const manager = ChatManager.forTest(runtime(session), 20);
		const events = await collect(manager, () => manager.send("slow"));
		expect(aborted()).toBe(true);
		expect(events.map((e) => e.type)).toEqual(["error"]);
		expect(manager.isBusy()).toBe(false);
	});
});

describe("ChatManager.new", () => {
	it("swaps the session, re-wires events, broadcasts reset", async () => {
		const first = happySession();
		const second = happySession();
		const manager = ChatManager.forTest(runtime(first.session), undefined, {
			newRuntime: () => Promise.resolve(runtime(second.session, "stub/test2")),
		});
		const events = await collect(manager, () => manager.new());
		expect(events.map((e) => e.type)).toEqual(["reset"]);
		expect(first.disposed()).toBe(true);
		// The NEW session owns event wiring now: a turn emits through it.
		await manager.send("again");
		expect(second.promptCalls).toEqual(["again"]);
		expect(manager.modelId).toBe("stub/test2");
	});

	it("refuses while a turn is in flight", async () => {
		const { session } = hungSession();
		const manager = ChatManager.forTest(runtime(session), undefined, {
			newRuntime: () => Promise.resolve(runtime(happySession().session)),
		});
		const run = manager.send("first");
		await expect(manager.new()).rejects.toThrow(BusyError);
		manager.stop();
		await run;
	});
});

describe("ChatManager.history", () => {
	it("projects role+text and skips empty messages", () => {
		const { session } = happySession();
		const textBlock = (text: string) => ({ type: "text", text });
		const stubMessages = [
			{ role: "user", content: "hi" },
			{ role: "assistant", content: [textBlock("he"), textBlock("llo")] },
			{ role: "assistant", content: [textBlock("  ")] }, // whitespace-only → skipped
			{ role: "toolResult", content: "noise" }, // other roles → skipped
		];
		(session as unknown as { messages: unknown[] }).messages = stubMessages;
		const manager = ChatManager.forTest(runtime(session));
		expect(manager.history()).toEqual([
			{ role: "user", text: "hi" },
			{ role: "assistant", text: "hello" },
		]);
	});
});
