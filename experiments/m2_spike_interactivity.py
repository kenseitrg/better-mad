"""M2 risk spike R1: datashader + HoloViews + Panel interactivity.

Run:
    uv run python experiments/m2_spike_interactivity.py
then open http://localhost:5007 and verify:
  1. hover tooltips work on the vector layer (shows all dims — will be restricted later)
  2. pan/zoom stays responsive at 708k points in both modes
  3. the datashader checkbox swaps rendering live without recreating the plot
  4. color-mapped datashaded rendering of a third column works (colorbar present)
"""

from __future__ import annotations

import holoviews as hv
import panel as pn
from holoviews.operation.datashader import rasterize

from better_mad.core.dataset import load_dataset

hv.extension("bokeh")

PATH = "data/14_01_post_stack_attr_after_scac_all"
X, Y, Z = "XCORD_MIDPT", "YCORD_MIDPT", "TR_DOMFREQ"

ds = load_dataset(PATH)
df = ds.df
print(f"loaded {ds.n_rows} rows in {ds.load_time_s:.2f}s (cache={ds.from_cache})")

points = hv.Points(df, kdims=[X, Y], vdims=[Z, "TR_RMSAMP", "CMP"])

vector_layer = points.opts(
    width=850,
    height=650,
    size=2,
    color="steelblue",
    alpha=0.5,
    tools=["hover"],
    xlabel=ds.display_names.get(X, X),
    ylabel=ds.display_names.get(Y, Y),
    title="vector rendering",
)

raster_layer = rasterize(points, column=Z, aggregator="mean").opts(
    width=850,
    height=650,
    cmap="viridis",
    colorbar=True,
    cnorm="eq_hist",
    xlabel=ds.display_names.get(X, X),
    ylabel=ds.display_names.get(Y, Y),
    title="datashader rendering (mean TR.DOMFREQ)",
)

toggle = pn.widgets.Checkbox(name="Datashader mode", value=True)


@pn.depends(toggle.param.value)
def view(use_datashader: bool) -> hv.Element:
    return raster_layer if use_datashader else vector_layer


pn.Column(
    pn.pane.Markdown(f"## M2 spike — {ds.n_rows:,} rows from `{PATH}`"),
    toggle,
    view,
).servable()
