/**
 * Bokeh live-document embed (handoff §6.1, verified facts §5):
 *
 * `bokeh.embed.server_session(url, session_id)` is exactly this fetch:
 *   `${server_url}/autoload.js?bokeh-autoload-element=${elId}
 *      &bokeh-app-path=/bm&bokeh-absolute-url=${server_url}`
 *   with the `Bokeh-Session-Id` header → blob → <script src=blobUrl>.
 * The rendered content lands INSIDE the element with id `elId`. No bokeh npm
 * package, no Python client-side.
 *
 * CORS is a non-issue for this fetch (autoload.js sends `Access-Control-*: *`)
 * — the cross-origin request from the hub-origin page to the worker port works
 * in dev and prod. The WebSocket is the origin-checked part (trap T1: pass
 * --dev-origin to the hub in dev).
 *
 * Re-embed policy (owned by PlotPane, mechanics here): on a worker `ready`
 * event with a new epoch, tear down (empty the container, revoke the blob —
 * the old BokehJS WS dies with its worker; no graceful BokehJS disposal) and
 * re-embed. The hub re-runs the last-good script on restart, so the fresh
 * document already holds the plot (handoff §5). Reconcile on every SSE
 * `hello` too (trap T11: epoch drift after tab sleep).
 */

export interface EmbedInfo {
  server_url: string | null;
  session_id: string;
  epoch: number;
}

/** What is currently embedded (epoch for staleness display + reconciliation). */
let embedded: { epoch: number; blobUrl: string } | null = null;

export function embeddedEpoch(): number | null {
  return embedded?.epoch ?? null;
}

export class EmbedError extends Error {}

export async function embedPlot(container: HTMLElement, info: EmbedInfo): Promise<void> {
  if (!info.server_url) {
    throw new EmbedError("no worker to embed (server_url is null)");
  }
  // Tear down any previous embed FIRST (trap T3: stale embeds stack — two
  // documents, doubled WS traffic, zombie rasters). The old blob URL is
  // revoked only here, never right after injection (the script must load).
  if (embedded) {
    URL.revokeObjectURL(embedded.blobUrl);
    embedded = null;
  }
  const elId = crypto.randomUUID();
  const host = document.createElement("div");
  host.id = elId;
  container.replaceChildren(host);

  const url =
    `${info.server_url}/autoload.js?bokeh-autoload-element=${elId}` +
    `&bokeh-app-path=/bm&bokeh-absolute-url=${info.server_url}`;
  let blobUrl: string;
  try {
    const resp = await fetch(url, { headers: { "Bokeh-Session-Id": info.session_id } });
    if (!resp.ok) {
      throw new EmbedError(`autoload.js fetch failed (HTTP ${resp.status})`);
    }
    blobUrl = URL.createObjectURL(await resp.blob());
  } catch (err) {
    container.replaceChildren(); // don't leave an empty host div behind
    throw err instanceof EmbedError ? err : new EmbedError(`embed failed: ${String(err)}`);
  }
  const script = document.createElement("script");
  script.src = blobUrl;
  host.append(script);
  embedded = { epoch: info.epoch, blobUrl };
}

/** Drop the current embed without re-embedding (worker going down, etc.). */
export function unembedPlot(container: HTMLElement): void {
  if (embedded) {
    URL.revokeObjectURL(embedded.blobUrl);
    embedded = null;
  }
  container.replaceChildren();
}
