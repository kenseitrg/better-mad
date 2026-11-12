/**
 * Bokeh live-document embed (handoff §6.1, verified facts §5) — one iframe
 * per embed.
 *
 * `bokeh.embed.server_session(url, session_id)` is exactly this fetch:
 *   `${server_url}/autoload.js?bokeh-autoload-element=${elId}
 *      &bokeh-app-path=/bm&bokeh-absolute-url=${server_url}`
 *   with the `Bokeh-Session-Id` header → blob → <script src=blobUrl>.
 * The rendered content lands INSIDE the element with id `elId`. No bokeh npm
 * package, no Python client-side.
 *
 * ## Why an iframe (2026-11-12, manual-QA bug)
 * Re-embedding inside the page proved unreliable: after a bokeh WS silently
 * dies (tab sleep / network drop), BokehJS's own reconnect REPULL fails
 * ("buffer for id=… not found" — bokeh 3.x keeps ndarray buffers out of the
 * serializable doc, same M3.1 finding that made the hub re-run the last-good
 * script), leaving the client in a broken state; a second same-page embed
 * then dies with "Error rendering Bokeh items: Assertion failed". A srcdoc
 * iframe gives every embed a throwaway JS context: teardown = remove the
 * iframe (WS, registry state and all — the clean "drop the DOM" disposal of
 * the handoff, now actually complete). srcdoc inherits the parent (hub)
 * origin, so the cross-origin autoload fetch (CORS `*`) and the WebSocket
 * origin check both behave exactly as before.
 *
 * Liveness: the iframe wraps its own WebSocket and reports
 * `{bm:"ws", open}` to the parent every 2 s; PlotPane re-embeds when the
 * report says dead (state/embedPolicy.ts).
 */

export interface EmbedInfo {
  server_url: string | null;
  session_id: string;
  epoch: number;
}

/** What is currently embedded (token for reconciliation). */
let embedded: { token: string; iframe: HTMLIFrameElement } | null = null;

export function embeddedEpoch(): number | null {
  return embedded?.iframe.dataset.epoch ? Number(embedded.iframe.dataset.epoch) : null;
}

/** Identity of the embedded view: `${workerEpoch}#${okRunSeq}` (PlotPane). */
export function embeddedToken(): string | null {
  return embedded?.token ?? null;
}

export class EmbedError extends Error {}

// --- liveness reports from the live iframe -------------------------------------

let lastReport: { token: string; open: boolean } = { token: "", open: false };
let lastEmbedAt = 0;

window.addEventListener("message", (ev: MessageEvent) => {
  const d = ev.data as { bm?: string; token?: string; open?: boolean; detail?: string } | null;
  if (d === null || typeof d.bm !== "string" || typeof d.token !== "string") return;
  if (embedded !== null && d.token !== embedded.token) return; // stale iframe
  if (d.bm === "ws") lastReport = { token: d.token, open: d.open === true };
});

/** True when the embedded bokeh connection reports OPEN (grace right after embed). */
export function bokehWsAlive(): boolean {
  if (embedded === null) return false;
  if (lastReport.token !== embedded.token) return Date.now() - lastEmbedAt < 10_000;
  return lastReport.open || Date.now() - lastEmbedAt < 10_000;
}

/** The iframe document: fetch autoload.js, inject blob script, report WS state.
 *  Injected values are JSON-encoded; the app-path/session are our own constants. */
function srcdoc(token: string, serverUrl: string, sessionId: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden}
#root,#root div{height:100% !important}
</style></head><body><div id="root"></div><script>
const TOKEN = ${JSON.stringify(token)};
const SERVER_URL = ${JSON.stringify(serverUrl)};
const SESSION_ID = ${JSON.stringify(sessionId)};
const sockets = [];
window.__sockets = sockets; // debug/QA hook
const NativeWS = window.WebSocket;
window.WebSocket = class extends NativeWS {
  constructor(url, protocols) {
    super(url, protocols);
    const entry = { ws: this, closed: false };
    sockets.push(entry);
    this.addEventListener("close", () => { entry.closed = true; });
  }
};
const report = () => parent.postMessage(
  { bm: "ws", token: TOKEN, open: sockets.some((s) => !s.closed && s.ws.readyState === 1) }, "*");
setInterval(report, 2000);
const elId = crypto.randomUUID();
const host = document.createElement("div");
host.id = elId;
document.getElementById("root").append(host);
const fail = (detail) => parent.postMessage({ bm: "error", token: TOKEN, detail: String(detail) }, "*");
fetch(SERVER_URL + "/autoload.js?bokeh-autoload-element=" + elId +
      "&bokeh-app-path=/bm&bokeh-absolute-url=" + encodeURIComponent(SERVER_URL),
      { headers: { "Bokeh-Session-Id": SESSION_ID } })
  .then((r) => { if (!r.ok) throw new Error("autoload.js HTTP " + r.status); return r.blob(); })
  .then((b) => {
    const s = document.createElement("script");
    s.src = URL.createObjectURL(b);
    s.onerror = () => fail("bokehjs script load failed");
    document.body.append(s);
    setTimeout(report, 3000);
  })
  .catch(fail);
</script></body></html>`;
}

export async function embedPlot(
  container: HTMLElement,
  info: EmbedInfo,
  token: string,
  onError: (message: string) => void,
): Promise<void> {
  if (!info.server_url) {
    throw new EmbedError("no worker to embed (server_url is null)");
  }
  // Teardown of the previous embed is just DOM removal: the iframe takes its
  // WebSocket and all BokehJS state with it (see header — no same-page
  // re-embed, no zombie sockets; trap T3).
  const iframe = document.createElement("iframe");
  iframe.dataset.epoch = String(info.epoch);
  iframe.title = "plot";
  iframe.srcdoc = srcdoc(token, info.server_url, info.session_id);
  container.replaceChildren(iframe);
  embedded = { token, iframe };
  lastEmbedAt = Date.now();
  // Async failures (fetch/script/render) arrive as postMessages; surface them
  // to the caller without wedging reconciliation.
  const onMsg = (ev: MessageEvent) => {
    const d = ev.data as { bm?: string; token?: string; detail?: string } | null;
    if (d?.bm === "error" && d.token === token) {
      window.removeEventListener("message", onMsg);
      onError(d.detail ?? "unknown embed error");
    }
  };
  window.addEventListener("message", onMsg);
}

/** Drop the current embed without re-embedding (worker going down, etc.). */
export function unembedPlot(container: HTMLElement): void {
  embedded = null;
  container.replaceChildren();
}
