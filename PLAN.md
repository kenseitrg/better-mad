# better-mad — Implementation Plan (v2)

v2 is the pivot to **agent-driven plotting** (design.md §2). The v1 codebase lives on
`archive/codebase`; its UI-free data core is restored in M0 rather than rewritten.

Core rule (unchanged): **UI-free core first, Panel app is a thin layer on top.**

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

## M3 — Full shell: chat panel, editor, files panel (3–4 days)

**2026-11-02 re-scope:** the left pane is now an **embedded pi chat panel** (design
§9.1) instead of a pty terminal — the terminal moves to future work (design §11.2).
Reference implementation: the pwb project (`~/pwb` — Fastify chat transport + pi SDK
`AgentSession` + manager with busy/stop/watchdog). Split into small steps:

- **M3.1 — Node chat sidecar** (`sidecar/`, TypeScript): pi SDK `AgentSession` behind
  pwb's chat contract — `GET /events` (SSE + heartbeat: `text_delta`, `agent_start/end`,
  `tool_execution_start/end`, `stopped`, `reset`, `error`), `POST /send` (202/busy-409),
  `POST /stop` (`session.abort()`), `POST /new`, `GET /history`, `GET /health`.
  Session: `cwd = workspace` (argv), built-in file tools **on** (they are the protocol),
  context files **on** (`AGENTS.md` auto-loads), no extensions/templates/themes,
  sessions at `workspace/.chat/` (`SessionManager`, resume recent on boot), watchdog
  abort on hung provider calls (pwb: 5 min). Model: `--model provider/model` arg
  resolved via `ModelRuntime` against `~/.pi/agent`; default = pi default.
  *Exit:* run the sidecar against a fixture workspace; `curl` the endpoints; a
  "write a hello plot.py" prompt produces the file; SSE streams text + tool events.
- **M3.2 — Sidecar lifecycle in core** (`core/agent.py`, headless + pytest): pick a
  free localhost port, spawn `node sidecar/dist/…`, poll `/health`, expose base URL;
  clean teardown; restart-once-on-crash then degrade. Tests run against the real
  sidecar when node + deps exist (skipif otherwise) plus a stub-server path for CI.
  *Exit:* pytest spins workspace + sidecar → health green → shutdown clean; kill -9
  the sidecar → restart → health green again.
- **M3.3 — Chat pane in the app** (`app/chat.py`): `pn.chat` (ChatFeed/ChatAreaInput)
  in the left column. A daemon SSE-reader thread pushes events into `queue.Queue`;
  the existing `tick()` drains them (thread rules per AGENTS.md — never touch widgets
  from the reader thread; `tick()` must not raise; catch `queue.Empty` first).
  Streaming deltas into the live bubble, tool notes ("⚙ name"), busy state disabling
  input, Stop → `/stop`, New chat → `/new` + clear transcript, history replay on pane
  init (`/history`), sidecar-down → banner with retry. Add `httpx` dep.
  *Exit:* type "scatter XCORD_MIDPT vs YCORD_MIDPT colored by TR.DOMFREQ" → agent
  writes `plot.py` → watcher runs → preview updates. **The full loop in one window.**
- **M3.4 — Code view** (as originally planned): center **Preview ⟷ Code** toggle;
  editor bound to `plot.py` (Ctrl+S + debounced auto-save); external-change policy
  (clean → silent reload; dirty → Reload/Overwrite banner); collapsed Output drawer
  with last run's stdout/stderr.
- **M3.5 — Files panel + resizable columns** (as originally planned): right panel
  file cards with column lists + click-to-copy sanitized names, import settings block
  (delimiter/sentinels/re-parse), close-file confirm when referenced; drag-resizable
  columns with persisted widths (CSS-variable mechanism from v1).
- **Exit (M3):** import → chat (or hand-edit) → preview, all three panels functional
  at 1920×1080; sidecar crash degrades to a banner without touching preview/editor.

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
- **R1 Node sidecar lifecycle** (re-scoped 2026-11-02 from the pty-terminal spike —
  dropped): spawn/health/crash-restart and port handling are ours to own, but the
  transport and manager semantics are lifted from pwb's proven code (`manager.ts`,
  `sessions.ts`, `chat.ts`). Node ≥ 20 becomes a runtime requirement; the app must
  degrade to a banner when it is missing.
- **R1b pn.chat streaming from a background thread** — pn.chat widgets are Panel
  objects with the usual threading rules; stream via the established queue + `tick()`
  drain only (never from the SSE reader thread), and `tick()` must never raise.
- **R2 HoloViews pickling** across the subprocess boundary — ⚠ Elements/Curve
  pickle fine, but **DynamicMaps don't** (rasterize/datashade closures); the SDK
  materializes them to static frames (M2 fix). Overlays/Layouts of Elements OK.
- **R3 Watcher debounce** vs. agents that write files in bursts — coalesce runs;
  never run a script that is mid-write (stability check: unchanged for N ms).
  The chat agent writes `plot.py` via its file tools — same burst profile as before.
- **R4 Model capability floor** — if small local models fail even with skills,
  mitigation is richer recipes/templates, not a product change (design §8). The
  chat panel makes this cheaper to test (direct iteration loop in-app).

## Rough total: ~2 weeks of focused work to end of M5 (v2 complete).
