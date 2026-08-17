# AGENTS.md

## What this project is
**better-mad** — an LLM-agent-driven instrument for visualizing seismic attributes from
large tabular text files (1–2M rows). Users load data, describe plots to an agent
running in an embedded terminal, and get back an editable plotting script plus a live
interactive preview (HoloViews + Panel + Datashader) served as a localhost web app.

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
- Phase: **v2 planning complete, no code yet**. Next step: milestone **M0** (PLAN.md).
- When finishing a milestone: verify its exit criteria, then update PLAN.md status.

## Locked decisions (do not relitigate without new evidence)
- Product direction: **instrument, not omnibus** — programmable surface (agent +
  editable code) instead of menus for every plot type.
- Agent integration: **embedded pty terminal**, harness-agnostic. The app spawns no
  agent and never parses terminal traffic; coupling is **filesystem-only**
  (agent edits `plot.py`, app watches and re-runs).
- Stack: **HoloViews + Panel + Datashader**, pandas; uv + ruff + ty.
- Execution: agent/user scripts run in a **subprocess** via the `better_mad.sdk`
  transport (`show()` pickles the figure to an app-provided path). Never in-process.
  No sandbox — user-privilege execution is the documented trust model.
- One active script (`plot.py`) per workspace in v2.
- Nulls: configurable sentinels; **0 is always a valid value**. No gridding/
  interpolation.
- Distribution: CLI starts Panel server → system browser. AppImage is stretch-only.
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
**UI-free core.** Loading, workspace model, runner, SDK, and manifest generation live
in `better_mad/core` and must be headless and pytest-covered. The Panel app in
`better_mad/app` is a thin layer over the core. Don't let UI concerns leak into core.

## Working conventions
- Python ≥3.11 (system has 3.14), manage with `uv`; lint/format `ruff`, types `ty`.
- float32 for attribute columns, float64 for coordinates; parquet cache in
  `~/.cache/better-mad/` (design §6).
- When a discussion changes a decision or adds one: update `design.md`/`UX.md` in the
  **same commit**, including the decisions table at the top of each file.
- Prefer small commits per milestone step; reference the milestone (e.g. "M1: runner …").
- Errors degrade to banners/placeholders; modals only for destructive confirmations.
- Panel gotcha: string-form `@pn.depends("widget.value")` requires a
  `param.Parameterized` owner; on plain classes use explicit `pn.bind(fn, widget, ...)`.
- HoloViews gotcha: `rasterize(points, column=...)` **silently ignores `column`** for
  `hv.Points` and aggregates the **first vdim**. To mean-aggregate z, shade a dedicated
  `hv.Points(..., vdims=[z])`.
- HoloViews gotcha: bokeh sizing policies are not exposed as opts and
  `responsive=True` yields unusable `None` dimensions; set policies via an opts
  `hooks=[...]` callback on `plot.state` (see v1 `fit_container_hook` on
  `archive/codebase`).
- Panel gotcha: `pn.Tabs.active` starts at 0, so appending the first tab fires **no**
  `active` event — drawer-style watchers must also watch `objects`.
- Panel gotcha: most widgets default to `width=300, sizing_mode=None`; set
  `sizing_mode="stretch_width"` on widgets in narrow containers. `pn.config.raw_css`
  is a **list** — append, never `+=`.
- Panel gotcha: `FastListTemplate` registers sidebar objects at doc init —
  **replacing sidebar objects after serving never reaches the live page**. Mutate a
  stable container's children instead.
- CSS gotcha: JS layout shims must set the template's width **CSS variables**
  (`--sidebar-width` / `--right-sidebar-width`), never inline `min/max-width`.

## Commands (fill in as milestones land)
```bash
uv sync                 # install deps
uv run better-mad       # start app (M2+)
uv run pytest           # tests
uv run ruff check . && uv run ty check   # lint + types
```
