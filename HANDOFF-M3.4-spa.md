# HANDOFF — M3.4: SPA shell (React/TS three-pane app)

You are implementing milestone step **M3.4**: `web/` — the user-facing shell. Three
panes (chat | plot/code | files), a header, and the glue that makes M3.1–M3.3 feel
like one instrument. This file is your single source of instructions. Where it
conflicts with your instincts, this file wins.

M3.1 (worker), M3.2 (hub), M3.3 (sidecar) are **done and live-verified** (M3.3 live
smoke: chat → write-tool → `plot.py` → watcher → `run_result` in one loop; kill -9
restarts; clean shutdown). Treat them as stable dependencies. You create `web/`
and make three small, tested edits to the hub/CLI layer (§7).

Decisions already made for you (do not relitigate):

- **Stack**: React 19 + Vite + TypeScript strict. No router (single view), no SSR,
  no state library (plain hooks + two custom SSE hooks), no UI framework (plain CSS,
  CSS variables for pane widths). pwb's thin fetch-and-render discipline.
- **Editor**: CodeMirror 6 (`@codemirror/lang-python`, one-dark theme).
- **Tests**: vitest **^3.2** + `@testing-library/react`. NOT vitest 4 — its native
  parser needs AVX2; this CPU is AVX1 (M3.3 finding, SIGILL). Pure-JS vitest 3 runs
  clean. Keep network out of unit tests: test reducers/state logic, not fetches.
- **Styling**: dark-first minimal now (match `scripts/m32_qa_demo.py`'s `#1a1a1a`
  baseline); full theme/empty-state polish is M3.5.
- **The "script ran but no `show()`" placeholder is deferred to M3.5** (it needs a
  worker `run_result` extension — `has_figure` from doc roots; do not touch the
  worker in M3.4). M3.4 treats any ok run as good.

## 1. Mission

```
BROWSER (you build)
┌───────────────────────────────────────────────────────────────┐
│ Header: workspace path · [▶ Run] [Auto-run ✓] · status · model │
├──────────────┬──────────────────────────────┬─────────────────┤
│ ChatPane     │ PlotPane ▾ / CodePane         │ FilesPane       │
│ (React port  │ • bokeh live doc (embed)      │ (basic: list +  │
│  of pwb's    │ • CodeMirror 6 on plot.py     │  import; M3.5   │
│  client)     │ staleness badge · Output      │  enriches)      │
└──────────────┴──────────────────────────────┴─────────────────┘
   │ REST + SSE under /api (hub, port 5006)      │ embed: worker's
   │            chat under /api/chat/* (proxied) │ own port (live doc,
   ▼                                            ▼ WebSocket)
   HUB (M3.2, done) ──────► SIDECAR (M3.3, done) / WORKER (M3.1, done)
```

**Exit criteria (PLAN.md M3.4):** type *"scatter XCORD_MIDPT vs YCORD_MIDPT colored
by TR.DOMFREQ"* in chat → agent writes `plot.py` → watcher runs it → live plot
appears → **zoom re-rasterizes**. Full loop in one window, verified at 1920×1080.
Plus: kill -9 worker → re-embed works; kill -9 sidecar → chat degrades to a banner
while plot/editor/files keep working.

**Order of work (risk-first):** R2b embed spike (§6.1) → app scaffold + header +
layout → chat pane → code pane → files basics → resilience polish. Do not build
panes before the embed works.

## 2. Read first (~25 min, in order)

| File | Extract |
|------|---------|
| `AGENTS.md` (repo root) | conventions, architecture rule, gotchas |
| `design.md` §3, §4, §5, §7, §9 | layout, ledger, worker/preview semantics, stack |
| `UX.md` (all of it; §1, §5, §6, §8, §9 are your spec) | pane behaviors, editor policy, degradation table |
| `PLAN.md` M3.4 + R2b | scope, exit criteria, sizing finding |
| `src/better_mad/hub/app.py` | the exact API you consume (§4 below is a catalog, not a substitute) |
| `HANDOFF-M3.3-sidecar.md` §4 | the chat contract the SPA renders |
| `~/pwb/web/src/main.ts` | **lift** the chat event switch (agent_start/text_delta/tool notes/stopped/reset/error → DOM ops); port to React state |
| `~/pwb/vite.config.ts` | **lift** the dev-proxy pattern (`/api` → hub, one origin in dev) |
| `scripts/m32_qa_demo.py` | the R2b-verified embed page: `server_session` tag + `#plot div {height:100% !important}` fix |
| `scripts/m33_chat_demo.sh` | manual QA of the chat sidecar through the hub |

Conventions: Python gates `uv run pytest` / `uv run ruff check .` / `uv run ty
check`; web gates `npm run typecheck` + `npm test` (in `web/`). All green before
committing; commits prefixed `M3.4:`.

## 3. Code map

```
web/                              ← YOU CREATE (Node ≥22.18 toolchain, same floor as sidecar)
├── package.json                  # react, react-dom, @codemirror/*, vite, vitest ^3.2
├── tsconfig.json                 # strict, noEmit (tsc = typecheck gate only; vite builds)
├── vite.config.ts                # build → dist/, dev proxy /api → hub (§8)
├── index.html
└── src/
    ├── main.tsx                  # mount
    ├── App.tsx                   # three-pane grid + drag-resize + persisted widths
    ├── api.ts                    # ALL endpoint types + fetch helpers (single source of truth)
    ├── sse.ts                    # useHubEvents() + useChatEvents() hooks (auto-reconnect)
    ├── embed.ts                  # bokeh autoload embed + re-embed on epoch change (§6.1)
    ├── state/
    │   ├── chatState.ts          # ClientEvent[] + history → transcript model (PURE — tested)
    │   └── runState.ts           # run/staleness derivation from hub events (PURE — tested)
    ├── header/Header.tsx
    ├── chat/ChatPane.tsx
    ├── plot/PlotPane.tsx
    ├── code/CodePane.tsx         # CodeMirror + save policy + external-change banner + Output drawer
    └── files/FilesPane.tsx       # basic list + import button (M3.5 enriches)
src/better_mad/
├── hub/app.py                    ← YOU EDIT (small): GET /api/chat/health proxy (§7.1)
├── worker/manager.py             ← YOU EDIT (small): extra websocket origins kwarg (§7.2)
└── cli.py                        ← YOU EDIT (small): --dev-origin flag (§7.2)
```

Dev loop: hub on 5006 (`uv run better-mad data/<file> --no-browser --dev-origin
http://localhost:5173`), `npm run dev` on 5173, open 5173. Prod: `npm run build` →
the hub serves `web/dist` automatically (it already probes cwd + repo root for
`web/dist/index.html` — M3.2 code, no change needed).

## 4. The hub API you consume (M3.2/M3.3, done — catalog)

All JSON under `/api`; no auth (localhost, single origin through the hub).

### REST

| Endpoint | In | Out |
|---|---|---|
| `GET /api/health` | — | `{ok, workspace, worker{state,port,epoch,restarts}, chat{state,port,model,restarts,reason?}}` |
| `GET /api/status` | — | `{auto_run, last_run, worker{…}, watcher{last_change_ts,pending}, chat{…}}` |
| `GET /api/files` | — | `{datasets: [{name, source, rows, columns: [{original, script, dtype, min, max, nan_pct}]}]}` |
| `POST /api/files/import` | multipart `file` + optional `settings` (JSON form field: `delimiter, decimal, sentinels, comment, float64_columns`) | dataset summary \| `400` on parse error |
| `GET /api/plot.py` | — | `{content}` (404 if missing) |
| `PUT /api/plot.py` | `{content}` | `{ok}` — atomic write; **unchanged content is a no-op** (no watcher churn) |
| `POST /api/run` | `{timeout_s?}` (body optional) | run result (below); worker down → error-shaped result, not 500 |
| `POST /api/autorun` | `{on: bool}` | `{auto_run}` |
| `GET /api/plot/embed` | — | `{server_url, session_id, epoch}` — `server_url` is `http://127.0.0.1:<port>/bm`, `null` while no worker |
| `GET /api/chat/health` | — | *(YOU ADD, §7.1)* proxied sidecar `/health`: `{ok, busy, model, session_file}` |

Run result shape (from `POST /api/run` and the `run_result` SSE event):
`{status: "ok"|"error"|"timeout", run_seconds, stdout_tail, stderr_tail, epoch}`.

### Hub SSE — `GET /api/events`

- On connect: `{"type": "hello", **status}` — treat as the authoritative full-state
  sync (also on every auto-reconnect).
- `{"type": "plot_changed"}` — `plot.py` changed on disk (any writer) and is stable.
- `{"type": "run_result", …}` — a run finished (manual, auto, or worker-restart replay).
- `{"type": "worker", "state": "starting"|"ready"|"restarting"|"down", "port", "epoch", "restarts"}`.
- `{"type": "chat", "state": "starting"|"ready"|"restarting"|"down", "port", "model", "restarts", "reason"?}`.
- Heartbeat comment `: heartbeat` every 20 s (not data — ignore comments).

### Chat — `GET /api/chat/events` (SSE) + REST (all proxied verbatim to the sidecar)

`retry: 2000` + `: ping` every 25 s; payloads = M3.3's ClientEvent union:
`{type:"text_delta",delta}` · `agent_start` · `agent_end` ·
`{type:"tool_execution_start",toolName}` / `{type:"tool_execution_end",toolName,isError}` ·
`stopped` · `reset` · `{type:"error",message}`.
REST: `POST /api/chat/send {text}` → `202 {started:true}` | `400` | `409`;
`POST /api/chat/stop` → `202 {stopping:true}` | `409` idle;
`POST /api/chat/new` → `202 {reset:true}` | `409` busy;
`GET /api/chat/history` → `{messages: [{role: "user"|"assistant", text}]}`.
Sidecar down → **all** `/api/chat/*` → `503 {error, reason}` (show the reason in
the chat banner).

## 5. Verified facts (checked against the installed stack; trust these)

- **Embed mechanics (bokeh 3.x)** — `bokeh.embed.server_session(url, session_id)`
  generates nothing magic; it is exactly (verified output):
  ```
  fetch(`${server_url}/autoload.js?bokeh-autoload-element=${elId}` +
        `&bokeh-app-path=/bm&bokeh-absolute-url=${server_url}`,
        {headers: {"Bokeh-Session-Id": session_id}})
  → blob → <script src=blobUrl> appended to document
  ```
  and the rendered content lands **inside the element with id `elId`**. Do this in
  `embed.ts` (no bokeh npm package, no Python involvement client-side).
- **CORS is a non-issue for the embed**: bokeh's `autoload.js` handler sends
  `Access-Control-Allow-Origin: *` / `Allow-Headers: *` — the cross-origin fetch
  (hub-origin page → worker port) works, in dev and prod (this is why the M3.2 QA
  page rendered). The **WebSocket** is the origin-checked part (below, trap T1).
- **Session identity**: the worker creates ONE shared session (`bm-main`) at boot;
  `embed.session_id` pins it — all tabs/views see the same document. A browser
  hitting the worker URL without the header gets a *new empty* session; always use
  the header.
- **R2b sizing (confirmed 2026-11-09)**: `autoload.js` replaces nothing — you host
  the target div — but the injected `div.bk-root` is `height:auto`; the worker's
  `stretch_both` measures the **parent**, so the figure collapses to a
  toolbar-height strip unless **every div in the chain is forced to full height**
  (`#plot-pane, #plot-pane div {height:100%}` — the `!important` M3.1/M3.2 pattern).
- **On worker restart the hub re-runs the last-good script** (M3.2 behavior):
  after a worker `ready` event with a new epoch, the new document already
  contains the last good plot — re-embed and it's there; you do not need to
  trigger a run.
- **No `run_started` event exists**: "running…" state = you saw `plot_changed`
  (auto-run on) or you clicked Run — clear it on the next `run_result`. `status
  .watcher.pending` tells you a change is still being coalesced.
- **The hub opens the browser on boot**; in dev run it with `--no-browser`.
- pwb's EventSource-through-Vite-proxy works (same proxy shape) — SSE streaming is
  fine through vite dev; do not add polyfills.

## 6. Design

### 6.1 `embed.ts` — the plot pane's core (build + verify FIRST)

```ts
export async function embedPlot(container: HTMLElement, info: EmbedInfo): Promise<void>
```

1. `elId = crypto.randomUUID()`; create `<div id=elId>`; `container.replaceChildren(div)`.
2. Fetch `autoload.js` with the `Bokeh-Session-Id` header (recipe §5), blob-inject.
3. Re-embed policy (subscribe to hub events): on `worker` event with
   `state==="ready" && epoch !== embeddedEpoch` → tear down (empty the container;
   revoke the blob URL; old BokehJS WS dies with its worker — do NOT try to
   gracefully dispose BokehJS, just drop the DOM) and re-embed. During
   `restarting`/`down`: keep the frozen plot up + a small banner.
4. Also reconcile on SSE `hello` (reconnect after hub sleep etc.): compare
   `hello.worker.epoch` to the embedded epoch.
5. Store the embedded epoch in a ref/state so PlotPane can show it in the
   staleness badge (nice-to-have, not required).

The R2b spike = this function + a bare container, before any other UI: hub with
real data → verify fill + zoom re-rasterizes at e.g. 1000×800. If stretch fails,
re-check the height chain before anything else (it has been the cause twice).

### 6.2 `sse.ts` — two hooks, same shape

`useHubEvents(onEvent)` and `useChatEvents(onEvent)`: EventSource, parse `data:`,
ignore comment lines (`:` prefix), pass typed events to a reducer; auto-reconnect
is the browser's (both streams set `retry:`); on `error`/reopen, refetch state:
hub → the next `hello` IS the refetch; chat → `GET /api/chat/history` (the sidecar
resumed its session file; events during the gap are gone — history is the truth)
+ `GET /api/chat/health` for `busy`.

### 6.3 `state/chatState.ts` — pure, tested (port of pwb main.ts's switch)

Transcript model: `{messages: Msg[], running: boolean}` where
`Msg = {kind: "user"|"assistant"|"error"|"stopped"|"note", text, toolNotes?: {name, isError}[]}`.
Events: `agent_start` → running=true, open live assistant bubble; `text_delta` →
append to live bubble; `tool_execution_start/end` → append `⚙ name…/done/failed`
note to the live bubble; `agent_end` → close live bubble, running=false (pwb
exact semantics — a provider retry then emits a fresh `agent_start`, which
re-opens; the brief re-enable is accepted pwb behavior); `stopped` →
"Stopped." marker, running=false; `reset` → clear transcript + "Fresh chat
started."; `error` → error bubble, running=false. History load → replace
transcript.

**Ordering rule**: load history first, then attach the SSE stream (a delta for a
turn already in history must append, not duplicate — key live bubbles by nothing,
just append; history refresh replaces wholesale on reconnect).

### 6.4 `state/runState.ts` — pure, tested

From hub events + REST state derive: `{phase: "idle"|"running", lastResult,
stale: boolean, lastGoodAt?: …}`. `plot_changed` + auto_run → running; `run_result`
→ phase=idle, stash result; `stale` = last result is error/timeout AND an ok
result existed before (badge "⚠ plot.py failed HH:MM — showing last good result").
Banner text = `stderr_tail` last line(s) + [Open output] → Output drawer.

### 6.5 Panes (all thin; state lives in the hooks/reducers)

- **ChatPane** (UX §8): transcript (code the pwb classes: user/assistant/tool-note/
  error/stopped bubbles), composer (disabled while `running`; Enter=send), Stop
  button (visible while running; POST stop), New chat button (POST new; disabled
  while running), model id in the pane header (from `chat.health`/`hello.chat`),
  first-run hint line (UX §2). Sidecar `down`/`starting` → banner with
  `chat.reason` at the top of the pane; input disabled; **pane never blocks the
  rest** (it's just a pane).
- **PlotPane**: `embed.ts` + the staleness badge/banner from runState + empty
  state ("Add files (right), then tell the agent what to plot." — UX §2).
- **CodePane** (UX §5): CodeMirror 6; GET `/api/plot.py` on mount; save = explicit
  Ctrl+S **and** debounced auto-save (~1 s idle / focus loss) → PUT; PUT is
  atomic + no-ops on unchanged content, so auto-save is cheap. **External-change
  policy** (the subtle part — make it a pure, tested function): on
  `plot_changed`: content on disk (GET) — if buffer clean or identical → reload
  silently (this also covers the echo of your own PUT: your save → watcher fires
  → GET returns what you just wrote → identical → no-op); if buffer dirty AND
  differs → banner `plot.py changed on disk (agent?). [Reload] [Overwrite]`
  (Overwrite = PUT the buffer). Output drawer (collapsed) under the editor:
  last `stdout_tail`/`stderr_tail`.
- **FilesPane** (basic only; M3.5 does settings/re-parse/close): cards from
  `GET /api/files` — name, rows×cols; expand → column list (display name +
  `script` name; the click-to-copy nicety is M3.5, keep it simple now).
  [＋ Add files] → `<input type=file>` → POST import (no settings field yet) →
  refetch files. Refresh the list on load and after your own imports (no hub
  event exists for dataset changes — none needed: only the user imports).
- **Header**: workspace path (health), Run button (POST run), Auto-run toggle
  (POST autorun; state from hello/status), status line (runState: ✓ ran X s ago /
  running… / ✗ one-line reason — UX §1: it must always answer "did my last change
  make it to the plot?"), model id. No browser tab title games needed.
- **Layout**: CSS grid `grid-template-columns: var(--w-chat) 1fr var(--w-files)`;
  two drag handles (mousedown → pointermove updates the CSS var → mouseup
  persists to localStorage, clamped 220–560 px chat / 240–520 px files; center
  gets the rest). Restore on mount. `100dvh` app height; nothing scrolls the
  page itself — panes scroll internally (transcript, file list, output drawer);
  the plot pane must NEVER scroll (height chain, trap T2).

## 7. Hub/CLI edits (small; each tested; no behavior change when unused)

1. **`GET /api/chat/health` proxy** (`hub/app.py`): the SPA needs `busy` at boot
   (pwb's load pattern) — the sidecar serves it, the hub just doesn't map it.
   Add to the proxy set: `/api/chat/health` → sidecar `GET /health` (same
   `_chat_passthrough` shape, path rewrite `"health"` → `/health` — note the
   sidecar route is `/health`, not `/api/chat/health`; one-line special case or
   extend the mapper). 503 when down, like the rest. Extend the reserved-path
   comment + one pytest in `test_chat_proxy.py`.
2. **Dev websocket origins** (`worker/manager.py` + `cli.py`): in dev the page
   origin is `http://localhost:5173`, bokeh's WS check only allows what the hub
   passed (`127.0.0.1:<hub-port>`) → 403 on the live doc (trap T1). Add
   `WorkerManager(..., extra_ws_origins: Sequence[str] = ())` merged into the
   spawn's `allow_websocket_origin` (the worker already normalizes URLs→netlocs),
   and CLI flag `--dev-origin URL` (repeatable) that forwards to it. Prod
   unchanged (default empty). One pytest each (manager spawns with the extra
   origin visible in the worker cmdline; CLI parses/forwards).

## 8. npm/TS setup

`web/package.json` (own install — **no root npm workspace**: keeps the sidecar's
npm-install-hint logic (trap T12 of M3.3) truthful and avoids churn; the M3.3
handoff anticipated a root file, this supersedes it):

```json
{ "name": "better-mad-web", "private": true, "type": "module",
  "engines": { "node": ">=22.18" },
  "scripts": { "dev": "vite", "build": "vite build",
               "typecheck": "tsc --noEmit", "test": "vitest --run" },
  "dependencies": {
    "react": "^19", "react-dom": "^19",
    "@codemirror/state": "^6", "@codemirror/view": "^6", "@codemirror/commands": "^6",
    "@codemirror/language": "^6", "@codemirror/lang-python": "^6",
    "@codemirror/theme-one-dark": "^6" },
  "devDependencies": {
    "typescript": "^5.6", "vite": "^7", "@vitejs/plugin-react": "^5",
    "vitest": "^3.2", "@testing-library/react": "^16", "@testing-library/jest-dom": "^6",
    "jsdom": "^25", "@types/react": "^19", "@types/react-dom": "^19" } }
```

`vite.config.ts` (pwb pattern; it sits in `web/`, so `npm run dev` there uses
it as root automatically — no `root` option needed):

```ts
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: { port: 5173, proxy: { "/api": { target: process.env.BETTER_MAD_HUB ?? "http://127.0.0.1:5006", changeOrigin: true } } },
});
```

`tsconfig.json`: `strict`, `noEmit`, `module/moduleResolution: "bundler"`,
`target: "es2022"`, `jsx: "react-jsx"`, `lib: ["es2022", "dom", "dom.iterable"]`,
`types: ["vite/client"]`, `verbatimModuleSyntax: true` (same discipline as the
sidecar: `import type` where a binding is only a type).

## 9. Traps

| # | Trap | Rule |
|---|------|------|
| T1 | Bokeh WS origin check (non-empty allowlist **replaces** defaults): dev page at `localhost:5173` → live-doc WebSocket 403s | The `--dev-origin` flag (§7.2); remember to pass it in dev or the plot pane silently shows nothing (check the browser console for the 403 — it does NOT surface in the embed promise) |
| T2 | `div.bk-root` is `height:auto`; stretch measures the parent → toolbar-height collapse | Force `height: 100%` on the plot container AND all its div children (`.plot-pane div {height:100%}` — `!important` if needed); the container's parents must have real heights too (the grid row is `1fr` of a `100dvh` app — fine) |
| T3 | Stale embeds stack: re-embed without teardown → two documents, doubled WS traffic, zombie rasters | `container.replaceChildren()` before every embed; revoke blob URLs; track the embedded epoch |
| T4 | Own editor save echoes back: PUT → watcher `plot_changed` → naive reload clobbers the cursor | The external-change policy compares content: identical → no-op (§6.5); never `setValue` when the disk content equals the buffer |
| T5 | `agent_end` fires per low-level run (`willRetry` may follow) — the input briefly re-enables between runs | Mirror pwb exactly: `agent_end` → running=false; a retry's `agent_start` re-disables; `stopped`/`error` are the calm end markers; the 409-on-send is the hard guard |
| T6 | Chat SSE reconnect after sidecar restart: events during the gap are lost forever (SSE has no replay) | On reconnect: refetch history + `chat/health` for busy; replace the transcript wholesale |
| T7 | EventSource handlers see stale React state (closures) | Feed events into a reducer via a ref or `useSyncExternalStore`; don't `setState` per delta with stale accumulators — batch `text_delta` with a rAF or the transcript re-renders per token (fine at GLM speeds, but don't fight it later) |
| T8 | PUT `/api/plot.py` is a full-file write — autosave with a half-typed buffer sends broken scripts | Debounce autosave (1 s idle + focus loss), keep Ctrl+S as the explicit path; the watcher's 0.5 s stability check is the backstop, not the fix |
| T9 | The hub serves `web/dist` from the repo-root probe — a stale `dist` shadows your dev debugging | In dev always use 5173 (vite), never the hub port; `rm -rf web/dist` when confused |
| T10 | vitest 4 SIGILL on this AVX1 CPU | Pin `^3.2` (as M3.3 did); do not "helpfully" upgrade |
| T11 | `hello.worker.epoch` vs embedded epoch drift after tab sleep | Reconcile on every `hello` (§6.1.4), not only on worker events |
| T12 | Files pane import with no hub event → stale list | Refetch after your own POST; there is no dataset-changed event by design (only the user imports) |

## 10. Tests

**Web (vitest, jsdom, no network — fetch/EventSource mocked at the hook seam):**
- `chatState`: event sequence → transcript (deltas accumulate into one bubble;
  tool notes attach; stopped vs error markers; reset clears; history replace;
  late delta after history doesn't duplicate).
- `runState`: plot_changed→running; ok→idle/✓; error after ok→stale; timeout;
  hello resync.
- External-change policy (pure fn): clean+same → silent reload; dirty+differs →
  banner state; own-write echo → no-op.
- `api.ts` types compile against fixture payloads (paste real captured JSONs as
  test fixtures — they double as contract tests).
- ChatPane render: busy disables composer; down-banner shows reason.

**Python (extend, don't rewrite):** `/api/chat/health` proxy test
(`test_chat_proxy.py`); `--dev-origin` + `extra_ws_origins` tests
(`test_worker.py`/`test_sidecar.py`-style, stub where needed). Full `uv run
pytest` stays green (nothing existing changes when the flag is unset).

**Manual (exit criteria — document in the commit message):** dev mode + prod
mode (`npm run build`, hub serves dist); the chat→plot loop of §1 on the real
121 MB sample at 1920×1080 with zoom re-rasterization; kill -9 worker →
re-embed + last-good plot back; kill -9 sidecar → chat banner, everything else
alive; editor: edit→save→auto-run; break script → staleness badge + banner →
fix; agent writes while editor dirty → Reload/Overwrite banner; auto-run off →
edit does nothing until Run.

## 11. Do / don't

DO: lift pwb's chat switch semantics exactly (it is live-proven; cite
`~/pwb/web/src/main.ts` in comments); keep every pane independently functional
(the shell must survive any one backend being down); make `api.ts` the only
place URLs/payload types are defined; run the R2b spike before building panes;
keep the CSS boring (variables + grid + flex only).

DON'T: add a router, state library (redux/zustand), UI kit (mui/antd), CSS
framework (tailwind), or a query library (react-query) — the app is two SSE
streams and a handful of REST calls; build the M3.5 files-panel features
(import settings UI, click-to-copy, close confirm, first-launch card, full
theme); touch `core/`, `sdk/`, the sidecar/, or worker run semantics; invent a
run-start event (none exists; derive running state client-side); use the
bokeh npm package (the autoload recipe is 10 lines).

## 12. Documentation updates (same commits, per convention)

- `AGENTS.md` commands: `cd web && npm install && npm run dev` (dev mode +
  `--dev-origin`), `npm run build` (prod); "Current status" → M3.4.
- `PLAN.md`: tick the M3.4 exit line (✔ + one-line summary); update R2b with
  the SPA's final sizing pattern.
- `design.md` §9 SPA bullet / `UX.md` §1: only if reality forced a deviation —
  record it in the decisions table, same commit.

## 13. Definition of done

- [ ] `web/` per §3/§6; R2b spike verified first (fill + re-raster in a plain
      CSS-sized container)
- [ ] Full chat→plot loop of §1 in one window, 1920×1080, zoom re-rasterizes
- [ ] Re-embed on worker restart; chat banner on sidecar death (both live-verified)
- [ ] Editor: save/autosave/echo-safe external-change policy + Output drawer
- [ ] Hub edits §7 (chat/health proxy, --dev-origin) with tests; full pytest green
- [ ] `npm run typecheck` + `npm test` + `uv run pytest` + `ruff` + `ty` green
- [ ] Prod build served by the hub verified (not just the dev server)
- [ ] Doc updates §12; PLAN.md M3.4 ticked

Report at the end: files changed, test counts, deviations from this document
(with justification), surprises.
