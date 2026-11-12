/** runState reducer tests (handoff §10): running/stale derivation. */
import { describe, expect, it } from "vitest";
import { initialRunState, lastReason, runReducer, statusLine } from "./runState";
import type { RunResult, Status } from "../api";

const ok: RunResult = {
  status: "ok",
  run_seconds: 2.3,
  stdout_tail: "rows: 708650\n",
  stderr_tail: "",
  epoch: 1,
};
const err: RunResult = {
  status: "error",
  run_seconds: 0.6,
  stdout_tail: "",
  stderr_tail: 'KeyError: "14_01_post_stack_at"',
  epoch: 1,
};
const timeout: RunResult = { ...ok, status: "timeout", stderr_tail: "watchdog kill" };

const helloWith = (last_run: RunResult | null, auto_run = true): Status => ({
  auto_run,
  last_run,
  worker: { state: "ready", port: 1, epoch: 1, restarts: 0 },
  watcher: { last_change_ts: null, pending: false },
  chat: { state: "ready", model: "m" },
});

describe("runState", () => {
  it("plot_changed with auto-run → running (no run_started event exists)", () => {
    let s = runReducer(initialRunState(), { type: "plot_changed" });
    expect(s.phase).toBe("running");
  });

  it("plot_changed with auto-run OFF does nothing (edit waits for Run)", () => {
    let s = runReducer({ ...initialRunState(), autoRun: false }, { type: "plot_changed" });
    expect(s.phase).toBe("idle");
    expect(s.lastResult).toBeNull();
  });

  it("ok run_result → idle, not stale, ranAt recorded", () => {
    let s = runReducer(initialRunState(), { type: "plot_changed" });
    s = runReducer(s, { type: "run_result", result: ok });
    expect(s.phase).toBe("idle");
    expect(s.stale).toBe(false);
    expect(s.ranAt).not.toBeNull();
    expect(s.lastResult?.status).toBe("ok");
  });

  it("error AFTER ok → stale (last good plot still shows)", () => {
    let s = runReducer(initialRunState(), { type: "run_result", result: ok });
    s = runReducer(s, { type: "run_result", result: err });
    expect(s.stale).toBe(true);
    expect(s.ranAt).not.toBeNull(); // ok baseline kept for the badge time
  });

  it("error with NO prior ok → not stale (nothing good to fall back to)", () => {
    const s = runReducer(initialRunState(), { type: "run_result", result: err });
    expect(s.stale).toBe(false);
  });

  it("ok after stale clears staleness (fix → back to green)", () => {
    let s = runReducer(initialRunState(), { type: "run_result", result: ok });
    s = runReducer(s, { type: "run_result", result: err });
    s = runReducer(s, { type: "run_result", result: ok });
    expect(s.stale).toBe(false);
  });

  it("timeout after ok → stale, like errors", () => {
    let s = runReducer(initialRunState(), { type: "run_result", result: ok });
    s = runReducer(s, { type: "run_result", result: timeout });
    expect(s.stale).toBe(true);
  });

  it("manual_run sets running without needing a watcher event", () => {
    const s = runReducer(initialRunState(), { type: "manual_run" });
    expect(s.phase).toBe("running");
  });

  it("hello resyncs full state (auto-run + last result; stale knowledge resets)", () => {
    let s = runReducer(initialRunState(), { type: "run_result", result: ok });
    s = runReducer(s, { type: "run_result", result: err });
    expect(s.stale).toBe(true);
    s = runReducer(s, { type: "hello", status: helloWith(err) });
    expect(s.autoRun).toBe(true);
    expect(s.lastResult?.status).toBe("error");
    expect(s.stale).toBe(false); // unknowable across reloads (accepted)
    expect(s.phase).toBe("idle");
  });

  it("autorun action updates the toggle from the server echo", () => {
    let s = runReducer(initialRunState(), { type: "autorun", on: false });
    expect(s.autoRun).toBe(false);
  });
});

describe("statusLine", () => {
  it("running beats everything", () => {
    const s = { ...initialRunState(), phase: "running" as const };
    expect(statusLine(s, 0)).toBe("running…");
  });

  it("no results yet", () => {
    expect(statusLine(initialRunState(), 0)).toBe("no runs yet");
  });

  it("ok shows elapsed seconds", () => {
    const now = 1_000_000;
    const s = { ...initialRunState(), lastResult: ok, ranAt: now - 7000 };
    expect(statusLine(s, now)).toBe("✓ ran 7s ago");
  });

  it("error shows the one-line reason", () => {
    const s = { ...initialRunState(), lastResult: err, ranAt: 1 };
    expect(statusLine(s, 2000)).toBe('✗ KeyError: "14_01_post_stack_at"');
  });
});

describe("lastReason", () => {
  it("takes the last non-empty stderr line", () => {
    expect(
      lastReason({ ...err, stderr_tail: "line one\nline two\n\nKeyError: x\n" }),
    ).toBe("KeyError: x");
  });

  it("falls back to the status when stderr is empty", () => {
    expect(lastReason({ ...err, stderr_tail: "" })).toBe("error");
  });

  it("truncates very long lines", () => {
    expect(lastReason({ ...err, stderr_tail: "x".repeat(500) }).length).toBeLessThanOrEqual(201);
  });
});

describe("okSeq (embed token component — dead-WS recovery)", () => {
  it("bumps on ok runs only", () => {
    let s = initialRunState();
    s = runReducer(s, { type: "run_result", result: ok });
    expect(s.okSeq).toBe(1);
    s = runReducer(s, { type: "run_result", result: err });
    expect(s.okSeq).toBe(1);
    s = runReducer(s, { type: "run_result", result: timeout });
    expect(s.okSeq).toBe(1);
    s = runReducer(s, { type: "run_result", result: ok });
    expect(s.okSeq).toBe(2);
  });

  it("hello keeps the sequence when the hub confirms the same ok run", () => {
    let s = initialRunState();
    s = runReducer(s, { type: "run_result", result: ok });
    s = runReducer(s, { type: "hello", status: helloWith(ok) });
    expect(s.okSeq).toBe(1);
  });

  it("hello resets when there is no last run anywhere", () => {
    let s = initialRunState();
    s = runReducer(s, { type: "run_result", result: ok });
    s = runReducer(s, { type: "hello", status: helloWith(null) });
    expect(s.okSeq).toBe(0);
  });
});
