# better-mad — Design

Seismic attribute visualization tool: load tabular attribute files, explore them
interactively with layered, configurable plots, and export figures.

**v1 focus: visualization quality and general UX.** Everything else is ranked below it.

---

## 1. Decisions (resolved)

| # | Topic | Decision |
|---|-------|----------|
| 1 | Frontend stack | **HoloViews + Panel + Datashader** |
| 2 | Distribution | CLI starts a Panel web server on localhost; user works in the system browser. AppImage packaging of this CLI is a **stretch goal**, not v1 |
| 3 | Polar/radial plots | Generic polar scatter where user-supplied columns provide θ and r. Pre-stack offset/azimuth workflows are **future work**, but the polar machinery must not block them |
| 4 | Map view | Scatter only. **No gridding/interpolation in v1** |
| 5 | Line grouping | No grouping needed for 3D data. For 2D lines, the user's file contains an explicit line-number column; group by a user-selected categorical column (**future work**, design must not block it) |
| 6 | Nulls | Configurable null sentinels per file. **0 is always a valid value** |
| 7 | Cross-file expressions/joins | Deferred; needs its own design pass (join keys, alignment) |
| 8 | Persistence | Save/load plot configurations and full sessions |

---

## 2. Input data

### 2.1 Format
- Plain-text tabular files: **whitespace-delimited (default)**, comma, tab, or custom
  delimiter — user-selectable per file, with auto-detect as convenience default.
- First row is a header with attribute names.
- No file extensions expected (`14_01_post_stack_attr_after_scac_all` style); the app
  must not rely on extensions.
- Decimal separator configurable (`.` default, `,` optional).
- Ragged rows: short rows are padded with NaN, over-long rows skipped; comment lines
  (`#` by default) are ignored. Never crash the load.

### 2.2 Column names
Real headers contain characters that are invalid in expressions and awkward in APIs
(`TR.DOMFREQ`, `3DT_SEC_ORD_CELCTR`), so:
- Each column gets a **sanitized internal name** (e.g. `TR_DOMFREQ`, `X3DT_SEC_ORD_CELCTR`)
  used in expressions and APIs.
- The **original name is preserved** for all display purposes (axes, legends, pickers).
- Mapping must be deterministic and collision-free (suffix on collision).

### 2.3 Null sentinels
- Per-file list of sentinel values (e.g. `-999.25`, `-9999`, `1e30`), applied at load
  time → converted to NaN. Sentinels may be numeric or **string tokens (`NULL`)**.
- Defaults provided (`-999.25, -999, -9999, -99999, 1e30, "NULL"` — matching common
  processing-software conventions), user-editable per file.
- `0.0` is never treated as null automatically.

### 2.4 Scale & performance
- Target: files with **1–2 million rows**, possibly several files open.
- Load pipeline: parse → pandas DataFrame (float32 by default for attribute columns to
  halve memory; float64 for coordinates). Report load time and row count in the UI.
- Optional **parquet cache**: after first load, write a cached `.parquet` under
  `~/.cache/better-mad/` (data directories are often read-only), keyed by
  path+mtime+size+parser-settings; subsequent loads of an unchanged file read the cache.
- Rendering:
  - **Datashader is a per-plot toggle, selectable at any dataset size.**
  - Default: warn when rendering >~100k points in vector (non-datashader) mode; user can
    override.
  - Line plots with very many points need decimation (e.g. LTTB) in vector mode;
    datashader mode needs none.

---

## 3. Expression engine (on-the-fly column arithmetic)
- Grammar: arithmetic (`+ - * / // ** %`), comparisons, unary ops, parentheses;
  function library: `abs, log, log10, sqrt, exp, clip, where, normalize, percentile`.
- Column references use the **sanitized names**; the UI picker inserts them, so the user
  rarely types. A quoting syntax (e.g. `{Original.Name}`) resolves ambiguous cases.
- Implementation: `pandas.eval`/numexpr-based (fast, safe — no arbitrary code execution).
- Computed columns are materialized lazily and cached; they show up as regular columns
  with a marker in the UI.
- Expressions operate **within one file** in v1. Cross-file operations are deferred (§9).

---

## 4. Plot system

### 4.1 Plot types (v1)
| Type | Notes |
|------|-------|
| Scatter (XY) | map views and crossplots |
| Color scatter | third column mapped to color |
| Polar scatter | user picks θ column and r column (enables future azimuth/offset work) |
| Histogram | continuous column, configurable bin count/edges, linear or log y |
| Density (1D) | KDE over a column, overlayable on histogram |
| 2D density | datashader aggregation as image layer: count (default) or mean of a z column |
| Line graph | attribute vs an x column (CMP, CDP, index…) |

Deferred (future work, design must not block): rose diagram (needs azimuth input data),
bar chart for categorical columns, 2D line grouping.

### 4.2 Layers & composition
- Every plot holds an ordered list of **layers**; each layer references a loaded file
  (or computed column), a plot type, and a style block.
- Layers from **different files** may coexist in one plot. Column references are therefore
  always file-qualified internally.
- Composition rules (what can overlay what):
  - scatter + scatter, scatter + line, scatter + 2D density image: OK
  - histogram + density curve: OK (shared normalization: density scaled to counts or %)
  - arbitrary mixing is not promised; the UI exposes only valid combinations.
- Datashader toggle is **per layer**; a plot may mix vector and raster layers.

### 4.3 Styling (user-configurable)
Legend (on/off, position, labels), symbol, size, opacity, colormap, axis labels, titles,
axis limits, log/linear axes, equal aspect for map views (UTM coordinates), grid lines.

### 4.4 Color handling (required, not optional)
- Per-layer colormap selection.
- **Percentile clipping** (default e.g. 2–98%) for heavy-tailed attribute distributions;
  explicit min/max override.
- Log color scale option.
- **Shared color scale across layers/plots** for precise dataset comparison (point 15 of
  the original draft): a "lock color scale" control applying to selected layers.

### 4.5 Comparison workflow
Multiple datasets (e.g. `*_b4_scac_all` vs `*_after_scac_all`) with identical
visualization parameters and axis/color limits. Supported via:
- duplicating a layer/plot and swapping the file,
- locked/shared axis and color limits (§4.4),
- session templates (§6).

---

## 5. Interactivity
- Pan/zoom everywhere (Panel/HoloViews defaults).
- **Hover annotations** on vector layers: show the plotted columns (x, y, and color/z if
  present) by default; the user can add extra columns to the hover tooltip per layer.
  Never annotate the full attribute list — files can have 20–30 columns.
  Known limitation: datashaded layers are raster images — no per-point hover. v1 accepts
  this; a nearest-point lookup on tap is a future enhancement.
- **Filtering**: per-file filter panel — range sliders/value filters on user-selected
  columns; filters apply to all layers using that file. Filters compose (AND).
- **Selection/lasso**: HoloViews linked selection where supported; feeds the (lower
  priority) point export (§7).

---

## 6. Configurations & sessions
- Every plot's full state (data refs, plot type, layers, styles, filters, limits) is
  serializable to **JSON** (chosen over YAML: zero extra dependencies).
- **Plot config**: one plot's state; saveable/loadable as a template, applicable to other
  files (the comparison workflow depends on this).
- **Session**: all loaded files (paths + parser settings + sentinels) + all plots + layout.
  Save/load from the UI. File paths stored as-given; missing files degrade to a warning,
  not a crash.

---

## 7. Export
- **PNG export** of any plot via HoloViews matplotlib backend (no kaleido/Chromium needed).
- Selected-points export (CSV; user chooses columns incl. computed ones): **lower priority**,
  design kept open but implementation deferred within v1 if time-boxed.

---

## 8. Tech stack & architecture
- Python ≥ 3.11, **uv** for environment/deps, **ruff** (lint/format) + **ty** (types).
- Core: pandas (float32), HoloViews, Panel (server + widgets), Datashader, xarray
  (datashader aggregates), numexpr or pandas.eval.
- Testing: pytest for the data layer (parsing, sentinels, name sanitization, expressions)
  and config (de)serialization — the parts that are headless-testable.
- Architecture: **UI-free core** (loading, data model, expressions, filters, plot-config
  model) separated from the Panel app. The core must be usable headlessly; the web app is
  a thin layer over it. This keeps packaging, testing, and a future CLI rendering mode sane.
- CLI: `better-mad [files...]` → starts Panel server on localhost, opens system browser.
  Port selection (`--port`), headless flag for later batch rendering.

## 9. Future work (explicitly out of v1, ordered)
1. Rose diagram + offset/azimuth workflows (pre-stack files)
2. Cross-file expressions & joins (join key design needed — CMP? coordinates?)
3. Line grouping by categorical column (2D data)
4. Categorical bar charts
5. Gridding/interpolation for map views
6. Hover/tap inspection on datashaded layers
7. AppImage packaging of the CLI
8. Batch/headless plot rendering from saved configs
