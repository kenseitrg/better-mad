# AGENTS.md

## What this project is
**better-mad** — an LLM-agent-driven instrument for visualizing seismic attributes from
large tabular text files (1–2M rows). Users load data, describe plots to an embedded
pi agent chat, and get back an editable plotting script plus a live
interactive preview (HoloViews + Datashader on a live bokeh server, React SPA shell)
served as a localhost web app.

## Read these first, in order
| File | Role |
|------|------|
| `design.md` | The v2 spec: concept, layout, workspace/agent protocol, SDK & runner, decisions (§1), future work (§11) |
| `UX.md` | Interaction model: three-pane shell, per-feature user flows, decisions (§1) |
| `PLAN.md` | Milestones M0–M5 with exit criteria; tracks implementation progress |

**These documents are the source of truth.** Many questions that look open are already
decided there — check before proposing alternatives.

## History
v1 was a declarative plotting UI (M0–M4 implemented). In August 2025 the project
pivoted to agent-driven plotting; the complete v1 codebase is preserved on the
**`archive/codebase`** branch. Its UI-free data core (loader, column sanitization,
parquet cache) is restored in M0 — restore from there, don't rewrite.

## Current status
- Phase: **M3 in progress — M3.4 done** (SPA shell; exit criteria verified live:
  chat → agent writes plot.py → watcher runs → live plot → zoom re-rasterizes at
  1920×1080, one window; kill -9 worker → re-embed with last-good; kill -9 sidecar
  → chat banner, everything else alive; prod build served by the hub verified).
  Next: **M3.5 files panel & polish** (import settings, click-to-copy, close
  confirm, theme/empty states; PLAN.md M3.5).
- When finishing a milestone: verify its exit criteria, then update PLAN.md status.

## Locked decisions (do not relitigate without new evidence)
- Product direction: **instrument, not omnibus** — programmable surface (agent +
  editable code) instead of menus for every plot type.
- Agent integration: **embedded pi chat panel** — a Node sidecar process hosts a pi
  SDK `AgentSession` (cwd = workspace) and serves pwb's SSE/HTTP chat contract; the
  app renders it with a React chat pane (pwb client pattern). Coupling remains **filesystem-only** (agent edits
  `plot.py`, app watches and re-runs). The app spawns no pty and never parses agent
  traffic beyond chat display events. Reference implementation: `~/pwb`.
- Stack: **HoloViews + Datashader** rendered on a **live bokeh-server document** in
  an isolated render worker; **React/TS SPA** frontend; **FastAPI** hub; Node pi-chat
  sidecar; pandas; uv + ruff + ty (Python), Vite + tsc (TypeScript). Panel dropped
  2026-11-02 (look & feel, framework fights, static rasters). Node ≥22.18 for the
  sidecar (native-TS execution; locked 2026-11-10).
- Execution: `plot.py` runs **inside the render worker** (isolated process, fresh
  namespace per run, watchdog timeout, kill/restart on fatal) against a live bokeh
  document; `bm.show()` attaches figures — **DynamicMaps stay dynamic** (zoom
  re-rasterizes). Never executes in the hub. (2026-11-02: replaces subprocess +
  pickle transport, which forced static rasters.)
- One active script (`plot.py`) per workspace in v2.
- Nulls: configurable sentinels; **0 is always a valid value**. No gridding/
  interpolation.
- Distribution: CLI starts the FastAPI hub (spawns render worker + chat sidecar,
  serves the SPA) → system browser. AppImage is stretch-only.
- I/O is **browser-native** (imports/downloads), not server-side file paths.

## Sample data gotchas (`data/`)
Two 121 MB files, ~708k rows × 9 columns. Know these before touching the loader:
- **Whitespace-delimited**, multi-space padded, trailing spaces, no file extension.
- Hostile headers: `TR.DOMFREQ` (dot), `3DT_SEC_ORD_CELCTR` (leading digit) →
  sanitize to internal names, keep originals for display (design §6).
- Coordinates `XCORD_MIDPT`/`YCORD_MIDPT` are irregular (UTM-ish), not a grid.
- Rows containing `9999` are normal CMP numbers — **not** nulls. No real sentinels
  present in these files, but the loader must support configurable ones.
- Never commit these files or derivatives >~a few hundred rows.

## Architecture rule
**UI-free core.** Loading, workspace model, script-execution semantics, SDK, manifest
generation live in `better_mad/core` and must be headless and pytest-covered — no
framework imports in `core/`. Worker process lifecycle lives in `better_mad/worker/`
(headless, pytest-covered; it necessarily imports bokeh); sidecar lifecycle lives
in `better_mad/hub/sidecar.py` beside the hub (M3.3), mirroring
`worker/manager.py`. Thin layers over the core:
`better_mad/worker/` (bokeh server render worker), `better_mad/hub/` (FastAPI
transport), `web/` (React SPA), `sidecar/` (Node pi-chat host). Don't let UI
concerns leak into core.

## Working conventions
- Python ≥3.11 (system has 3.14), manage with `uv`; lint/format `ruff`, types `ty`.
  **Node ≥22.18** required for the chat sidecar (native-TS execution; M3.3); TypeScript in `sidecar/`.
- float32 for attribute columns, float64 for coordinates; parquet cache in
  `~/.cache/better-mad/` (design §6).
- When a discussion changes a decision or adds one: update `design.md`/`UX.md` in the
  **same commit**, including the decisions table at the top of each file.
- Prefer small commits per milestone step; reference the milestone (e.g. "M1: runner …").
- Errors degrade to banners/placeholders; modals only for destructive confirmations.

## Gotchas (current architecture)
- HoloViews gotcha: `rasterize(points, column=...)` **silently ignores `column`** for
  `hv.Points` and aggregates the **first vdim**. To mean-aggregate z, shade a dedicated
  `hv.Points(..., vdims=[z])`.
- HoloViews gotcha: a bare `import holoviews` may set `Store.current_backend` while
  **no plotting module is loaded** (`Store.loaded_backends() == []`) — `.opts()` then
  raises "No plotting extension is currently loaded". Any process that applies opts
  must call `hv.extension("bokeh")` itself (the render worker does; test
  `loaded_backends()`, not `current_backend`).
- HoloViews gotcha: opts are **lazy** — invalid values (e.g. a bad cmap) raise at
  render time, not at `.opts()`. In the worker this is safe: render is eager inside
  `run`, so bad opts become clean error results while the document keeps its last
  good roots.
- Bokeh sizing gotcha: fit/stretch sizing policies are not exposed as hv opts; apply
  them via an opts `hooks=[...]` callback on `plot.state` (v1 `fit_container_hook`,
  `archive/codebase`). Re-verify under the SPA's plain CSS-sized container (no Panel
  template in the chain anymore — should be easier, not harder).
- Worker gotcha: script runs execute on the bokeh IOLoop thread — a long run blocks
  document pushes for its duration (acceptable at ~seconds). The hub drives the
  worker only through its control API (`/run`, `/health`, `/doc.json`), never by
  poking its internals.
- **Panel-era gotchas are historical** (Shadow DOM traversal, FastListTemplate card
  collapse, sidebar registration, pane `.object` races, `tick()`/queue threading
  rules, DynamicMap/opts pickling). They applied to the archived Panel app layer —
  see `sessions/2026-08-17-v2-pivot-and-m2.md` and M2 commits. Do not carry them
  into the new stack.
- SPA-era gotcha (M3.4): **Bokeh 3.x renders views into Shadow DOM** —
  `document.querySelectorAll('canvas')` finds nothing; probe by walking
  `el.shadowRoot` recursively (see `embed.ts`/QA scripts). `innerHTML` of the
  host (`.bk-Figure`) also looks empty while the figure is fully rendered.
- SPA-era gotcha (M3.4): **center-view stacking must keep layout computed** —
  toggling Preview/Code uses stacked `visibility` layers, not `display:none`
  (which zeroes the live bokeh container → stale canvas on re-show, and
  un-measures CodeMirror).

## Commands (fill in as milestones land)
```bash
uv sync                 # install deps
uv run better-mad       # start hub (+ worker, sidecar, SPA) (M3+)
uv run better-mad --model provider/model   # override the chat model (M3.3+)
uv run better-mad data/<file> --no-browser --dev-origin http://localhost:5173
                        # dev mode: allowlist the Vite origin for the live-doc
                        # WebSocket (trap T1 — without it the plot pane shows
                        # nothing; check the browser console for the 403)
uv run pytest           # tests
uv run ruff check . && uv run ty check   # lint + types
cd sidecar && npm install && (npm run typecheck && npm test)   # sidecar (M3.3+)
cd web && npm install                    # web/ deps (M3.4+)
cd web && npm run dev                    # Vite dev server on 5173, proxies /api → hub
cd web && npm run typecheck && npm test  # web gates (M3.4+)
npm run build           # prod SPA → web/dist, served by the hub automatically
```
