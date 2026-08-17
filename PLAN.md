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

## M1 — Workspace, SDK & runner (headless) (2–3 days)
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
  a HoloViews Points object.

## M2 — Preview pane + minimal app (2 days)
- Panel app: header (Run, auto-run toggle, status line) + center preview only.
- Consume runner results: render figure; last-good-plot retention with staleness
  badge; error banner with stderr tail; "no show()" placeholder.
- File watcher on `plot.py` (debounced ~0.5 s) driving auto-run.
- **Exit:** edit `plot.py` by hand in an external editor → preview updates live;
  break the script → last good plot stays up with error banner.

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
- **R2 HoloViews pickling** across the subprocess boundary — verify in M1 that
  Overlays/Layouts + datashader hooks survive pickle in the same env.
- **R3 Watcher debounce** vs. agents that write files in bursts — coalesce runs;
  never run a script that is mid-write (stability check: unchanged for N ms).
- **R4 Model capability floor** — if small local models fail even with skills,
  mitigation is richer recipes/templates, not a product change (design §8).

## Rough total: ~2 weeks of focused work to end of M5 (v2 complete).
