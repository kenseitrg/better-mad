# Session 2026-11-02 — Agent surface + frontend pivot

## Context

Returning to the project after M2. Two re-evaluations, both driven by the user's
dissatisfaction with the original v2 plan and by the `~/pwb` reference project
(Fastify + Vite web app embedding the pi SDK as chat agents).

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Left pane = **embedded pi chat panel** (Node sidecar, pwb chat contract), not a pty terminal | pwb proves the pattern end-to-end; drops the R1 pty spike; `~/.pi/agent` providers reused; filesystem coupling unchanged; external harnesses still work in a normal terminal |
| 2 | Chat wiring: **HTTP/SSE sidecar** (pwb contract) — UI side superseded by #3 same day | transport contract copied from pwb's `chat.ts`/`manager.ts`/`sessions.ts` |
| 3 | **Panel dropped**; shell = **web-native React/TS SPA (Vite) + FastAPI hub** | user metrics (performance → interactivity/robustness → look & feel): Panel capped look & feel, 15+ documented gotchas, 7 M2 fix-commits fighting the framework. Rewrite is cheap: app layer was 384 lines; the 1000+ line tested headless core survives untouched (no panel imports in `core/`/`sdk/`) |
| 4 | Plot rendering: **live bokeh-server document from day one** (not static BokehJS JSON first) | the pickle transport was the *cause* of the static-raster limitation; a persistent render worker executing `plot.py` in-process keeps DynamicMaps dynamic → true re-aggregation on zoom, live hover/streams; isolation preserved via worker process + watchdog + kill/restart; SDK public API unchanged (`show()` attaches instead of pickles) |

## De-risking done

- Smoke test (in-repo venv, bokeh 3.9.2): 500k-point datashaded DynamicMap
  (mean-z aggregation) renders in-process via `hv.renderer("bokeh").get_plot`,
  attaches to a bokeh document, carries `RangeXYCallback` + `PlotSizeCallback`
  → zoom re-rasterization works server-side. Embedding facts for M3.4:
  `server_session(model, url, session_id=…)` is the right embed (shared doc);
  `server_document` takes **no** session_id (fresh doc per tab) — do not use it.
- pwb reference read end-to-end: chat routes, PersonaManager (busy/stop/watchdog),
  session factory (`DefaultResourceLoader`, `SessionManager`, `ModelRuntime`),
  vanilla-JS chat client — the port plan is concrete.

## What survives / what dies

- **Survives**: core loader/columns/cache/dataset (M0), workspace + manifest (M1),
  SDK public API, AGENTS.md skill-file concept, sidecar design (#1/#2).
- **Dies**: `app/preview.py` + `app/server.py` (Panel layer), pickle transport,
  opts capture/re-apply, DynamicMap materialization, `pn.chat` plan (→ React chat),
  subprocess-per-run (→ worker exec-in-process).

## Open items for M3

- M3.1 worker: bokeh `Server` + `extra_patterns` control API (`/run`, `/health`,
  `/doc.json`); last-good retention via doc-root swap + hub-side snapshot restore;
  no auto re-run after failure-restart (no crash loops); recycle after failed runs
  (state-pollution mitigation).
- M3.2 hub: FastAPI REST/SSE + lifecycle for worker & sidecar; single-origin chat
  proxy; serves `web/dist`.
- M3.3 sidecar: pwb port minus personas.
- M3.4 SPA: three panes, `server_document` embed (re-embed on embed-epoch bump),
  CodeMirror 6 editor, React chat, error/staleness states.
- Verify: bokeh fit-sizing under a plain CSS container (R2b); shared session id
  across tabs; equal-aspect behavior under the new container (was broken under
  Panel fit-sizing).
