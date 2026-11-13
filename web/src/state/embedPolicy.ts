/**
 * Embed reconciliation policy (PURE, tested).
 *
 * The plot pane re-embeds when:
 *  - the wanted token differs from the embedded one — a new OK run landed
 *    (fresh document) or the worker epoch changed (restart), or
 *  - the token matches but the bokeh WebSocket is dead — the connection can
 *    die silently (tab sleep / network drop) while the worker epoch stays the
 *    same; without this check the canvas freezes forever (manual QA 2026-11-12).
 */

import type { RunResult } from "../api";

export function needsReembed(current: string | null, want: string, wsAlive: boolean): boolean {
  if (current === null) return true; // nothing embedded yet
  if (current !== want) return true; // new ok run / new worker epoch
  return !wsAlive; // same doc, dead connection → frozen view
}

/**
 * Does this run result bump the embed token? (PURE, M3.5 §6.5.)
 *
 * An ok run WITH a figure re-embedded: fresh document. An ok run WITHOUT a
 * figure does NOT — the document still holds the last good roots, and
 * re-embedding would resurrect them right under the placeholder. Errors and
 * timeouts change nothing. Old payloads without `has_figure` count as having
 * a figure (missing → true — never fight a stale-client placeholder).
 */
export function shouldBumpForRun(result: RunResult): boolean {
  return result.status === "ok" && result.has_figure !== false;
}
