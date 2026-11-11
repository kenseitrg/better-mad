/**
 * Transcript model (PURE, tested) — React port of pwb's chat switch
 * (`~/pwb/web/src/main.ts`, mountChat's EventSource handler; live-proven
 * semantics, cited per handoff §11).
 *
 * Trap T5 semantics mirrored exactly: `agent_end` fires per low-level run
 * (a provider retry's `willRetry` may follow) → running=false briefly; the
 * retry's `agent_start` re-opens a fresh live bubble. `stopped`/`error` are
 * the calm end markers; the send-side 409 is the hard guard.
 *
 * Ordering rule (§6.3): history loads first (replaces wholesale), then live
 * events append on top; a reconnect refetch replaces wholesale again.
 */

import type { ChatHistoryMessage, ChatEvent } from "../api";

export type MsgKind = "user" | "assistant" | "error" | "stopped" | "note";

export interface ToolNote {
  name: string;
  phase: "start" | "end";
  isError: boolean;
}

export interface Msg {
  kind: MsgKind;
  text: string;
  /** Tool notes attach to assistant bubbles (pwb's `span.tool-note`). */
  toolNotes?: ToolNote[];
  /** True while this bubble is receiving the current stream. */
  live?: boolean;
}

export interface ChatState {
  messages: Msg[];
  running: boolean;
}

export type ChatAction =
  | { type: "user_send"; text: string }
  | { type: "busy"; value: boolean }
  | { type: "history"; messages: ChatHistoryMessage[] }
  | { type: "chat_event"; event: ChatEvent };

export function initialChatState(): ChatState {
  return { messages: [], running: false };
}

function openLive(messages: Msg[]): Msg[] {
  // pwb: `liveBubble = undefined` on agent_start — the NEXT delta/note
  // creates a fresh bubble (a retry re-opens; the old bubble stays closed).
  return [...messages, { kind: "assistant", text: "", toolNotes: [], live: true }];
}

function appendToLive(messages: Msg[], delta: string): Msg[] {
  const last = messages[messages.length - 1];
  if (last && last.kind === "assistant" && last.live) {
    const updated: Msg = { ...last, text: last.text + delta };
    return [...messages.slice(0, -1), updated];
  }
  // pwb appendToLive creates the bubble when missing (e.g. delta before a
  // tool note, or a stream with no agent_start — defensive).
  return [...messages, { kind: "assistant", text: delta, toolNotes: [], live: true }];
}

function addToolNote(messages: Msg[], note: ToolNote): Msg[] {
  const last = messages[messages.length - 1];
  if (last && last.kind === "assistant" && last.live) {
    return [...messages.slice(0, -1), { ...last, toolNotes: [...(last.toolNotes ?? []), note] }];
  }
  // pwb addToolNote creates the live bubble when missing.
  return [...messages, { kind: "assistant", text: "", toolNotes: [note], live: true }];
}

function appendMarker(messages: Msg[], msg: Msg): Msg[] {
  // pwb: markers close the live bubble (liveBubble = undefined).
  const last = messages[messages.length - 1];
  const closed =
    last && last.live ? [...messages.slice(0, -1), { ...last, live: false }] : messages;
  return [...closed, msg];
}

/** The pwb event switch, as a reducer (§6.3). */
export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "user_send": {
      // pwb submit: optimistic user bubble + running (the SSE agent_start
      // confirms; a 409 from send resets via the error path).
      return {
        messages: [...state.messages, { kind: "user", text: action.text }],
        running: true,
      };
    }
    case "busy":
      return { ...state, running: action.value };
    case "history":
      // Wholesale replace (boot + reconnect): history is the truth (§6.2).
      return {
        ...state,
        messages: action.messages.map((m) => ({
          kind: m.role === "user" ? ("user" as const) : ("assistant" as const),
          text: m.text,
        })),
      };
    case "chat_event":
      return applyChatEvent(state, action.event);
  }
}

function applyChatEvent(state: ChatState, event: ChatEvent): ChatState {
  switch (event.type) {
    case "agent_start":
      // pwb: setRunning(true); liveBubble = undefined (fresh bubble opens
      // with the first delta/note of this run).
      return { messages: openLive(state.messages), running: true };
    case "text_delta":
      return { ...state, messages: appendToLive(state.messages, event.delta) };
    case "tool_execution_start":
      // pwb: `⚙ name…`
      return {
        ...state,
        messages: addToolNote(state.messages, {
          name: event.toolName,
          phase: "start",
          isError: false,
        }),
      };
    case "tool_execution_end":
      // pwb: `⚙ name done|failed` (a second note, mirroring pwb exactly)
      return {
        ...state,
        messages: addToolNote(state.messages, {
          name: event.toolName,
          phase: "end",
          isError: event.isError,
        }),
      };
    case "agent_end":
      // Trap T5: per low-level run; a retry re-opens via agent_start.
      return { messages: closeLive(state.messages), running: false };
    case "stopped":
      // pwb: calm marker, no error bubble; partial reply stays visible.
      return {
        messages: appendMarker(state.messages, { kind: "stopped", text: "Stopped." }),
        running: false,
      };
    case "reset":
      // pwb: drop the transcript, note it calmly.
      return {
        messages: [{ kind: "note", text: "Fresh chat started." }],
        running: false,
      };
    case "error":
      return {
        messages: appendMarker(state.messages, {
          kind: "error",
          text: `Error: ${event.message}`,
        }),
        running: false,
      };
  }
}

function closeLive(messages: Msg[]): Msg[] {
  const last = messages[messages.length - 1];
  return last && last.live ? [...messages.slice(0, -1), { ...last, live: false }] : messages;
}
