# Session 2026-11-09 — M3.2: hub backend (FastAPI)

## Context

Implementing `HANDOFF-M3.2-hub.md` on top of the M3.1 render worker (untouched).
New: `hub/app.py`, `hub/watcher.py`, `worker/manager.py`, CLI rewired to the
hub; the Panel `app/` layer is now unreachable dead code (left in place per
handoff W10, deleted in a later cleanup step).

## What landed

- **`worker/manager.py` — WorkerManager**: spawn via
  `asyncio.to_thread(start_worker, …, allow_websocket_origin=[hub_origin],
  stderr_log=<ws>/.data/worker.log)`; epoch starts at 1, +1 per (re)start;
  health poll (2 s interval, 2 s timeout, skipped while a run is in flight —
  trap W2; process death via `poll()` needs no HTTP); restart path re-executes
  the last-good script written to `<ws>/.data/last_good.py` (content stored at
  each `ok` run, read BEFORE issuing the run); crash-loop guard: at most
  `restart_limit` restarts inside `restart_window_s`, next death → state
  `down` + SSE event + graceful `/api/run` degradation; 409 → `status:"busy"`
  (W4); hub never dies with the worker (initial spawn failure → `down`).
- **`hub/app.py` — FastAPI transport** per handoff §4: health, files list,
  multipart import (python-multipart; settings JSON → `ParserSettings`),
  plot.py get/put (atomic `os.replace`; identical-content PUT is a no-op so
  Ctrl+S doesn't churn the watcher), run (degrades to `{"status":"error",
  "stderr_tail":"worker down"}`), autorun, status, plot/embed, SSE `/api/events`
  (hand-rolled `StreamingResponse`: hello snapshot, per-client queues, 20 s
  heartbeat comments, W8 headers). `/api/chat/*` reserved for M3.3.
  `web/dist` mounted last (can't shadow `/api`); placeholder page otherwise.
  `HubState.start/stop` idempotent and wired to the lifespan — tests call them
  directly (W6's preferred pattern).
- **`hub/watcher.py` — PlotWatcher**: poll `mtime_ns`+`size` every 0.3 s; fire
  after 500 ms stability (agents write in bursts); hub-side coalescing: run in
  flight → `pending` flag → one follow-up run of the LATEST script (never a
  queue).
- **`cli.py`**: `better-mad [files...]` → workspace → load files (errors print,
  never crash) → WorkerManager + app → `uvicorn.run` (blocks); browser opens
  from a daemon thread once `/api/health` is green; `--port 0` allocates a
  free port up front (documented TOCTOU).
- **Tests**: 8 hub tests (handoff §10 scenarios 1–7 incl. crash-loop with
  `restart_limit=2`), plus the CLI smoke test rewrite (see deviations).

## Deviations (with justification)

1. **SSE tests boot a real uvicorn on an ephemeral port** (lifespan off;
   fixture still owns lifecycle via `state.start/stop`). httpx's
   `ASGITransport` buffers the entire body (`ASGIResponseStream.__aiter__`
   yields `b"".join(body)` after the ASGI call completes), so an infinite SSE
   stream is unreadable through it — W8's `client.stream` + readline contract
   needs a real socket. REST tests stay on ASGITransport (W6 letter).
2. **Crash-loop guard semantics**: at most `restart_limit` restarts inside the
   window; the next death flips to `down` (handoff §5.5 says "more than limit
   restarts within window" — same protective behavior, one spawn cheaper).
   Test 6 kills 3× with `restart_limit=2` → down.
3. **`worker` restarting event carries the upcoming epoch** (bump before
   emit), so it matches the `ready` event that follows — the SPA can key
   re-embed decisions on one epoch value.
4. **PUT /api/plot.py no-ops on identical content** (no watcher churn /
   spurious rerun); contract output `{"ok": true}` unchanged.
5. **`tests/test_package.py` rewritten**: the old test asserted v1 delegation
   to `app.server.serve_app` (monkeypatched); the rewired CLI ignores that, so
   it booted the real hub and hung forever. Replaced with a stubbed-uvicorn
   plumbing test (workspace + manager + app wired, nothing binds/spawns).
6. **pytest-asyncio (dev dep)** with session loop scope so one real worker +
   hub serves the whole hub test module (~21 s for all 8).

## Surprises

- The full-suite hang that started this session's debugging: the old
  `test_main_delegates_to_serve_app` monkeypatching `serve_app` — deviation #5.
- httpx `ASGITransport` body buffering (deviation #1) — W6 and W8 as written
  are mutually unsatisfiable for SSE; resolved by splitting transports per
  concern.

## Verification

- `uv run pytest`: 112 passed (~75 s, was 104 + 8 hub).
- `uv run ruff check .` + `uv run ty check`: clean.
- Live smoke (`uv run better-mad tests/fixtures/sample_ws.txt --no-browser`):
  curl health → multipart import (hostile headers sanitized, NaN% shown) →
  PUT rasterize script → SSE `hello → plot_changed → run_result(ok, 2.9 s)` →
  `/api/plot/embed` URL family serves the bokeh app (`/bm` page, `/doc.json`
  has the Image glyph) → kill -9 worker → epoch 2 / restarts 1 / new port /
  last-good replayed (`runs: 1`, Image glyph back). Placeholder page at `/`.
- **Manual QA (2026-11-09, user-driven, PASSED)** via
  `scripts/m32_qa_demo.py` (committed): boots the real hub on the 121 MB /
  708k-row sample + a `/demo` viewer page embedding the live session with
  `bokeh.embed.server_session` (the M3.4 SPA mechanism). Verified in-browser:
  map fills the viewport, zoom re-rasterizes, watcher auto-run updates the
  live plot without reload, error runs keep last-good, kill -9 → restart +
  last-good replay, API surface incl. import-with-sentinels. One finding,
  fixed same day: **R2b confirmed** — the injected `div.bk-root` wrapper is
  height:auto and `stretch_both` measures the parent, collapsing the figure
  to a toolbar-height strip; fix = force the div chain to full height
  (M3.1 pattern, now in the demo page CSS and noted in PLAN R2b for M3.4).

## Next

M3.3 — Node chat sidecar (`sidecar/`, pwb chat contract, pi SDK
`AgentSession`, cwd = workspace; hub proxies `/api/chat/*`).
