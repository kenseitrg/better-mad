/**
 * Plot pane (thin — handoff §6.5): hosts the worker's live bokeh document
 * via embed.ts, with the worker-lifecycle banner and the staleness badge
 * from runState. The pane NEVER scrolls (trap T2) — the height chain
 * (`.plot-embed, .plot-embed div {height:100% !important}`) is the
 * R2b-verified fix for `div.bk-root {height:auto}` + stretch_both.
 *
 * Reconcile (§6.1): on worker `ready` with a new epoch → re-embed (the hub
 * re-ran the last-good script, so the fresh doc already holds the plot —
 * handoff §5). During restarting/down → keep the frozen plot + banner.
 * The same reconciliation runs on every `hello` (App feeds workerInfo from
 * both — trap T11: epoch drift after tab sleep) and on every OK run_result
 * (token bump) — plus a bokeh-WS liveness probe: a silently dead connection
 * (tab sleep / network drop, epoch unchanged) re-embeds instead of freezing
 * (manual QA 2026-11-12; see state/embedPolicy.ts).
 */

import { useEffect, useRef, useState } from "react";
import type { WorkerInfo } from "../api";
import { api } from "../api";
import { embedPlot, embeddedToken, bokehWsAlive, EmbedError } from "../embed";
import { needsReembed } from "../state/embedPolicy";
import { lastReason } from "../state/runState";
import type { RunState } from "../state/runState";

export function PlotPane(props: {
  worker: WorkerInfo | null;
  runState: RunState;
  onOpenOutput: () => void;
}) {
  const { worker, runState, onOpenOutput } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const [embedError, setEmbedError] = useState<string | null>(null);
  // Guards against duplicate concurrent embeds (effect can re-fire while a
  // fetch is in flight). `done` distinguishes settled from in-flight: a
  // settled embed may still need a liveness re-embed (dead WS, same token).
  const attempt = useRef<{ token: string; done: boolean } | null>(null);

  const hasResult = runState.lastResult !== null;

  // Slow liveness poll: a silently dead bokeh WS (tab sleep) recovers even
  // without a new run (needsReembed sees bokehWsAlive() === false).
  const [poll, setPoll] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setPoll((n) => n + 1), 20_000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;
    if (worker === null || worker.state !== "ready" || !hasResult) return;
    // Token = worker epoch + OK-run sequence: changes on every successful run
    // or restart; constant across reconnect hellos (no gratuitous flicker).
    const want = `${worker.epoch}#${runState.okSeq}`;
    if (attempt.current?.token === want && !attempt.current.done) return; // in flight
    if (!needsReembed(embeddedToken(), want, bokehWsAlive())) return;
    attempt.current = { token: want, done: false };
    setEmbedError(null);
    api
      .embed()
      .then((info) => {
        if (info.server_url === null) throw new EmbedError("worker has no port yet");
        return embedPlot(container, info, want, (msg) => setEmbedError(msg));
      })
      .then(() => {
        if (attempt.current?.token === want) attempt.current.done = true;
      })
      .catch((err: unknown) => {
        // A failed embed must not wedge reconciliation: allow a retry when
        // the next event arrives (worker event / reconnect hello).
        if (attempt.current?.token === want) attempt.current = null;
        setEmbedError(err instanceof EmbedError ? err.message : String(err));
      });
  }, [worker, hasResult, runState.okSeq, poll]);

  const badge = runState.stale && runState.lastResult !== null;
  const workerBanner =
    worker === null
      ? null
      : worker.state === "restarting"
        ? "render worker restarting…"
        : worker.state === "down"
          ? "render worker down — the hub will restart it; the last good plot stays frozen"
          : null;

  return (
    <div className="plot-pane">
      {workerBanner !== null && <div className="banner warn">{workerBanner}</div>}
      {embedError !== null && (
        <div className="banner error">
          {"embed failed: " + embedError}
        </div>
      )}
      {hasResult ? (
        <div className="plot-embed" ref={containerRef} />
      ) : (
        <div className="plot-empty">
          {worker !== null && worker.state !== "ready"
            ? "Waiting for the render worker…"
            : "Add files (right), then tell the agent what to plot."}
        </div>
      )}
      {badge && runState.lastResult !== null && (
        <div className="banner warn">
          {`⚠ plot.py failed ${
            runState.lastAt !== null
              ? new Date(runState.lastAt).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : ""
          } — showing last good result`}
          <div style={{ fontFamily: "var(--mono)", fontSize: 11, marginTop: 2 }}>
            {lastReason(runState.lastResult)}
          </div>
          <div className="actions">
            <button onClick={onOpenOutput}>Open output</button>
          </div>
        </div>
      )}
    </div>
  );
}
