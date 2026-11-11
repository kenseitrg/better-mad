/**
 * App shell (handoff §6.5): three-pane grid (chat | plot/code | files) with
 * drag-resize + persisted widths, header, and the ONE hub SSE subscription
 * feeding the reducers. Panes own their state; App only wires transport.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ChatInfo, HubEvent, WorkerInfo } from "./api";
import { api } from "./api";
import { Header } from "./header/Header";
import { ChatPane } from "./chat/ChatPane";
import { PlotPane } from "./plot/PlotPane";
import { CodePane } from "./code/CodePane";
import { FilesPane } from "./files/FilesPane";
import { initialRunState, runReducer } from "./state/runState";
import { useHubEvents } from "./sse";

// --- pane widths (CSS variables, localStorage persist; UX §1) -----------------

const CHAT_KEY = "bm.w-chat";
const FILES_KEY = "bm.w-files";
const CHAT_MIN = 220, CHAT_MAX = 560;
const FILES_MIN = 240, FILES_MAX = 520;

function storedWidth(key: string, fallback: number, min: number, max: number): number {
  const raw = window.localStorage.getItem(key);
  const n = raw === null ? NaN : Number(raw);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : fallback;
}

export function App() {
  // --- hub state ---------------------------------------------------------------

  const [runState, dispatchRun] = useReducer(runReducer, undefined, initialRunState);
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [worker, setWorker] = useState<WorkerInfo | null>(null);
  const [chatInfo, setChatInfo] = useState<ChatInfo | null>(null);
  const [plotChangedTick, setPlotChangedTick] = useState(0);
  const [outputTick, setOutputTick] = useState(0);
  const [view, setView] = useState<"preview" | "code">("preview");

  const onHubEvent = useCallback((event: HubEvent) => {
    switch (event.type) {
      case "hello": {
        setWorker(event.worker);
        setChatInfo(event.chat);
        dispatchRun({ type: "hello", status: event });
        break;
      }
      case "plot_changed":
        setPlotChangedTick((n) => n + 1);
        dispatchRun({ type: "plot_changed" });
        break;
      case "run_result":
        dispatchRun({ type: "run_result", result: event });
        break;
      case "worker":
        setWorker({
          state: event.state,
          port: event.port,
          epoch: event.epoch,
          restarts: event.restarts,
        });
        break;
      case "chat":
        setChatInfo(event);
        break;
    }
  }, []);

  useHubEvents(onHubEvent);

  // Initial REST fetch (the hello also carries all of this; this covers a
  // slow SSE — cheap and idempotent).
  useEffect(() => {
    api.health().then(
      (h) => setWorkspace(h.workspace),
      () => {},
    );
  }, []);

  const onRun = useCallback(() => {
    dispatchRun({ type: "manual_run" });
    // The SSE run_result completes the state (the hub emits it for manual
    // runs too); the catch only fires on transport-level errors.
    api.run().catch(() => {});
  }, []);

  const onAutorun = useCallback((on: boolean) => {
    api
      .autorun(on)
      .then((res) => dispatchRun({ type: "autorun", on: res.auto_run }))
      .catch(() => {});
  }, []);

  // --- layout / drag-resize -------------------------------------------------------

  const [wChat, setWChat] = useState(() => storedWidth(CHAT_KEY, 340, CHAT_MIN, CHAT_MAX));
  const [wFiles, setWFiles] = useState(() => storedWidth(FILES_KEY, 320, FILES_MIN, FILES_MAX));
  const rootRef = useRef<HTMLDivElement>(null);

  const startDrag = useCallback(
    (which: "chat" | "files") => (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const handle = e.currentTarget;
      handle.setPointerCapture(e.pointerId);
      handle.classList.add("dragging");
      const move = (ev: PointerEvent) => {
        const rect = rootRef.current?.getBoundingClientRect();
        const right = rect?.right ?? window.innerWidth;
        if (which === "chat") {
          setWChat(Math.min(CHAT_MAX, Math.max(CHAT_MIN, ev.clientX - (rect?.left ?? 0))));
        } else {
          setWFiles(Math.min(FILES_MAX, Math.max(FILES_MIN, right - ev.clientX)));
        }
      };
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.classList.remove("dragging");
        window.localStorage.setItem(CHAT_KEY, String(wChatRef.current));
        window.localStorage.setItem(FILES_KEY, String(wFilesRef.current));
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    },
    [],
  );

  // The up-handler reads the LATEST widths (closures over state would stale).
  const wChatRef = useRef(wChat);
  const wFilesRef = useRef(wFiles);
  wChatRef.current = wChat;
  wFilesRef.current = wFiles;

  return (
    <div className="app">
      <Header
        workspace={workspace}
        runState={runState}
        model={chatInfo?.model ?? null}
        onRun={onRun}
        onAutorun={onAutorun}
      />
      <div
        className="panes"
        ref={rootRef}
        style={
          {
            "--w-chat": `${wChat}px`,
            "--w-files": `${wFiles}px`,
          } as React.CSSProperties
        }
      >
        <ChatPane chatInfo={chatInfo} />
        <div className="handle" onPointerDown={startDrag("chat")} />
        <div className="pane center">
          <div className="pane-head">
            <div className="view-toggle">
              <button className={view === "preview" ? "on" : ""} onClick={() => setView("preview")}>
                ● Preview
              </button>
              <button className={view === "code" ? "on" : ""} onClick={() => setView("code")}>
                Code
              </button>
            </div>
          </div>
          {/* Both views stay alive (UX decision #2): stacked layers toggled
              with visibility — display:none would zero the live bokeh doc's
              container (stale canvas on re-show) and un-measure the editor. */}
          <div className="center-body">
            <div
              className="pane-layer"
              style={{ visibility: view === "preview" ? "visible" : "hidden" }}
            >
              <PlotPane
                worker={worker}
                runState={runState}
                onOpenOutput={() => {
                  setView("code");
                  setOutputTick((n) => n + 1);
                }}
              />
            </div>
            <div
              className="pane-layer"
              style={{ visibility: view === "code" ? "visible" : "hidden" }}
            >
              <CodePane plotChangedTick={plotChangedTick} outputTick={outputTick} runState={runState} />
            </div>
          </div>
        </div>
        <div className="handle" onPointerDown={startDrag("files")} />
        <FilesPane />
      </div>
    </div>
  );
}
