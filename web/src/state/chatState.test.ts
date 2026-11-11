/** chatState reducer tests (handoff §10): pwb switch semantics. */
import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState } from "./chatState";

const ev = chatReducer;
const start = initialChatState();
const liveTexts = (s: { messages: { kind: string; text: string }[] }) =>
  s.messages.filter((m) => m.kind === "assistant").map((m) => m.text);

describe("chatState", () => {
  it("user_send adds a user bubble and sets running (pwb optimistic send)", () => {
    const s = ev(start, { type: "user_send", text: "plot it" });
    expect(s.messages).toEqual([{ kind: "user", text: "plot it" }]);
    expect(s.running).toBe(true);
  });

  it("deltas accumulate into ONE live bubble", () => {
    let s = ev(start, { type: "user_send", text: "hi" });
    s = ev(s, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "Hel" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "lo" } });
    expect(liveTexts(s)).toEqual(["Hello"]);
    expect(s.running).toBe(true);
    expect(s.messages.at(-1)?.live).toBe(true);
  });

  it("tool notes attach to the live bubble (start + end notes, pwb style)", () => {
    let s = ev(start, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "working" } });
    s = ev(s, {
      type: "chat_event",
      event: { type: "tool_execution_start", toolName: "write" },
    });
    s = ev(s, {
      type: "chat_event",
      event: { type: "tool_execution_end", toolName: "write", isError: false },
    });
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0]?.toolNotes).toEqual([
      { name: "write", phase: "start", isError: false },
      { name: "write", phase: "end", isError: false },
    ]);
    expect(liveTexts(s)).toEqual(["working"]);
  });

  it("tool note creates a live bubble when no text arrived yet", () => {
    let s = ev(start, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, {
      type: "chat_event",
      event: { type: "tool_execution_start", toolName: "bash" },
    });
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0]?.toolNotes).toHaveLength(1);
    expect(s.messages[0]?.text).toBe("");
  });

  it("failed tool end note carries isError", () => {
    let s = ev(start, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, {
      type: "chat_event",
      event: { type: "tool_execution_end", toolName: "write", isError: true },
    });
    expect(s.messages[0]?.toolNotes?.[0]).toEqual({
      name: "write",
      phase: "end",
      isError: true,
    });
  });

  it("agent_end closes the live bubble and clears running (trap T5)", () => {
    let s = ev(start, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "partial" } });
    s = ev(s, { type: "chat_event", event: { type: "agent_end" } });
    expect(s.running).toBe(false);
    expect(s.messages.at(-1)?.live).toBe(false);
    expect(liveTexts(s)).toEqual(["partial"]); // stays visible
  });

  it("a retry's agent_start re-opens a FRESH live bubble (willRetry flow)", () => {
    let s = ev(start, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "one" } });
    s = ev(s, { type: "chat_event", event: { type: "agent_end" } });
    s = ev(s, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "two" } });
    expect(liveTexts(s)).toEqual(["one", "two"]); // no merge into one bubble
    expect(s.running).toBe(true);
  });

  it("stopped leaves a calm marker, partial reply stays", () => {
    let s = ev(start, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "par" } });
    s = ev(s, { type: "chat_event", event: { type: "stopped" } });
    expect(s.running).toBe(false);
    expect(s.messages.at(-1)).toMatchObject({ kind: "stopped", text: "Stopped." });
    expect(liveTexts(s)).toEqual(["par"]);
  });

  it("reset clears the transcript and notes it calmly", () => {
    let s = ev(start, { type: "user_send", text: "x" });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "y" } });
    s = ev(s, { type: "chat_event", event: { type: "reset" } });
    expect(s.running).toBe(false);
    expect(s.messages).toEqual([{ kind: "note", text: "Fresh chat started." }]);
  });

  it("error appends an error bubble and clears running", () => {
    let s = ev(start, { type: "user_send", text: "x" });
    s = ev(s, { type: "chat_event", event: { type: "error", message: "provider down" } });
    expect(s.running).toBe(false);
    expect(s.messages.at(-1)).toMatchObject({ kind: "error", text: "Error: provider down" });
    expect(s.messages[0]).toMatchObject({ kind: "user" }); // context kept
  });

  it("history replaces the transcript wholesale (boot + reconnect)", () => {
    let s = ev(start, { type: "user_send", text: "old" });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "stream" } });
    s = ev(s, {
      type: "history",
      messages: [
        { role: "user", text: "a" },
        { role: "assistant", text: "b" },
      ],
    });
    expect(s.messages).toEqual([
      { kind: "user", text: "a" },
      { kind: "assistant", text: "b" },
    ]);
  });

  it("a delta arriving after history appends, not duplicates (§6.3 ordering)", () => {
    let s = ev(start, {
      type: "history",
      messages: [{ role: "user", text: "hi" }],
    });
    s = ev(s, { type: "chat_event", event: { type: "agent_start" } });
    s = ev(s, { type: "chat_event", event: { type: "text_delta", delta: "new turn" } });
    expect(s.messages).toEqual([
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "new turn", toolNotes: [], live: true },
    ]);
  });

  it("busy action toggles running (health resync)", () => {
    let s = ev(start, { type: "busy", value: true });
    expect(s.running).toBe(true);
    s = ev(s, { type: "busy", value: false });
    expect(s.running).toBe(false);
  });
});
