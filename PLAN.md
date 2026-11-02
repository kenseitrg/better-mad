# better-mad — Implementation Plan (v2)

v2 is the pivot to **agent-driven plotting** (design.md §2). The v1 codebase lives on
`archive/codebase`; its UI-free data core is restored in M0 rather than rewritten.

Core rule (unchanged): **UI-free core first; hub, worker, SPA, and sidecar are thin
layers on top.**

## M0 — Scaffold + restore data core (1 day) ✅ done
- New uv project (deps per design §9); package layout `better_mad/{core,app}`,
  `tests/`, CLI stub.
- Restore from `archive/codebase`: `core/loading.py`, `core/columns.py`,
  `core/cache.py`, `core/dataset.py` (+ their tests and trimmed fixtures). Drop the
  v1 expression/composition/styles modules — composition is now the agent's job.
- **Exit:** loader tests green on the restored code; both 121 MB sample files load
  (first ≈11 s, cached ≈0.05 s, numbers from v1). ✔ 48 tests green; ruff + ty clean.
  Cold parse of the 121 MB file 12.8 s, cached reload 0.08 s, 31 MB RAM — v1 numbers
  confirmed. Bonus: three additional sample files (csv w/ sentinels, 229k-row
  prestack, 2D-like) also load cleanly; dropped v1 `computed_columns` field and the
  expression/composition/styles modules per the pivot.

## M1 — Workspace, SDK & runner (headless) (2–3 days) ✅ done
The new engine; fully headless and pytest-covered.
- Workspace model: create dir, write `AGENTS.md` skill file + `datasets.md` manifest;
  regenerate manifest on dataset changes.
- SDK (`better_mad/sdk`): `data(name)`, `list_data()`, `show(obj)` → pickle to the
  runner-provided output path.
- Runner: subprocess execution of `plot.py`; env wiring; stdout/stderr capture;
  timeout (default 60 s); result = figure | error | timeout + run time.
- **Tests:** script → figure round-trip; error/timeout paths; missing-dataset KeyError;
  manifest content matches loaded datasets.
- **Exit:** headless demo: load sample file → run a hand-written `plot.py` → get back
  a HoloViews Points object. ✔ 121 MB file loaded → script → `hv.Points` with 708,650
  points in 0.8 s; 14 new tests (62 total). Skill file + starter script + manifest ship
  in `core/templates`; R2 (hv pickling) verified across the subprocess boundary.

## M2 — Preview pane + minimal app (2 days) ✅ done
- Panel app: header (Run, auto-run toggle, status line) + center preview only.
- Consume runner results: render figure; last-good-plot retention with staleness
  badge; error banner with stderr tail; "no show()" placeholder.
- File watcher on `plot.py` (debounced ~0.5 s) driving auto-run.
- **Exit:** edit `plot.py` by hand in an external editor → preview updates live;
  break the script → last good plot stays up with error banner. ✔ headless:
  watcher-driven real-subprocess test + failure-path tests; live: verified with
  headless Chromium (playwright, dev dep): 708k-point datashaded map fills the
  viewport (1560×799 at 1600×900), zero server-side tracebacks. 15 new tests
  (75 total).
- Post-review sizing fixes (three layered bugs):
  1. `rasterize()`/`datashade()` return **DynamicMaps that don't pickle**
     (`Can't pickle local object 'Dynamic._dynamic_operation...'`) — `bm.show()`
     now materializes them to their current frame (recursing through containers).
     Consequence: datashaded previews are static rasters — pan/zoom work but do
     not re-aggregate; in-app re-rasterizing is future work. R2 (M1) was only
     verified for Elements before this.
  2. **FastListTemplate wraps each main item in a content-sized fast-card**; its
     shadow-DOM slot stays content-sized, so bokeh "fit" policies measured ~40 px
     parents and collapsed. Switched to VanillaTemplate (clean height chain).
  3. **Setting `pane.object` in place while the session document initializes
     renders 0-height figures** (timing race: fast scripts finish before page
     load). Fixed by recreating the HoloViews pane per figure (`_show_figure`).
- Post-review fix 4 ("colormap edit doesn't update the plot"): `.opts()` lives in
  HoloViews' global id-keyed registry and silently dies in pickle — the user's
  first "viridis" was actually the library default. SDK now captures explicit
  options per element (`_capture_options`, traverse-order aligned) and the runner
  re-applies them after unpickle. Verified live: viridis → fire switch updates the
  canvas in-session.
- Known limitation (M2): **`aspect="equal"` is incompatible with the auto-sizing
  preview** — bokeh's match_aspect computes the plot area from stale default 600×600
  dimensions under fit sizing policies (measured: 494×355 canvas in a 1560×799
  figure; sizing_mode="stretch_both" no better). Demo + skills omit it; proper
  equal-aspect support is future work (needs client-side size reconciliation).
- Post-review fix 5 ("invalid opts hang the app in running state"): opts are lazy —
  an invalid cmap passes through the subprocess and raises at render time in the app
  process; the exception escaped `tick()` and froze the session. Fixes: figures are
  validated with a headless `hv.render()` before the pane swap (mid-sync exceptions
  can wedge the page), render failures degrade to a banner + last good plot, the
  worker converts any exception to an error result (`_running` can no longer wedge),
  and `tick()` itself is exception-guarded (a dead periodic callback kills the whole
  session; note `queue.Empty` is an Exception subclass — catch it before broad
  handlers). Verified live: good → bad cmap → fixed recovers with last-good retention.
- Diagnostics note: Panel 1.9 renders pane content inside **Shadow DOM** — page
  scripts must traverse `shadowRoot`s (plain `querySelector` finds nothing).
- Post-audit fixes (2026-08-28, pre-M3 review — three issues found by re-auditing
  M0–M2; all gates re-verified: tests/ruff/ty green, app boots):
  1. Reopening a workspace (`--workspace`) rehydrates `Workspace.datasets` from
     `.data/` instead of wiping `datasets.md` while the snapshots still serve
     scripts. `add_file` writes a `.json` sidecar (source path + ParserSettings)
     per snapshot; corrupt snapshots degrade to skipped, broken sidecars to
     fallbacks. design.md §4.2 ledger updated.
  2. Script-local `.opts(hooks=[fn])` no longer breaks the figure transport
     (function pickles by reference → app-side unpickle raised AttributeError that
     escaped `run_script`). Callable-valued opts are dropped at capture, an
     unloadable payload becomes an error `RunResult` (never an escaping
     exception), and the preview sizing hook merges with script hooks instead of
     replacing them.
  3. Default preview toolbar now carries box/lasso select (UX §6) with pan pinned
     as the active drag; script-configured `tools`/`default_tools` are never
     overridden. Gotchas found: HoloViews prepends its own `default_tools` (must
     be cleared or every tool appears twice), and overlay-level `tools` opts are
     inert (an overlay's toolbar is the union of its children's — apply per leaf).
  R1 (pty Terminal widget) closed 2026-11-02 without a spike — the chat-panel
  re-scope (M3) removed the terminal from v2 entirely.
- **Superseded 2026-11-02 (frontend pivot):** the Panel app layer (`app/preview.py`,
  `app/server.py`) is replaced in M3 by the SPA + hub + worker architecture
  (design §5, §9). Retired with it: the pickle transport and its fixes (opts
  capture/re-apply, DynamicMap materialization — static-raster limitation gone).
  Everything headless (loader, workspace, runner semantics, SDK public API) carries
  over. Panel-era lessons are archived in `sessions/2026-08-17-*.md` and git history.

## M3 — Full shell: SPA, live-document worker, hub, chat (8–10 days)

**2026-11-02 re-scope (2nd):** Panel dropped after an honest re-evaluation — look &
feel capped, 15+ framework gotchas, static rasters. New architecture per design §9:
**React/TS SPA + FastAPI hub + live bokeh-server render worker + Node chat sidecar**.
The headless core survives untouched. Steps are ordered by risk (worker first):

- **M3.1 — Render worker** (`better_mad/worker/`, headless + pytest): programmatic
  `bokeh.server.Server`, single shared-session document, control API via
  `extra_patterns` (`POST /run`, `GET /health`, `GET /doc.json`). `run` executes
  `plot.py` in-process via `better_mad.sdk` (fresh namespace per run) on the IOLoop;
  `bm.show()` attaches to the document; eager render → invalid opts raise at run time
  → clean error result, document keeps last good roots. Watchdog kill on hang
  (default 60 s). Sizing: fit-to-container policies via in-process opts hook.
  *De-risked:* smoke test 2026-11-02 — 500k-pt datashaded DynamicMap renders
  in-process, attaches to a doc with `RangeXYCallback`/`PlotSizeCallback` (zoom →
  re-rasterize works).
  *Exit:* pytest spawns worker → run good script → `doc.json` contains the figure →
  run broken script → error result + last good roots intact → timeout → worker killed.
- **M3.2 — Hub backend** (`better_mad/hub/`, FastAPI + uvicorn, headless + pytest):
  workspace/files/import endpoints, `plot.py` get/put, `POST /run`, `GET /status`,
  `GET /events` (SSE: run results, worker lifecycle, embed epoch),
  `GET /plot/embed` (worker URL + session id), chat proxy `/api/chat/*` → sidecar,
  serve `web/dist`. Worker lifecycle: spawn (free port), health-poll, restart-once
  with **doc-snapshot restore** (no auto re-run after failure-restart), teardown.
  Same lifecycle for the chat sidecar.
  *Exit:* pytest drives hub end-to-end headlessly (import → run → SSE events →
  embed info); kill -9 worker → restart → snapshot restored → event on SSE.
- **M3.3 — Node chat sidecar** (`sidecar/`, TypeScript; unchanged from the
  2026-11-02 chat decision): pi SDK `AgentSession` behind pwb's chat contract
  (`/events` SSE, `/send`, `/stop`, `/new`, `/history`, `/health`); cwd = workspace,
  built-in file tools on, context files on (AGENTS.md), sessions in
  `workspace/.chat/`, watchdog, `--model provider/model` via `ModelRuntime`.
  *Exit:* sidecar against a fixture workspace; curl endpoints; "write a hello
  plot.py" prompt produces the file; SSE streams text + tool events.
- **M3.4 — SPA shell** (`web/`, React + TS + Vite, pwb dev-proxy pattern): three-pane
  layout (chat | plot/code | files) with drag-resize + persisted widths; header
  (workspace, Run, auto-run, status, model); plot pane embeds the live document via
  `bokeh.embed.server_document` (re-embed on worker restart / embed-epoch change);
  Code view with CodeMirror 6 bound to `plot.py` (Ctrl+S + debounced auto-save,
  external-change policy, Output drawer); chat pane (React port of pwb's client:
  streaming, tool notes, busy/stop/new, history replay); error/staleness/empty
  states per UX §6/§9.
  *Exit:* type "scatter XCORD_MIDPT vs YCORD_MIDPT colored by TR.DOMFREQ" in chat →
  agent writes `plot.py` → watcher runs → live plot appears → **zoom re-rasterizes**.
  Full loop in one window; verified at 1920×1080.
- **M3.5 — Files panel & polish**: file cards (columns, click-to-copy sanitized
  names, min/max/NaN%), import settings (delimiter/sentinels/re-parse), close-file
  confirm when referenced; empty/first-launch states; dark theme.
  *Exit:* import → wrong-parse → re-parse flow works end-to-end in the UI.
- **Exit (M3):** import → chat (or hand-edit) → live preview with re-aggregation on
  zoom, all three panels functional at 1920×1080; worker/sidecar crashes degrade to
  banners without touching the rest.

## M4 — Agent skills & end-to-end validation (2–3 days)
- Author the workspace `AGENTS.md`: SDK reference, hard rules, and complete runnable
  recipes for: colored scatter map (equal aspect, datashaded), crossplot, histogram,
  line/CMP gather, percentile-clipped colormaps, hover/selection via streams.
- Encode v1 gotchas (rasterize-first-vdim, sizing policies, pandas-3 parsing).
- Validate **primarily through the embedded chat panel** (AGENTS.md now loads as a
  context file with guaranteed pickup — design §8); keep one external harness in the
  loop as a regression check that nothing drifted into chat-only coupling.
- Fix skill wording where models stumble; keep `datasets.md` stats rich enough that
  the model never needs to read the parquet files.
- **Exit:** a small/local model produces a correct datashaded map of the 708k-row
  sample file through the chat panel, without user code edits.

## M5 — Polish (1–2 days)
- Status-line detail (rows rendered, run time), empty/first-launch states, chat hint
  text, README security + Node-requirement note.
- Bug pass on watcher races (agent multi-write bursts), editor/agent conflicts,
  sidecar lifecycle edges (port collisions, crash-loop backoff).
- **Stretch (future milestones, design §11):** script history/diff, PNG export,
  session persistence, selection→CSV, pty terminal pane for non-pi harnesses.

## Risks & spikes
- **R1 Chat sidecar lifecycle** — spawn/health/crash-restart; transport and manager
  semantics lifted from pwb's proven code. Node ≥ 20 runtime requirement; degrade to
  a banner when missing.
- **R2 ~~HoloViews pickling~~ RESOLVED 2026-11-02** — the pickle transport is gone;
  figures live in the worker's document. New risk in its place: **worker state
  pollution / leaks** from in-process exec — mitigations in design §5 (fresh
  namespace, recycle on failure, run cap); escalate to per-run subprocesses only if
  leaks prove real (that would forfeit live re-rasterization).
- **R2b bokeh server embedding quirks** — single shared session id (all tabs see the
  same doc), re-embed after worker restart, sizing of the embedded root in a CSS-
  sized container (plain div — no Panel templates in the way, but verify fit
  policies under BokehJS alone). Spike early in M3.4.
- **R3 Watcher debounce** vs. agents that write files in bursts — coalesce runs;
  never run a script that is mid-write (stability check: unchanged for N ms).
- **R4 Model capability floor** — if small local models fail even with skills,
  mitigation is richer recipes/templates, not a product change (design §8). The
  chat panel makes this cheaper to test (direct iteration loop in-app).
- **R5 SPA scope/async complexity** — a real frontend is more code than the 384-line
  Panel layer; mitigated by the pwb pattern (thin fetch-and-render components, no
  framework beyond React) and by keeping all data logic in the hub.

## Rough total: ~3 weeks of focused work to end of M5 (v2 complete).
