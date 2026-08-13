# AGENTS.md

## What this project is
**better-mad** — a Python app for visualizing seismic attributes from large tabular text
files (1–2M rows). Interactive layered plots (HoloViews + Panel + Datashader), served as a
localhost web app opened in the system browser.

## Read these first, in order
| File | Role |
|------|------|
| `design.md` | The spec: scope, data format rules, plot types, resolved decisions (§1), deferred work (§9) |
| `UX.md` | Interaction model: layout, per-feature user flows, resolved UX decisions (§1) |
| `PLAN.md` | Milestones M0–M7 with exit criteria; tracks implementation progress |

**These documents are the source of truth.** Many questions that look open are already
decided there — check before proposing alternatives.

## Current status
- Phase: **planning complete, no code yet**. Next step: milestone **M0** (see PLAN.md).
- When finishing a milestone: verify its exit criteria, then update PLAN.md status.

## Locked decisions (do not relitigate without new evidence)
- Stack: **HoloViews + Panel + Datashader**, pandas; uv + ruff + ty. Plotly was considered and rejected.
- Distribution: CLI starts Panel server → system browser. **AppImage is stretch-only.**
- **No gridding/interpolation**; scatter maps only. Polar plots use user-supplied θ/r columns.
- Nulls: configurable sentinels; **0 is always a valid value**.
- Datashader: per-layer toggle at any size; vector mode warns >~100k points (banner, never modal).
- I/O is **browser-native** (downloads/uploads), not server-side file paths.
- Plot workspace: **tabs**. Session/plot configs: **JSON**.
- Cross-file expressions/joins, rose diagrams, line grouping: **deferred** (design §9).

## Sample data gotchas (`data/`)
Two 121 MB files, ~708k rows × 9 columns. Know these before touching the loader:
- **Whitespace-delimited**, multi-space padded, trailing spaces, no file extension.
- Hostile headers: `TR.DOMFREQ` (dot), `3DT_SEC_ORD_CELCTR` (leading digit) →
  sanitize to internal names, keep originals for display (design §2.2).
- Coordinates `XCORD_MIDPT`/`YCORD_MIDPT` are irregular (UTM-ish), not a grid.
- Rows containing `9999` are normal CMP numbers — **not** nulls. No real sentinels present
  in these files, but the loader must support configurable ones.
- Never commit these files or derivatives >~a few hundred rows.

## Architecture rule
**UI-free core.** Loading, data model, expressions, filters, and config (de)serialization
live in `better_mad/core` and must be headless and pytest-covered. The Panel app in
`better_mad/app` is a thin layer over the core. Don't let UI concerns leak into core.

App module split (M4): `app/layers.py` = spec dataclasses + pure render functions
(headless-testable); `app/controls.py` = widgets synced to specs; `app/views.py` =
PlotTab orchestration + sidebar panes; `app/server.py` = assembly/serving. Rendering
is eager-on-change and must never crash the UI — errors degrade to banners.

## Working conventions
- Python ≥3.11 (system has 3.14), manage with `uv`; lint/format `ruff`, types `ty`.
- float32 for attribute columns, float64 for coordinates; parquet cache in
  `~/.cache/better-mad/` (design §2.4).
- When a discussion changes a decision or adds one: update `design.md`/`UX.md` in the
  **same commit**, including the decisions table at the top of each file.
- Prefer small commits per milestone step; reference the milestone (e.g. "M1: loader …").
- Errors degrade to banners/placeholders; modals only for destructive confirmations.
- Panel gotcha: string-form `@pn.depends("widget.value")` requires a
  `param.Parameterized` owner; on plain classes use explicit `pn.bind(fn, widget, ...)`.
- HoloViews gotcha: `rasterize(points, column=...)` **silently ignores `column`** for
  `hv.Points` (warning "Parameter(s) [column] not consumed") and aggregates the **first
  vdim**. To mean-aggregate z, shade a dedicated `hv.Points(..., vdims=[z])`.
- HoloViews gotcha: bokeh sizing policies (`width_policy`/`height_policy`) are **not**
  exposed as opts, and `responsive=True` yields unusable `None` dimensions. To make
  plots fill their container, set the policies via an opts `hooks=[...]` callback on
  `plot.state` (see `fit_container_hook` in `app/layers.py`).
- Panel gotcha: `pn.Tabs.active` starts at 0, so appending the first tab fires **no**
  `active` event — drawer-style watchers must also watch `objects`.
- Panel gotcha: most widgets default to `width=300, sizing_mode=None`; two of them in a
  narrow container overflow and cause a horizontal scrollbar. Set
  `sizing_mode="stretch_width"` on widgets placed in sidebars/drawers. Also
  `pn.config.raw_css` is a **list** of CSS strings — append, never `+=` a string.
- Panel gotcha: `FastListTemplate` registers each sidebar object as a render item when
  the server document initializes — **replacing sidebar objects after serving never
  reaches the live page**. Put one stable container into the sidebar at construction
  and mutate *its* children instead (see `build_drawer` in `app/server.py`).
- CSS gotcha: JS layout shims must set the template's width **CSS variables**
  (`--sidebar-width` / `--right-sidebar-width`), never inline `min/max-width` — inline
  styles override the `.hidden` rules and break the header collapse buttons.

## Commands (fill in as milestones land)
```bash
uv sync                 # install deps
uv run better-mad       # start app (M2+)
uv run pytest           # tests
uv run ruff check . && uv run ty check   # lint + types
```
