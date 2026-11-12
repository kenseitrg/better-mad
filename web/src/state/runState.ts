/**
 * Run/staleness derivation (PURE, tested) from hub events + REST state
 * (handoff §6.4).
 *
 * No `run_started` event exists (§5): "running…" = `plot_changed` arrived
 * with auto-run on, or the user clicked Run — cleared by the next
 * `run_result`. `stale` = the last result is error/timeout AND an ok result
 * existed before (in this page session) — the worker document still shows
 * the last good plot (design §5), so the badge says so.
 */

import type { RunResult, Status } from "../api";

export interface RunState {
  phase: "idle" | "running";
  lastResult: RunResult | null;
  /** True → badge "⚠ plot.py failed HH:MM — showing last good result". */
  stale: boolean;
  /** When the last OK run completed ("ran Xs ago" + stale baseline). */
  ranAt: number | null;
  /** When the last result of ANY status arrived (staleness badge time). */
  lastAt: number | null;
  /** Bumped per OK run — the embed token's run component (dead-WS recovery). */
  okSeq: number;
  autoRun: boolean;
}

export type RunAction =
  | { type: "hello"; status: Status }
  | { type: "plot_changed" }
  | { type: "manual_run" }
  | { type: "run_result"; result: RunResult }
  | { type: "autorun"; on: boolean };

export function initialRunState(): RunState {
  return {
    phase: "idle",
    lastResult: null,
    stale: false,
    ranAt: null,
    lastAt: null,
    okSeq: 0,
    autoRun: true,
  };
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "hello": {
      // Authoritative full-state sync (boot + every reconnect). In-session
      // staleness knowledge resets: hello carries only the LAST result, so
      // "an ok existed before" is unknowable across reloads (accepted).
      const lastResult = action.status.last_run;
      return {
        phase: "idle",
        lastResult,
        stale: false,
        ranAt: null,
        lastAt: null,
        // A reconnect keeps the run sequence when the hub confirms the same
        // ok run (no gratuitous re-embed/flicker); no runs anywhere → reset.
        okSeq: lastResult?.status === "ok" ? state.okSeq : 0,
        autoRun: action.status.auto_run,
      };
    }
    case "plot_changed":
      // Only auto-run makes this "running" (auto-run off: edit does nothing
      // until Run — UX §5 / manual QA item).
      return state.autoRun ? { ...state, phase: "running" } : state;
    case "manual_run":
      return { ...state, phase: "running" };
    case "run_result": {
      const ok = action.result.status === "ok";
      const now = Date.now();
      return {
        phase: "idle",
        lastResult: action.result,
        // stale only when a good plot existed to fall back to (§6.4)
        stale: !ok && state.ranAt !== null,
        ranAt: ok ? now : state.ranAt,
        lastAt: now,
        okSeq: ok ? state.okSeq + 1 : state.okSeq,
        autoRun: state.autoRun,
      };
    }
    case "autorun":
      return { ...state, autoRun: action.on };
  }
}

/** One-line header status (UX §1: "did my last change make it to the plot?"). */
export function statusLine(state: RunState, now: number): string {
  if (state.phase === "running") return "running…";
  const last = state.lastResult;
  if (last === null) return "no runs yet";
  if (last.status === "ok") {
    if (state.ranAt === null) return "✓ last run ok";
    const s = Math.max(0, Math.round((now - state.ranAt) / 1000));
    return s < 60 ? `✓ ran ${s}s ago` : `✓ ran ${Math.floor(s / 60)}m ago`;
  }
  return `✗ ${lastReason(last)}`;
}

/** Last non-empty stderr line — the banner/body text (§6.4). */
export function lastReason(result: RunResult): string {
  const lines = result.stderr_tail.split("\n").filter((l) => l.trim().length > 0);
  const reason = lines[lines.length - 1] ?? result.status;
  return reason.length > 200 ? reason.slice(0, 200) + "…" : reason;
}
