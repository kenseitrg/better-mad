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

## QC of M3.1 (2026-11-02, same day)

Gates: 102 tests green, ruff/ty clean, PLAN exit line ticked. Verified live:
spawned worker → datashaded run ok (2.7–3.0 s) → /doc.json carries the Image
glyph → bokeh python client attaches to the shared session over the real
websocket protocol. Findings:

1. **Property patches don't re-rasterize — by design.** hv's `RangeXYCallback`
   listens to the `rangesupdate` *event* (sent by WheelZoom/Pan tools), not to
   range property changes. Any test that sets `x_range.start/end` client-side
   will see nothing — not a bug.
2. **bokeh 3.9.2 Python drops event payload values** (`RangesUpdate` has no
   `event_values()` override server-side), so python-side synthetic events
   arrive with x0/x1=None and crash hv's `_process_msg`. BokehJS (TypeScript)
   DOES serialize the values (`get event_values()` in the bundle) — the real
   browser→server path is unaffected. Synthetic-event tests must populate
   values at the handler, not rely on the wire round-trip.
3. **Panel is imported transitively** by hv's server callbacks
   (`panel.io.server`/`state` in the stack) — harmless but present in every
   worker traceback; don't be surprised by panel frames.
4. **RESOLVED 2026-11-03 (user-confirmed broken, then fixed):** zoom did not
   re-rasterize in the browser. Root cause: hv schedules its deferred stream
   processors (`Callback.process_on_event/process_on_change`) through panel's
   `async_execute`, which checks `getattr(func, "lock", False)`; without the
   marker they are scheduled **nolock**, bokeh's session skips the
   `with_document_locked` wrap, `_pending_writes` stays None, and the first
   model mutation inside `Stream.trigger` raises — swallowed by panel's
   exception handler. Fix: `_patch_hv_callback_locking()` in worker boot sets
   `.lock = True` on both processors (the same marker `panel.io.document
   .with_lock` sets for panel's own callbacks). Regression test:
   `test_zoom_event_re_rasterizes` fires `RangesUpdate` server-side (x+y
   values — bokeh's *python* serializer drops them; BokehJS sends them) and
   asserts the raster hash changes. Upstream hv main restructured this area
   (`_schedule_event` via `doc.add_next_tick_callback`) but does not set the
   marker either — revisit on the next hv upgrade.

Manual test artifact: `scripts/manual_worker_demo.py` (demo workspace + spawned
worker + `viewer.html` embed via `server_session` — the exact mechanism the
SPA will use).
