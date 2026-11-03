# HANDOFF — M3.2: hub backend (FastAPI)

You are implementing milestone step **M3.2**: the **hub** — the FastAPI process
that owns the workspace, spawns and supervises the render worker, serves the
REST/SSE API the SPA will consume (M3.4), and rewires the CLI. This file is
your single source of instructions. Where it conflicts with your instincts,
this file wins.

M3.1 (render worker) is **done and browser-verified** — treat it as a stable
dependency; do not modify it (one exception: `worker/manager.py` is yours to add).

## 1. Mission

Create `src/better_mad/hub/` + `src/better_mad/worker/manager.py`, add the
process orchestration the v2 shell runs on:

```
SPA (M3.4, later)  ── /api/* REST+SSE ──►  HUB (you)  ── control API ──►  worker (done)
                                           │ spawns + supervises both
                                           └── /api/chat/* ──►  sidecar (M3.3, later)
```

- **WorkerManager** (`worker/manager.py`): spawn / health-poll / restart-with-
  last-good / embed epoch / crash-loop guard.
- **Hub app** (`hub/app.py` + `hub/watcher.py`): REST + SSE endpoints per §4,
  file import, `plot.py` get/put, run trigger, auto-run watcher.
- **CLI rewire** (`cli.py`): `better-mad [files...]` starts the hub (which
  spawns the worker) and opens the browser. The old Panel `app/` becomes
  unreachable dead code — **leave it and its tests alone** (deleted in a later
  cleanup step).

**Exit criteria (from PLAN.md M3.2):** pytest drives the hub end-to-end
headlessly (import → run → SSE events → embed info); kill -9 the worker →
restart → last-good plot re-established → event on SSE.

## 2. Read first (~15 min, in order)

| File | Extract |
|------|---------|
| `AGENTS.md` | conventions, architecture rule, gotchas |
| `design.md` §9 (+ §9.1) | process layout, hub responsibilities, endpoints |
| `design.md` §5 | worker semantics (last-good, no-auto-rerun) |
| `PLAN.md` M3.2 | scope + exit criteria |
| `HANDOFF-M3.1-worker.md` §4.2 | the worker control API you will drive |
| `src/better_mad/worker/server.py`, `lifecycle.py` | `start_worker`, `WorkerHandle`, `WORKER_READY` |
| `src/better_mad/core/workspace.py`, `dataset.py` | `Workspace`, `Dataset`, `ParserSettings` |

Conventions: `uv`; gates `uv run pytest` / `uv run ruff check .` /
`uv run ty check` all green before committing; small commits prefixed `M3.2:`.

## 3. Code map

```
src/better_mad/
├── core/            # UI-free, frozen: loading/workspace/dataset/cache — DO NOT TOUCH
├── sdk/             # bm.data/show + _ATTACH transport — DO NOT TOUCH
├── worker/          # DONE (M3.1): server.py, control.py, executor.py, lifecycle.py,
│                    #   __main__.py. You ADD manager.py only.
├── hub/             # ← YOU CREATE: app.py, watcher.py
├── app/             # superseded Panel app — leave untouched (tests still green)
└── cli.py           # ← YOU REWIRE to the hub
```

Key APIs you consume (all verified):
- `start_worker(data_dir, *, port=0, session_id="bm-main", allow_websocket_origin=None, stderr_log=None) -> WorkerHandle`
  — blocks until `WORKER_READY` (up to 120 s; typically 3–5 s).
- `WorkerHandle`: `.port`, `.base_url`, `.session_id`, `.process` (Popen | None),
  `.run(script_path, timeout_s=None) -> dict` (blocking HTTP, timeout =
  run-timeout + 15 s headroom), `.health() -> dict`, `.doc_json() -> str`,
  `.stop()`.
- Worker control API: `POST /run {"script": path, "timeout_s"?: float}` →
  `{"status": "ok"|"error"|"timeout", "run_seconds", "stdout_tail", "stderr_tail"}`
  (409 if a run is in flight); `GET /health`; `GET /doc.json`.
- `Workspace`: `.path`, `.script_path`, `.data_dir`, `.datasets` (dict name→Dataset),
  `.add_file(path, settings=None) -> Dataset`, `.refresh_manifest()`.
- `Dataset`: `.df`, `.columns`, `.display_names` (sanitized→original), `.n_rows`.
- `better_mad.worker.executor.HANG_EXIT_CODE == 69` — worker exit code for a
  watchdog (timeout) kill: a crash to restart from, not a bug.

## 4. Hub API contract (all under `/api`, localhost, no auth)

| Endpoint | In | Out |
|---|---|---|
| `GET /api/health` | — | `{"ok": true, "workspace": "<path>", "worker": {"state", "port", "epoch", "restarts"}}` |
| `POST /api/files/import` | multipart: `file` (upload), optional `settings` (JSON of ParserSettings fields) | dataset summary (same shape as one `/api/files` entry) |
| `GET /api/files` | — | `{"datasets": [{name, source, rows, columns: [{original, script, dtype, min, max, nan_pct}]}]}` |
| `GET /api/plot.py` | — | `{"content": str}` |
| `PUT /api/plot.py` | `{"content": str}` | `{"ok": true}` — write to disk; the watcher takes it from here (auto-run applies) |
| `POST /api/run` | `{"timeout_s"?: float}` | the worker `/run` result verbatim (+ `"epoch"`) |
| `POST /api/autorun` | `{"on": bool}` | `{"auto_run": bool}` |
| `GET /api/status` | — | `{"auto_run", "last_run": <result or null>, "worker": {...}, "watcher": {"last_change_ts", "pending"}}` |
| `GET /api/plot/embed` | — | `{"server_url": "http://127.0.0.1:<worker-port>/bm", "session_id", "epoch"}` |
| `GET /api/events` | — | SSE stream, see below |

SSE events (each `data:` line is JSON; heartbeat comment every ~20 s; send a
`hello` with the full `/api/status` snapshot on connect):

- `{"type": "hello", <status snapshot>}`
- `{"type": "run_result", <worker result fields>, "epoch"}`
- `{"type": "plot_changed"}` — watcher fired (before any run)
- `{"type": "worker", "state": "starting"|"ready"|"restarting"|"down", "port", "epoch", "restarts"}`

`/api/chat/*` is **reserved but NOT implemented** in M3.2 (sidecar lands in
M3.3). Just note the reserved path in a comment.

Serve `web/dist/` at `/` **if it exists** (it won't until M3.4); otherwise a
minimal placeholder page at `/` stating the API is up. Static mounting must not
shadow `/api`.

## 5. WorkerManager design (`worker/manager.py`)

```python
class WorkerManager:
    def __init__(self, data_dir: Path, hub_origin: str, *,
                 health_interval_s: float = 2.0,
                 restart_limit: int = 3, restart_window_s: float = 60.0): ...
    async def start(self) -> None            # spawn (via asyncio.to_thread(start_worker, ...))
    async def run(self, timeout_s: float | None = None) -> dict | None   # None if worker down
    def embed_info(self) -> dict              # {"server_url", "session_id", "epoch"}
    async def stop(self) -> None
    def subscribe(self) -> Callable[[], None] # or expose an asyncio.Queue/event hook the hub polls
```

State machine and rules (design §5 — implement exactly):

1. **Spawn**: `start_worker(data_dir, allow_websocket_origin=[hub_origin], stderr_log=<workspace>/.data/worker.log)`
   — the origin is REQUIRED (bokeh: a non-empty allowlist REPLACES the default
   self-origin entries). The hub port must therefore be known before spawn:
   bind the uvicorn socket first (or allocate a free port up front and pass it
   to uvicorn) — decide and document in a docstring.
2. **Epoch**: integer, starts at 1, +1 on every worker (re)start. The SPA
   re-embeds when it changes. `embed_info()` reflects the current worker.
3. **Health poll** (asyncio task, `health_interval_s`): GET `/health` with
   ~2 s timeout via `httpx.AsyncClient`. **Do not restart while a run is in
   flight** — the worker serves `/run` and `/health` on the same IOLoop, so
   health checks legitimately time out during a run (trap W2). Track
   run-in-flight hub-side; only restart when the process is dead
   (`handle.process.poll()`), or health fails while idle.
4. **Restart semantics**: on death → spawn a new worker (epoch+1) → re-execute
   the **last-good script**: the hub stores the `plot.py` content it had when
   the last `ok` run completed (read content BEFORE issuing the run; store on
   ok). On restart, write it to `<workspace>/.data/last_good.py` (app-owned
   area) and `/run` that path. **Never** re-run the script that killed the
   worker (no crash loops). If even the last-good re-run errors → worker stays
   up, empty plot, status shows it (that's a degraded-but-alive state).
5. **Crash-loop guard**: more than `restart_limit` restarts within
   `restart_window_s` → state `down`, emit event, stop trying. `/api/run`
   returns `{"status": "error", "stderr_tail": "worker down"}`-style result;
   everything else keeps working (hub never dies with the worker).
6. **Serialization**: one run at a time — `asyncio.Lock`; if the worker 409s
   anyway (race), surface it as `{"status": "busy"}` or wait — your call, tests
   only require no exception escapes.
7. Last-good also covers the M3.1 quirk: `/doc.json` snapshots are structural
   only (not restorable) — that's WHY the re-run approach exists.

## 6. Watcher (`hub/watcher.py`)

- Poll `plot.py` `mtime`+`size` every ~0.3 s (watchdog optional — polling is
  fine and dependency-free).
- Fire when the file **stays unchanged for 500 ms** after a change (stability
  check — agents write in bursts, R3 in PLAN). Coalesce: if a run is already
  in flight when the watcher fires, remember "pending" and run once after the
  current run completes (run the LATEST script, not a queue).
- On fire: emit `plot_changed`; if `auto_run` (default **on**, togglable) →
  `manager.run()`.
- `PUT /api/plot.py` writes via this same path (write → watcher picks it up);
  do not run directly from the PUT handler.

## 7. CLI (`cli.py`)

Keep the arg surface: `files*`, `--port` (default 5006), `--no-browser`,
`--workspace`, `--version`. New flow: create/load workspace → `add_file` for
each CLI file → build hub app (with WorkerManager) → start uvicorn
programmatically (`uvicorn.Config`/`Server`; blocks) → open browser once
`/api/health` is green (startup event or a small timer thread). Load errors on
CLI files: print and continue (never crash the hub).

## 8. Dependencies

`uv add fastapi uvicorn httpx python-multipart`. SSE: hand-rolled
`StreamingResponse` with an async generator (no sse-starlette — keep deps lean;
pwb's hand-rolled SSE is the reference pattern). No other new deps without a
documented reason.

## 9. Traps (verified — don't rediscover them)

| # | Trap | Rule |
|---|------|------|
| W1 | `start_worker` and `WorkerHandle.run` are **blocking** (up to 120 s / run-timeout+15 s) | Call via `asyncio.to_thread` from async code; never on the event loop |
| W2 | Worker serves `/run`, `/health`, `/doc.json` on ONE IOLoop — a long run blocks health | Never restart based on health failure during a run (§5.3); tests that kill -9 must do it while idle or mid-run — both work since process death is detectable via `poll()` |
| W3 | `allow_websocket_origin` **replaces** bokeh's default allowlist | Always pass the hub origin `127.0.0.1:<hub-port>`; hub port must be fixed before worker spawn |
| W4 | Worker `/run` returns 409 while busy | Serialize hub-side (`asyncio.Lock`); treat 409 as "in flight", not an error to retry blindly |
| W5 | Exit code **69** = watchdog timeout kill | Crash → normal restart path (last-good re-run) |
| W6 | uvicorn + tests | Tests use `httpx.AsyncClient(transport=ASGITransport(app))` — never boot uvicorn in pytest; `LifespanManager` from asgi-lifespan IF needed for startup events (prefer making startup idempotent callable so tests can call it directly instead) |
| W7 | FastAPI file uploads need `python-multipart` | It's in §8; missing it produces a confusing `Form data requires "python-multipart"` at first request, not at import |
| W8 | SSE through anything | Set `Cache-Control: no-cache`, `X-Accel-Buffering: no`; yield heartbeat comments; test with `client.stream("GET", "/api/events")` + readline loop and a hard timeout |
| W9 | Upload temp files | `ws.add_file(temp_path)` records the temp path as "source" — acceptable (manifest shows it); don't invent persistent storage in M3.2 |
| W10 | Old `app/` Panel layer | Untouched; its tests keep passing; CLI no longer imports it |

## 10. Tests (`tests/test_hub.py`, all headless)

Reuse fixtures/patterns from `test_worker.py` (temp workspace, `sample_ws.txt`).
The real worker spawns in ~3–5 s — a session-scoped worker+hub fixture keeps the
suite fast. Every wait needs a hard timeout.

1. `import` fixture file via multipart → `/api/files` lists it with sanitized +
   original names and stats.
2. `GET/PUT /api/plot.py` round-trip; PUT (auto-run on, short debounce) →
   `plot_changed` then `run_result` events arrive on `/api/events` (streaming
   read with deadline).
3. `POST /api/run` with a good script → `status: "ok"`; `/api/plot/embed`
   `server_url` port matches worker `/health`; `doc.json` contains the glyph.
4. Broken script → `status: "error"`, traceback in `stderr_tail`; manager's
   last-good is still the previous ok script.
5. **kill -9 the worker** → restart observed: `epoch` +1, `worker` event on
   SSE, last-good re-executed (worker `runs` increments; `doc.json` contains
   the last-good glyph again).
6. Crash-loop: kill 3× within the window → worker state `down`, `/api/run`
   degrades gracefully (no exception), hub still serves `/api/health`.
7. Auto-run off → PUT doesn't trigger a run (no `run_result` within a window).

## 11. Do / don't

DO: keep the hub a thin transport (JSON views of core state live in the hub,
not core); make every endpoint total (never raise on worker-down); give the
WorkerManager a synchronous, pytest-friendly core (async shell is fine — tests
can `asyncio.run` it); log worker stderr to `<workspace>/.data/worker.log` for
debuggability; tick the M3.2 exit line in PLAN.md when done.

DON'T: touch `core/`, `sdk/`, or worker internals beyond adding `manager.py`;
implement chat proxying (reserved path only); build any SPA/static beyond the
placeholder; delete `app/`; add auth/CORS (localhost single-origin by design);
invent new workspace files outside `.data/` (the only new one is
`.data/last_good.py` + `.data/worker.log`).

## 12. Definition of done

- [ ] `hub/app.py`, `hub/watcher.py`, `worker/manager.py`, `cli.py` rewired
- [ ] Endpoints per §4 contract; SSE events per §4; reserved `/api/chat` noted
- [ ] Tests §10 (1–6 required, 7 required; crash-loop test may use short
      restart window) — whole suite green (`uv run pytest`)
- [ ] `uv run ruff check .` + `uv run ty check` clean
- [ ] Manual smoke (document in PR/commit message): `uv run better-mad
      tests/fixtures/sample_ws.txt --no-browser` → curl `/api/health`, import
      via curl multipart, `PUT /api/plot.py` with a rasterize script → SSE
      shows run_result → open `/api/plot/embed`'s URL family works (worker
      embed verified in M3.1 demo)
- [ ] PLAN.md M3.2 exit line ticked with a one-line summary

Report at the end: files changed, test count, deviations from this document
(with justification), surprises.
