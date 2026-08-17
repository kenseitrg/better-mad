# better-mad workspace — agent instructions

You are writing a plotting script for **better-mad**, an app for exploring seismic
attribute data. The user describes what they want to see; you write `plot.py`; the app
re-runs it automatically and shows the result as an interactive preview. Adjustments
happen by editing this file — every save triggers a fresh run.

## Hard rules

1. **Read `datasets.md` first.** It lists every loaded dataset with its columns
   (original names, script names, dtypes, min/max, NaN%). Never guess column names.
2. Use the SDK for data and output: `import better_mad.sdk as bm`.
3. Build [HoloViews](https://holoviews.org) figures (`import holoviews as hv`) and
   register the final one with `bm.show(fig)`. The **last `show()` wins**.
4. Everything runs headless in a subprocess: no `plt.show()`, no `input()`, no GUI.
5. `print()` output is shown to the user in an Output drawer — use it sparingly.
6. Keep runs fast (the user watches the preview): avoid recomputing expensive things
   on every edit when they can be variables at the top.

## SDK reference

- `bm.list_data() -> list[str]` — names of available datasets.
- `bm.data(name) -> pd.DataFrame` — a dataset by name (names are in `datasets.md`).
  Columns use **script names** (sanitized): e.g. original `TR.DOMFREQ` is `TR_DOMFREQ`,
  a leading digit gets an `X` prefix. The mapping table in `datasets.md` is authoritative.
- `bm.show(fig)` — register the preview figure: `hv.Element`, `Overlay`, `Layout`,
  `NdLayout`.

## Minimal example

```python
import holoviews as hv

import better_mad.sdk as bm

df = bm.data("sample")  # see datasets.md
pts = hv.Points(df, kdims=["XCORD_MIDPT", "YCORD_MIDPT"], vdims=["TR_DOMFREQ"])
bm.show(pts.opts(color="TR_DOMFREQ", cmap="viridis", size=3))
```

**Do not set `width=`/`height=` opts** — the preview sizes the figure to fill its
container automatically. Fixed sizes render as a small off-center plot.

## Large data

Datasets can have 1–2 million rows. For scatter-style plots above ~100k points, use
Datashader instead of vector glyphs:

```python
import datashader as ds
from holoviews.operation.datashader import rasterize

pts = hv.Points(df, kdims=["XCORD_MIDPT", "YCORD_MIDPT"], vdims=["TR_DOMFREQ"])
img = rasterize(pts, aggregator=ds.count())  # or ds.mean("TR_DOMFREQ") for color-by-z
bm.show(img.opts(cmap="viridis"))
```

## Known pitfalls

- **Do not use `aspect="equal"`**: it shrinks the plot to a small letterboxed area
  under the preview's auto-sizing (bokeh's match_aspect computes from stale default
  dimensions). If map proportions matter, mention it in a comment — proper
  equal-aspect support is tracked as future work.
- `import better_mad.sdk` already loads the HoloViews bokeh backend — `.opts()`
  works as-is; do not call `hv.extension()` or `hv.notebook_extension()` yourself.
- `rasterize(points, column=...)` **silently ignores `column`** for `hv.Points` and
  aggregates the first vdim. To color by the mean of z, shade a dedicated
  `hv.Points(..., vdims=[z])` with `aggregator=ds.mean(z)`.
- Coordinates like `XCORD_MIDPT`/`YCORD_MIDPT` are UTM-ish and irregular — scatter,
  not surface: no gridding/interpolation.
- 0 is always a real data value, never a null.
- Map views want equal aspect: `.opts(aspect="equal")`.
