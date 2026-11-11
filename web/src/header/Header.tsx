/**
 * Header (handoff §6.5): workspace path · [▶ Run] [Auto-run ✓] · status ·
 * model. The status line must always answer UX §1's question: "did my last
 * change make it to the plot?" — derived from runState, refreshed with a
 * 1 s tick so "ran Xs ago" stays honest.
 */

import { useEffect, useState } from "react";
import { statusLine } from "../state/runState";
import type { RunState } from "../state/runState";

export function Header(props: {
  workspace: string | null;
  runState: RunState;
  model: string | null;
  onRun: () => void;
  onAutorun: (on: boolean) => void;
}) {
  const { workspace, runState, model, onRun, onAutorun } = props;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const status = statusLine(runState, now);
  const statusClass = runState.phase === "running"
    ? "running"
    : runState.lastResult?.status === "ok"
      ? "ok"
      : runState.lastResult !== null
        ? "bad"
        : "";

  return (
    <header className="header">
      <span className="workspace">{workspace ?? "…"}</span>
      <span className="spacer" />
      <button className="primary" onClick={onRun}>
        ▶ Run
      </button>
      <label className="toggle">
        <input
          type="checkbox"
          checked={runState.autoRun}
          onChange={(e) => onAutorun(e.target.checked)}
        />
        Auto-run
      </label>
      <span className={`status ${statusClass}`}>{status}</span>
      {model !== null && <span className="model">{model}</span>}
    </header>
  );
}
