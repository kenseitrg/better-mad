/**
 * Chat pane (UX §8, handoff §6.5): pwb's chat client as a React component.
 * Owns its SSE hook + reducer (panes stay independently functional — the
 * shell survives the sidecar being down: banner + disabled composer, the
 * rest of the app unaffected).
 *
 * pwb semantics (~/pwb/web/src/main.ts, cited per handoff §11): optimistic
 * user bubble on send (202 → SSE drives from here), Stop visible while
 * running, New chat refused while busy, Enter sends / Shift+Enter newlines.
 * Reconnect (trap T6): history + health refetch, transcript replaced
 * wholesale — events during the gap are gone.
 */

import { useCallback, useReducer } from "react";
import type { ChatInfo } from "../api";
import { api, ApiError } from "../api";
import { chatReducer, initialChatState } from "../state/chatState";
import { useChatEvents } from "../sse";
function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { error?: string; reason?: string } | null;
    return body?.error ?? body?.reason ?? `send failed (HTTP ${err.status})`;
  }
  return String(err);
}

export function ChatPane(props: { chatInfo: ChatInfo | null }) {
  const { chatInfo } = props;
  const [state, dispatch] = useReducer(chatReducer, undefined, initialChatState);

  // Resync on EVERY successful open (initial + reconnect): history first
  // (§6.3), then the live stream continues on top; health → busy flag.
  const resync = useCallback(() => {
    api.chatHistory().then(
      (h) => dispatch({ type: "history", messages: h.messages }),
      () => {}, // sidecar down → the banner (from chatInfo) says so
    );
    api.chatHealth().then(
      (h) => dispatch({ type: "busy", value: h.busy }),
      () => {},
    );
  }, []);

  useChatEvents(
    useCallback((event) => dispatch({ type: "chat_event", event }), []),
    resync,
  );

  const running = state.running;
  const down =
    chatInfo !== null &&
    chatInfo.state !== "ready" &&
    chatInfo.state !== undefined;
  const model = chatInfo?.model ?? null;

  const send = (text: string) => {
    dispatch({ type: "user_send", text });
    api.chatSend(text).catch((err: unknown) => {
      // 409 busy / 400 / 503: calm error marker; pwb's hard guard backstop.
      dispatch({ type: "chat_event", event: { type: "error", message: errorText(err) } });
    });
  };

  const stop = () => {
    void api.chatStop().catch(() => {}); // 409 idle is fine (pwb)
  };

  const newChat = () => {
    void api.chatNew().catch((err: unknown) => {
      dispatch({ type: "chat_event", event: { type: "error", message: errorText(err) } });
    });
  };

  return (
    <div className="pane" id="chat-pane">
      <div className="pane-head">
        Agent chat
        {model !== null && <span style={{ marginLeft: "auto", fontFamily: "var(--mono)" }}>{model}</span>}
      </div>
      {down && (
        <div className="banner error">
          {`chat unavailable (${chatInfo?.state ?? "?"})${
            chatInfo?.reason ? `: ${chatInfo.reason}` : ""
          } — plot, editor and files keep working.`}
        </div>
      )}
      <div className="chat-scroll">
        {state.messages.length === 0 && !running && (
          <div className="msg note">
            <span className="hint">
              AGENTS.md and datasets.md in this directory teach me the SDK and your data.
            </span>
          </div>
        )}
        {state.messages.map((msg, i) => (
          <div key={i} className={`msg ${msg.kind}`}>
            {msg.text}
            {(msg.toolNotes ?? []).map((note, j) => (
              <span key={j} className={`tool-note${note.isError ? " error" : ""}`}>
                {note.phase === "start"
                  ? `⚙ ${note.name}…`
                  : `⚙ ${note.name} ${note.isError ? "failed" : "done"}`}
              </span>
            ))}
          </div>
        ))}
        {running && state.messages.every((m) => !m.live) && (
          <div className="msg note">thinking…</div>
        )}
      </div>
      <div className="composer">
        {running ? (
          <button style={{ width: "100%" }} onClick={stop}>
            ■ Stop
          </button>
        ) : (
          <textarea
            id="chat-input"
            placeholder={
              down
                ? "chat unavailable"
                : "Describe the plot you want — e.g. scatter XCORD_MIDPT vs YCORD_MIDPT colored by TR.DOMFREQ"
            }
            disabled={down}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                const el = e.currentTarget;
                const text = el.value.trim();
                if (text.length === 0) return;
                el.value = "";
                send(text);
              }
            }}
          />
        )}
      </div>
      <div className="composer" style={{ borderTop: "none", paddingTop: 0 }}>
        <button
          style={{ width: "100%", fontSize: 12 }}
          disabled={running || down}
          title={running ? "refused while a turn is in flight" : undefined}
          onClick={newChat}
        >
          New chat
        </button>
      </div>
    </div>
  );
}
