/**
 * Code pane (UX §5, handoff §6.5): CodeMirror 6 bound to plot.py.
 *
 * Save policy: explicit Ctrl+S AND debounced auto-save (1 s idle / focus
 * loss) → PUT. The PUT is atomic and no-ops on unchanged content, so
 * autosave is cheap (trap T8: autosave waits for idle — a half-typed buffer
 * still risks one bad PUT; the watcher's stability check is the backstop).
 *
 * External-change policy (the subtle part — savePolicy.ts, pure + tested):
 * on `plot_changed`, GET disk content and compare: identical → no-op (also
 * covers the echo of our own PUT, trap T4); clean+different → silent
 * reload; dirty+different → banner [Reload] [Overwrite]. Never setValue
 * when the disk content equals the buffer.
 */

import { useEffect, useRef, useState } from "react";
import { python } from "@codemirror/lang-python";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { indentOnInput } from "@codemirror/language";
import type { RunState } from "../state/runState";
import { api } from "../api";
import { externalChangePolicy } from "./savePolicy";

const AUTOSAVE_IDLE_MS = 1000;

export function CodePane(props: {
  /** Increments on every hub `plot_changed` (any writer). */
  plotChangedTick: number;
  /** Increments when the staleness badge's [Open output] is clicked. */
  outputTick: number;
  runState: RunState;
}) {
  const { plotChangedTick, outputTick, runState } = props;
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const lastSavedRef = useRef<string | null>(null); // null until initial load lands
  const saveTimerRef = useRef<number | null>(null);
  /** Bumped by every explicit user action (save/reload/overwrite) — a
   * plot_changed GET that resolves after this must be dropped (its disk
   * snapshot is stale; a fresh event follows if content still differs). */
  const userGenRef = useRef(0);
  const [banner, setBanner] = useState<string | null>(null); // disk content pending decision
  const [outputOpen, setOutputOpen] = useState(false);

  // [Open output] from the plot pane's staleness badge expands the drawer.
  useEffect(() => {
    if (outputTick > 0) setOutputOpen(true);
  }, [outputTick]);

  const save = (force = false) => {
    const view = viewRef.current;
    if (view === null || lastSavedRef.current === null) return;
    const content = view.state.doc.toString();
    if (!force && content === lastSavedRef.current) return; // nothing to save
    userGenRef.current += 1;
    api
      .putPlot(content)
      .then(() => {
        // Only mark clean if the user hasn't typed past this content mid-PUT.
        if (view.state.doc.toString() === content) lastSavedRef.current = content;
      })
      .catch(() => {}); // save failures surface via the next run/plot state
  };

  const scheduleSave = () => {
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => save(), AUTOSAVE_IDLE_MS);
  };

  // --- editor lifecycle (mount once; both views stay alive, UX decision #2) ---

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) return;
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: "// loading plot.py…",
        extensions: [
          lineNumbers(),
          history(),
          indentOnInput(),
          python(),
          oneDark,
          EditorView.lineWrapping,
          keymap.of([
            {
              key: "Mod-s",
              preventDefault: true,
              run: () => {
                save(true);
                return true;
              },
            },
            indentWithTab,
          ]),
          keymap.of(defaultKeymap),
          keymap.of(historyKeymap),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) scheduleSave();
          }),
          EditorView.domEventHandlers({
            blur: () => {
              save(); // focus loss → autosave (UX §5)
              return false;
            },
          }),
        ],
      }),
    });
    viewRef.current = view;
    api
      .getPlot()
      .then((res) => {
        if (viewRef.current !== view) return; // unmounted meanwhile
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: res.content } });
        lastSavedRef.current = res.content;
      })
      .catch(() => {}); // no plot.py yet (404): the empty state guides the user
    return () => {
      viewRef.current = null;
      view.destroy();
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- external changes (plot_changed tick from App) ---------------------------

  useEffect(() => {
    if (plotChangedTick === 0) return; // initial render, no event yet
    const view = viewRef.current;
    if (view === null) return;
    const gen = userGenRef.current; // user actions during the GET invalidate this pass
    api
      .getPlot()
      .then((res) => {
        if (viewRef.current !== view || userGenRef.current !== gen) return;
        const saved = lastSavedRef.current;
        if (saved !== null && res.content === saved) {
          // Own-write echo (PUT landed; the buffer may already have moved on).
          lastSavedRef.current = res.content;
          return;
        }
        const buffer = view.state.doc.toString();
        const dirty = saved !== null && buffer !== saved;
        switch (externalChangePolicy(buffer, res.content, dirty)) {
          case "noop":
            lastSavedRef.current = res.content;
            break;
          case "reload":
            view.dispatch({
              changes: { from: 0, to: view.state.doc.length, insert: res.content },
            });
            lastSavedRef.current = res.content;
            break;
          case "banner":
            setBanner(res.content);
            break;
        }
      })
      .catch(() => {}); // plot.py deleted → leave the buffer alone
  }, [plotChangedTick]);

  const reloadFromDisk = () => {
    const view = viewRef.current;
    if (view === null) return;
    // Re-fetch CURRENT disk — the banner snapshot may be stale (an in-flight
    // autosave can have landed since the banner was set); the user said
    // "disk wins", so load what disk holds NOW. Bumping the generation
    // drops any plot_changed handling that resolves after this action.
    userGenRef.current += 1;
    setBanner(null);
    api
      .getPlot()
      .then((res) => {
        if (viewRef.current !== view) return;
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: res.content },
        });
        lastSavedRef.current = res.content;
      })
      .catch(() => {
        // Transport failed: fall back to the (possibly stale) snapshot.
        if (banner !== null) {
          view.dispatch({
            changes: { from: 0, to: view.state.doc.length, insert: banner },
          });
          lastSavedRef.current = banner;
        }
      });
  };

  const overwrite = () => {
    save(true);
    setBanner(null); // the watcher echo comes back as `noop`
  };

  const last = runState.lastResult;

  return (
    <div className="code-pane">
      {banner !== null && (
        <div className="banner warn">
          plot.py changed on disk (agent?)
          <div className="actions">
            <button onClick={reloadFromDisk}>Reload</button>
            <button onClick={overwrite}>Overwrite</button>
          </div>
        </div>
      )}
      <div className="code-editor" ref={hostRef} />
      <details className="output-drawer" open={outputOpen} onToggle={(e) => setOutputOpen((e.target as HTMLDetailsElement).open)}>
        <summary>Output{last !== null ? ` — ${last.status} (${last.run_seconds.toFixed(1)}s)` : " — no runs yet"}</summary>
        {last !== null && (
          <>
            {last.stderr_tail.trim().length > 0 && <pre className="stderr">{last.stderr_tail}</pre>}
            {last.stdout_tail.trim().length > 0 && <pre>{last.stdout_tail}</pre>}
          </>
        )}
      </details>
    </div>
  );
}
