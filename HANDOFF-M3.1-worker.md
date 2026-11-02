# HANDOFF — M3.1: render worker (live bokeh-server document)

You are implementing milestone step **M3.1** of better-mad: the **render worker**.
This file is your single source of instructions. It contains verified API facts,
working code snippets, traps, scope limits, and a definition of done. Follow it
literally; where it conflicts with your instincts, this file wins.

## 1. Mission

Build `src/better_mad/worker/` — an isolated Python process that:

1. runs a programmatic **bokeh server** with **one shared session document** at
   app path `/bm`,
2. exposes a minimal **control API** on the same port
   (`POST /run`, `GET /health`, `GET /doc.json`),
3. **executes `plot.py` in-process** via `better_mad.sdk` on demand, attaching the
   produced figure to the shared document (DynamicMaps stay live: zoom
   re-rasterizes),
4. keeps the **last good roots** on failure, supports **watchdog kill on hang**,
   and serves a **document snapshot** for hub-side retention.

Everything is headless and pytest-covered. No hub, no SPA, no chat sidecar —
those are M3.2+.

**Exit criteria (copy of PLAN.md M3.1):** pytest spawns worker → run good script
→ `doc.json` contains the figure → run broken script → error result + last good
roots intact → timeout → worker killed.

## 2. Read first (in order, ~15 min)

| File | What to extract |
|------|-----------------|
| `AGENTS.md` (repo root) | conventions (uv, ruff, ty, pytest, commit style), gotchas |
| `design.md` §5 | worker design: the contract you are implementing |
| `design.md` §9 | process layout: where the worker sits (hub/worker/SPA/sidecar) |
| `PLAN.md` M3.1 + M3.2 | your step + what the hub will later need from you |
| `sessions/2026-11-02-frontend-pivot-spa-live-doc.md` | why this architecture |

Conventions: Python ≥3.11 via `uv`; `uv run pytest`, `uv run ruff check .`,
`uv run ty check` must all pass before you commit. Small commits, message prefix
`M3.1:`. Do not modify `design.md`/`UX.md`/`PLAN.md` except to tick the M3.1
exit line when done.

## 3. Current code map (what exists)

```
src/better_mad/
├── core/                 # UI-free, pytest-covered — DO NOT BREAK
│   ├── loading.py        # parser (whitespace/csv/tab, sentinels)  [frozen]
│   ├── columns.py        # hostile-header sanitization            [frozen]
│   ├── cache.py          # parquet cache (~/.cache/better-mad)    [frozen]
│   ├── dataset.py        # Dataset dataclass                      [frozen]
│   ├── workspace.py      # Workspace: .data/ parquet snapshots, datasets.md,
│   │                     #   AGENTS.md + plot.py templates        [frozen]
│   └── runner.py         # OLD subprocess runner (pickle transport) — superseded,
│                         #   still used by app/ + tests. Leave it alone in M3.1.
├── sdk/                  # bm.data() / bm.show() — public API frozen, you ADD a
│                         #   transport mode (see §6)
├── app/                  # OLD Panel app (superseded). Do not touch. Deleted in
│                         #   a later step, not by you.
├── worker/               # ← YOU CREATE THIS
└── cli.py                # CLI entry — you do NOT wire it up yet (hub is M3.2)
```

SDK usage contract for scripts (unchanged, for reference):

```python
import better_mad.sdk as bm
df = bm.data("file_a")          # reads <workspace>/.data/file_a.parquet
bm.show(fig)                    # currently: pickle to $BETTER_MAD_OUTPUT
```

`bm.data()` reads from env `BETTER_MAD_DATA_DIR`. Keep that mechanism; the worker
sets env vars before exec.

## 4. Target design

### 4.1 Module layout

```
src/better_mad/worker/
├── __init__.py
├── server.py      # boot_worker() -> WorkerHandle: builds bokeh Server,
│                  #   shared session, control handlers, io_loop lifecycle
├── control.py     # tornado handlers: RunHandler, HealthHandler, DocJsonHandler
├── executor.py    # run_script(): exec plot.py in fresh namespace, watchdog,
│                  #   result dataclass (ok/error/timeout, stdout/stderr, time)
└── lifecycle.py   # (thin) run-blocking helper for tests: start, run, stop
```

A `WorkerHandle` exposes: `port`, `session_id`, `base_url`, `run(script_path)`
(or tests POST to the HTTP API directly), `doc_json()`, `stop()`.

### 4.2 Control API contract (localhost only; consumed by the hub in M3.2)

- `POST /run` — body: `{"script": "<abs path to plot.py>"}` → executes the script.
  Response 200: `{"status": "ok"|"error"|"timeout", "run_seconds": float,
  "stdout_tail": str, "stderr_tail": str}` (tails = last ~2000 chars).
  On `ok` the document roots were replaced atomically. On `error`/`timeout` the
  document keeps its previous roots (last good).
  Busy policy: 409 `{"error": "run in progress"}` if a run is active.
- `GET /health` — 200 `{"ok": true, "session_id": "...", "runs": N,
  "last_status": "ok"|"error"|"timeout"|null, "started": "<iso>"}`.
- `GET /doc.json` — 200, the shared document snapshot as JSON text
  (`serialize_json(doc.to_json())`), or 200 `{"empty": true}` when no figure
  was ever shown successfully.

### 4.3 Run semantics (state machine)

```
idle ──POST /run──► running ──show()≥1 + no exception──► swap roots ──► idle(ok)
                       │
                       ├── exception anywhere ─────────────► idle(error), doc UNTOUCHED
                       └── watchdog timeout (default 60 s) ─► self-terminate process
```

- **Atomic swap**: render the new figure into bokeh models FIRST; only after a
  successful render, replace the document roots (clear + add) under
  `with_document_locked`. A script that fails must leave the doc exactly as it
  was.
- **No `show()` called** → result `status:"ok"`, but document gets NO roots
  swap... actually: treat as its own outcome — return `"error"` with
  `stderr_tail` = "script ran but never called bm.show()" and do not touch the
  document. (Hub/SPA show the placeholder case; M2 semantics.)
- **Watchdog**: a run exceeding the timeout must kill the worker PROCESS
  (`os._exit` from a watchdog thread is acceptable; the hub restarts it). Do not
  try to cleanly interrupt arbitrary Python. Document this in a docstring.
- **Recycle-after-failure**: after a run that raised, prefer restarting... NO —
  that is the hub's call in M3.2. In M3.1 the worker itself stays up after an
  error run (tests verify doc intact). Only timeouts/fatals kill the process.

### 4.4 Last-good retention across restarts (hub-side, M3.2)

Not your job to implement — your job is to expose `GET /doc.json`. For context:
the hub snapshots it after each successful run and restores on worker restart
(fallback if restore fidelity is poor: hub re-executes its stored last-good
script content — so keep `/run` accepting an arbitrary script path).

## 5. Verified bokeh facts (bokeh 3.9.2 in this venv) — use exactly these

Everything below was executed successfully in this repo's venv (2026-11-02).
Copy these patterns; do not improvise alternatives without testing.

### 5.1 Boot the server (port 0 = ephemeral; read back `srv.port`)

```python
from bokeh.server.server import Server

srv = Server({"/bm": lambda doc: None},          # modify_doc for NEW sessions
             port=0, address="127.0.0.1",
             unused_session_lifetime=24*3600*1000)  # ms; keep shared session
                                                    # alive with no browser
app_ctx = srv._tornado._applications["/bm"]       # ApplicationContext
```

`extra_patterns=[(route, HandlerClass), ...]` is also a valid `Server(...)` kwarg
(same as bokeh CLI) for the control API — verified accepted.

### 5.2 The shared session (create is ASYNC; retrieve is sync)

```python
sess = await app_ctx.create_session_if_needed("fixed-session-id")  # idempotent
sess2 = srv.get_session("/bm", "fixed-session-id")                 # is sess — 2 args!
# get_session raises ProtocolError("No such session …") if absent — create first.
```

### 5.3 Mutating the shared document — MUST hold the session lock

```python
def swap(doc):
    for r in list(doc.roots):
        doc.remove_root(r)
    doc.add_root(new_figure)

await sess.with_document_locked(lambda: swap(sess.document))
```

Direct `sess.document.add_root(...)` outside the lock raises
`RuntimeError: _pending_writes should be non-None when we have a document lock…`.
The lock is also what pushes changes to connected clients.

### 5.4 Making `curdoc()` target the shared doc during script exec

```python
from bokeh.io.state import curstate
from bokeh.io import curdoc
curstate().document = sess.document     # inside the run; curdoc() now hits it
...
curstate().document = None              # restore when the run ends
```

(Semi-public but stable across 3.x; there is no `bokeh.io.set_curdoc`.)

### 5.5 Attaching HoloViews figures (the `show()` body)

```python
import holoviews as hv
renderer = hv.renderer("bokeh")
plot = renderer.get_plot(hv_obj)      # eager: invalid opts raise HERE (good)
fig = plot.state                      # bokeh figure — add_root(fig)
# KEEP A REFERENCE to `plot` (worker attribute) — DynamicMap callbacks
# (RangeXY/PlotSize → re-rasterize on zoom) live on it; GC'ing it kills zoom.
```

Verified with a 500k-point `rasterize()`-d DynamicMap: renders in-process,
attaches, carries `RangeXYCallback` + `PlotSizeCallback`.

### 5.6 Snapshot JSON

```python
from bokeh.core.json_encoder import serialize_json
payload = serialize_json(sess.document.to_json())   # to_json() returns Serialized,
                                                    # NOT a dict — don't subscript it
# round-trip (hub-side later): Document.from_json_string(payload)
```

(Plain `to_json_string()` does NOT exist on Document in 3.9.)

### 5.7 Embedding (for M3.4; noted so you don't ship the wrong thing)

The SPA will use `bokeh.embed.server_session(model, url, session_id=...)`
(script tag). **`server_document` does NOT accept a `session_id`** — it would
give every browser tab its own fresh document. Nothing for you to implement here.

## 6. SDK integration: add an attach transport, keep the pickle one

`src/better_mad/sdk/__init__.py` — `show()` currently pickles to
`$BETTER_MAD_OUTPUT`. Change it minimally:

```python
# module level
_ATTACH: Callable[[object], None] | None = None   # set by the worker

def show(obj: object) -> None:
    if _ATTACH is not None:
        _ATTACH(obj)          # worker: render + collect; swap happens after success
        return
    ... existing pickle path unchanged ...
```

The worker sets `better_mad.sdk._ATTACH = collect_figure` before exec and clears
it after. The worker's `collect_figure` does NOT touch the document — it renders
(`renderer.get_plot`) into a pending slot; the executor swaps the doc only after
the script finishes without exception. Public API (`data`, `list_data`, `show`)
signatures unchanged. Do not delete the pickle path, `runner.py`, or `app/` —
old tests must keep passing.

Env for the exec: set `BETTER_MAD_DATA_DIR=<workspace>/.data` (bm.data reads it).
`BETTER_MAD_OUTPUT` is not needed in attach mode — make sure `show()` doesn't
demand it when `_ATTACH` is set.

## 7. Tests (tests/test_worker.py) — required scenarios

Reuse fixtures/patterns from `tests/test_runner.py` (it writes temp workspaces
with `.data/` parquet + `plot.py`). Spawn the worker per test (or
session-scoped with per-test script files) on an ephemeral port.

1. **good run**: script shows an `hv.Points` → `/run` returns ok → `/doc.json`
   non-empty → roots replaced (run a second good script, assert content changed).
2. **dynamic map stays dynamic**: script shows `rasterize(points)` → after run,
   the kept plot object has callbacks (assert worker kept a reference /
   `len(plot.callbacks) > 0`), doc has a root.
3. **error run keeps last good**: run good script, then one that raises →
   status error, `stderr_tail` has the traceback → doc.json identical to before.
4. **no show()**: script runs fine but never shows → status error with the
   "never called bm.show()" message, doc untouched.
5. **timeout**: script `while True: pass` with timeout=2 s (make timeout
   configurable via `/run` body `{"timeout_s": 2}` for tests) → worker process
   exits (assert `handle.process.wait(timeout=5)` — non-zero or -9).
6. **busy 409**: (only if you implement runs async-within-loop; simplest is a
   queue with in-flight flag) — if you make `/run` synchronous-block, document
   why 409 can't happen and skip this test.

Guard EVERY network/file wait in tests with explicit timeouts (`httpx`
`timeout=…`, `process.wait(timeout=…)`) — see trap T1.

Also update `.gitignore` if tests write temp dirs inside the repo (prefer
`tmp_path` fixtures).

## 8. Traps (each one verified the hard way today)

| # | Trap | Symptom | Rule |
|---|------|---------|------|
| T1 | `Server(...)` **binds its socket immediately**; nothing is served until `srv.io_loop.start()` | HTTP client connects (backlog accepts) then blocks **forever** — hung test | Never probe a bokeh server before the loop runs; always set client timeouts; prefer driving the worker in-process for tests |
| T2 | Mutating a live session doc without the session lock | `RuntimeError: _pending_writes should be non-None…` | All root add/remove/curdoc work happens inside `await sess.with_document_locked(fn)` |
| T3 | `doc.to_json()` returns `Serialized`, not dict | `TypeError: 'Serialized' object is not subscriptable` | Snapshot = `serialize_json(doc.to_json())` |
| T4 | `server.get_session(id)` (1 arg) | `TypeError: missing 'session_id'` | It's `get_session(app_path, session_id)`; creation is `await app_ctx.create_session_if_needed(id)` on `srv._tornado._applications["/bm"]` |
| T5 | Default `unused_session_lifetime` is short | Shared session garbage-collected when no browser is connected → zoom callbacks die | Pass `unused_session_lifetime` (ms) explicitly |
| T6 | Dropping the hv plot object reference | Plot renders but zoom no longer re-rasterizes (callbacks GC'd) | Worker holds `self._last_plot` |
| T7 | `IOLoop.make_current()` | DeprecationWarning | Use `srv.io_loop` (created by Server); don't construct loops yourself |
| T8 | Runs execute on the bokeh IOLoop thread | A long run blocks health/doc.json for its duration | Accepted by design (~seconds); just don't add background threads that mutate the doc |

HoloViews gotchas that still apply (from AGENTS.md): `rasterize(points,
column=…)` silently aggregates the **first vdim**; any process applying opts must
call `hv.extension("bokeh")` first (the SDK already does this at import — keep
it).

## 9. Do / don't

DO:
- `uv add httpx` if you use it in the worker (hub will need it anyway); stdlib
  `urllib` in tests is fine too.
- Keep `worker/` free of any UI/framework imports beyond bokeh/holoviews/tornado.
- Make timeout default 60 s, overridable per `/run` (`timeout_s`).
- Capture stdout/stderr per run (`contextlib.redirect_stdout/stderr` into
  StringIO — exec runs in-process now, this is all you need).

DON'T:
- No hub, SPA, sidecar, CLI wiring, or `app/`/`runner.py` deletion (later steps).
- No changes to `core/` (except: if `workspace.py` needs a tiny public helper,
  stop and reconsider — you shouldn't need one; read `.data/` directly).
- No new abstractions "for later" (no plugin systems, no multi-document
  support, no auth on the control API — localhost only).
- Do not pin/dump figures to disk in attach mode (the pickle path stays for the
  legacy runner only).

## 10. Definition of done

- [ ] `src/better_mad/worker/` implements §4; control API matches §4.2 exactly.
- [ ] SDK attach mode per §6; pickle path untouched; all existing tests green.
- [ ] `tests/test_worker.py` scenarios 1–5 (6 optional) green, each with hard
      timeouts; whole suite: `uv run pytest` green.
- [ ] `uv run ruff check .` and `uv run ty check` clean.
- [ ] Live sanity (not a test): start the worker against the demo workspace, run
      a datashaded script, open `http://127.0.0.1:<port>/bm`… — SKIP the browser
      check if you can't; instead assert `/doc.json` contains an Image glyph.
- [ ] Commits: `M3.1: worker server + shared session`, `M3.1: run executor + SDK
      attach transport`, `M3.1: control API handlers`, `M3.1: tests` (order to
      taste, keep each green).
- [ ] Tick the M3.1 exit line in PLAN.md (append "✔ done" + one-line summary).

When done, report: files changed, test count, any deviation from this document
(with justification), and anything that surprised you.
