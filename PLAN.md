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
- Diagnostics note: Panel 1.9 renders pane content inside **Shadow DOM** — page
  scripts must traverse `shadowRoot`s (plain `querySelector` finds nothing).
  R1 (Terminal widget) still open for M3.

## M3 — Full shell: terminal, editor, files panel (3–4 days)
- Left: embedded terminal (Panel Terminal widget / pty), cwd = workspace, restart action.
- Center: Preview ⟷ Code toggle; code editor bound to `plot.py` (Ctrl+S + debounced
  auto-save); external-change policy (clean → reload; dirty → Reload/Overwrite banner);
  collapsed Output drawer with last run's stdout/stderr.
- Right: file cards with column lists + click-to-copy sanitized names, import settings
  block (delimiter/sentinels/re-parse), close-file confirm when referenced.
- Drag-resizable columns, widths persisted (CSS-variable mechanism from v1).
- **Exit:** the full loop in one window: import → ask agent (or hand-edit) → preview,
  with all three panels functional at 1920×1080.

## M4 — Agent skills & end-to-end validation (2–3 days)
- Author the workspace `AGENTS.md`: SDK reference, hard rules, and complete runnable
  recipes for: colored scatter map (equal aspect, datashaded), crossplot, histogram,
  line/CMP gather, percentile-clipped colormaps, hover/selection via streams.
- Encode v1 gotchas (rasterize-first-vdim, sizing policies, pandas-3 parsing).
- Validate with at least two harnesses (one cloud, one local) against the sample
  files; fix skill wording where models stumble.
- **Exit:** a small/local model produces a correct datashaded map of the 708k-row
  sample file by following the skills, without user code edits.

## M5 — Polish (1–2 days)
- Status-line detail (rows rendered, run time), empty/first-launch states,
  terminal hint text, README security note.
- Bug pass on watcher races (agent multi-write bursts), editor/agent conflicts.
- **Stretch (future milestones, design §11):** script history/diff, PNG export,
  session persistence, selection→CSV, embedded SDK chat panel.

## Risks & spikes
- **R1 Terminal widget** — spike Panel's pty Terminal early (M3 or sooner): spawn,
  resize, scrollback, cwd control. Fallback if inadequate: xterm.js in a custom pane.
- **R2 HoloViews pickling** across the subprocess boundary — ⚠ Elements/Curve
  pickle fine, but **DynamicMaps don't** (rasterize/datashade closures); the SDK
  materializes them to static frames (M2 fix). Overlays/Layouts of Elements OK.
- **R3 Watcher debounce** vs. agents that write files in bursts — coalesce runs;
  never run a script that is mid-write (stability check: unchanged for N ms).
- **R4 Model capability floor** — if small local models fail even with skills,
  mitigation is richer recipes/templates, not a product change (design §8).

## Rough total: ~2 weeks of focused work to end of M5 (v2 complete).
