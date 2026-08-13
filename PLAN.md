# better-mad — Implementation Plan

Milestones ordered so that each ends with something runnable and demoable.
Core rule: **UI-free core first, Panel app is a thin layer on top** (design §8).

## M0 — Project scaffold (½ day) ✅ done
- uv project, deps: pandas, pyarrow, holoviews, panel, datashader, numexpr; dev: ruff, ty, pytest.
- Package layout: `better_mad/{core,app}/`, `tests/`, CLI entry point stub (`better-mad`).
- ruff + ty config; CI-less local checks script (`scripts/check.sh`).
- **Exit:** `uv run better-mad` prints a banner; tests run green (trivial). ✔
- Note: resolved on Python 3.11.15 with pandas 3.0.5 — watch pandas 3.x API changes in M1.

## M1 — Data core (2–3 days) ✅ done
The part everything else depends on; fully headless and tested.
- Loader: delimiter detection (whitespace/csv/tab), header parse, decimal separator,
  ragged-row/comment tolerance. Use `pd.read_csv(sep='\s+'|..., dtype=float32/64)`.
- Column name sanitization (design §2.2) + display-name registry. Deterministic, collision-safe.
- Null sentinels: configurable list per file, applied at load; **0 stays valid**.
- Optional parquet cache in `~/.cache/better-mad` keyed by path+mtime+size+parser-settings hash.
- Dataset model: name, path, DataFrame, parser settings, computed-column registry.
- **Tests:** fixtures from trimmed copies of `data/14_01_*` (first ~1000 rows committed);
  sentinel handling; name sanitization of `TR.DOMFREQ` / `3DT_*`; cache invalidation.
- **Exit:** loading the real 121 MB file takes a reported, sane time; reload via cache is fast.
  Measured: 708k×9 first load ≈11 s (python engine, `\s+`), cache reload ≈0.05 s (220×);
  31 MB RAM per file thanks to float32.

## M2 — Minimal web app + scatter/datashader (2–3 days) ✅ done
- Panel server: `better-mad file1 file2 ...` → serves UI on `--port`, opens browser.
- UI skeleton: file sidebar (loaded datasets + column list w/ display names),
  main plot pane, per-plot controls.
- Scatter plot element: x/y/z column pickers, datashader toggle per layer,
  **warn-on-large-vector-render** (>~100k pts, overridable).
- Hover annotations on vector layers (plotted x/y/z columns by default; user can add
  extra columns to the tooltip — never dump all 20–30 columns). → tooltip column
  picker lands with the M4 layer rework; plotted-cols default is in place.
- **Exit:** both sample files loaded; scatter of `TR_DOMFREQ` vs `TR_RMSAMP` renders;
  datashader toggle works at 708k rows. ✔ (R1 spike retired, color scatter added,
  Add-plot crash fixed with pn.bind regression test)

## M3 — Remaining v1 plot types (2–3 days) ✅ done
- Histogram (bins, edges, log-y) + 1D KDE overlay, shared normalization.
- 2D density layer (datashader aggregation: count default, mean optional).
- Line graph (x-column picker, stride decimation in vector mode >50k rows; LTTB deferred).
- Polar scatter (θ/r/z column pickers) — internal transform to keep azimuth work unblocked.
  Convention: θ as-is (0–360°, no flipping by offset sign), r = |offset| (design §1#9).
  Rendered under a polar graticule (matplotlib-polar look; Bokeh PolarTransform rejected
  as it breaks the datashader path for zero user-visible gain — discussed, kept for v1).
- Composition rules enforced in UI (design §4.2).
- Post-review fixes: grouped control rows (no horizontal scroll), auto-select new tab,
  polar z-color, datashader mean-of-z bug (rasterize aggregates first vdim — AGENTS.md).
- **Exit:** every v1 plot type demonstrable on the sample data. ✔ (manually verified)

## M4 — Layers, styling, color, comparison (2–3 days) ✅ done
- App module split for maintainability: `app/layers.py` (LayerSpec/PlotSpec models +
  pure render functions), `app/controls.py` (LayerRow + PlotControls widgets),
  `app/views.py` (PlotTab orchestration). Rendering is now eager-on-change with
  error-to-banner degradation; transient invalid states (e.g. x == y) get placeholders.
- Layer manager: add/remove/reorder/hide layers; file-qualified column refs;
  cross-file layers in one plot; column choices survive file swaps (same-schema).
- Composition rules (design §4.2) enforced by restricting type pickers (xy family:
  scatter/line/density2d; 1d family: histogram+density1d with shared normalization;
  polar single-family). Invalid type switches snap back to a valid type.
- Style panel: per-layer symbol/size/opacity/line-width/colormap in collapsible card;
  plot options card: title, axis labels, axis limits, log axes, legend on/off+position,
  equal aspect for maps.
- Color system: per-layer colormap, percentile clip (default 2–98%, disable = 0–100),
  explicit min/max, log color scale (cnorm), **locked shared color scale** across all
  layers of a plot (locked scale beats per-layer overrides).
- "⧉ Duplicate" + swap-file selector → comparison plot with identical parameters.
- **Exit:** the two sample files compared with identical parameters and locked color
  scale ✔ (headless smoke test: duplicate b4/after, columns kept, clim identical on both).
- Post-review UI refinement (1920×1080 target): layer blocks + plot options moved into
  a right-hand style drawer that follows the active tab; center workspace is plot-only;
  figures resize responsively via a bokeh sizing-policy hook (`fit/fit`), since
  HoloViews exposes no sizing-policy opts. Layer blocks restacked for the narrow
  drawer (≤2 widgets per row, style card collapsed).

## M5 — Filtering + expressions (2–3 days)
- Filter panel per file: range sliders + value predicates on selected columns, AND-composed,
  live-applied to all layers of that file.
- Expression engine (pandas.eval/numexpr): grammar + function library per design §3,
  sanitized-name references, `{Original.Name}` quoting, lazy materialization + cache,
  computed columns appear in pickers with a marker.
- **Tests:** expression correctness incl. NaN propagation; filter semantics.
- **Exit:** e.g. filter `STACK_WORD == 1`, plot `(A-B)/(A+B)`-style ratios.

## M6 — Sessions, configs, PNG export (2 days)
- Plot-config JSON schema (data refs, layers, styles, filters, limits); save/load/apply-to-other-file.
- Session JSON: files + parser settings + plots + layout; save/load; missing-file tolerance.
- PNG export via HoloViews matplotlib backend (per-plot button, DPI option).
- **Exit:** round-trip: session → quit → reload → identical plots; PNG output.

## M7 — Polish & packaging (1–2 days + stretch)
- UX pass: error toasts, load-progress indicators, sensible defaults, empty states.
- Selected-points export (CSV, user-chosen columns) — lower priority, cut if needed.
- **Stretch:** AppImage via PyInstaller (validate numba/datashader freezing early in a spike!).

## Risks & spikes
- **R1 Datashader+Panel selection/hover quirks** → ✅ spiked & verified with 708k rows:
  rasterize + live toggle + hover all work; vector mode usable up to ~700k (slower).
  Kept observations: wheel-zoom lag in datashader mode; multi-point hover when zoomed out
  (both recorded in design §5.1, accepted for v1).
- **R2 AppImage freezing of numba/datashader** → do a throwaway freeze spike before M7;
  drop to stretch if painful (design already allows CLI-only distribution).
- **R3 Memory at 2M rows × several files** → float32 default + parquet cache mitigate;
  measure in M1 with a synthetic 2M-row file.

## Rough total: ~2.5–3 weeks of focused work to end of M6 (v1 complete).
