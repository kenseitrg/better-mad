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

export function needsReembed(current: string | null, want: string, wsAlive: boolean): boolean {
  if (current === null) return true; // nothing embedded yet
  if (current !== want) return true; // new ok run / new worker epoch
  return !wsAlive; // same doc, dead connection → frozen view
}
