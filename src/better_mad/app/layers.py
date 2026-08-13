"""Layer model and rendering (M4, design §4.2-4.5).

Plain dataclasses (:class:`LayerSpec`, :class:`PlotSpec`) plus pure render
functions. No widgets here — headless-testable and the future home of the M6
plot-config JSON (de)serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
from holoviews.operation.datashader import rasterize
from holoviews.plotting.bokeh.element import ElementPlot
from scipy.stats import gaussian_kde

from better_mad.core.composition import family as layer_family
from better_mad.core.dataset import Dataset
from better_mad.core.styles import LayerStyle

#: Vector rendering above this row count raises the non-blocking warning banner (UX §8).
VECTOR_WARN_ROWS = 100_000
#: Line graphs in vector mode are decimated above this row count (PLAN M3).
LINE_DECIMATE_ROWS = 50_000
#: KDE subsample size for large columns (density stays representative).
KDE_SAMPLE_ROWS = 100_000

PLOT_WIDTH = 850
PLOT_HEIGHT = 650

NO_DATA = pn.pane.Markdown("*Load a file and add a plot to get started.*")
NO_VALUES = pn.pane.Markdown("*Selected column has no valid (non-NaN) values.*")
SAME_COLUMNS = pn.pane.Markdown("*X and Y must be different columns.*")


@dataclass
class LayerSpec:
    """Everything needed to render one layer; file-qualified column refs (design §4.2)."""

    file: str | None = None
    plot_type: str = "scatter"
    x: str | None = None
    y: str | None = None
    z: str = ""  # "" = no color column
    theta: str | None = None
    r: str | None = None
    aggregation: str = "count"  # density2d: "count" | "mean"
    bins: int = 50
    log_y: bool = False  # histogram y axis
    kde_overlay: bool = False  # histogram + KDE curve
    use_datashader: bool = False  # per-layer toggle (design §4.2)
    visible: bool = True
    style: LayerStyle = field(default_factory=LayerStyle)


@dataclass
class PlotSpec:
    """One plot: ordered layers plus plot-level style (design §4.3/§4.4)."""

    layers: list[LayerSpec] = field(default_factory=list)
    title: str = ""  # empty → auto
    x_label: str = ""  # empty → auto from first layer
    y_label: str = ""
    xlim: tuple[float, float] | None = None
    ylim: tuple[float, float] | None = None
    log_x: bool = False
    log_y: bool = False
    legend: bool = True
    legend_position: str = "right"
    equal_aspect: bool = False  # map views, UTM coordinates (design §4.3)
    color_locked: bool = False  # shared color scale across layers (design §4.4)
    clim_min: float | None = None
    clim_max: float | None = None


# ---------------------------------------------------------------------------
# color scale resolution
# ---------------------------------------------------------------------------


def resolve_clim(
    values: np.ndarray, style: LayerStyle, shared: tuple[float, float] | None
) -> tuple[float, float] | None:
    """Color-scale limits: locked shared scale > explicit min/max > percentile clip.

    Percentile clipping defaults to 2-98% for heavy-tailed attributes
    (design §4.4). Returns None when there is nothing meaningful to clamp.
    """
    if shared is not None:
        return shared
    lo, hi = style.clim_min, style.clim_max
    if (lo is None or hi is None) and style.clip_percentiles is not None and values.size:
        plo, phi = np.nanpercentile(values, list(style.clip_percentiles))
        lo = plo if lo is None else lo
        hi = phi if hi is None else hi
    if lo is not None and hi is not None and np.isfinite([lo, hi]).all() and lo < hi:
        return float(lo), float(hi)
    return None


def _color_opts(style: LayerStyle, clim: tuple[float, float] | None) -> dict[str, object]:
    """cmap/colorbar/clim/log-scale opts shared by z-colored elements."""
    opts: dict[str, object] = {"cmap": style.cmap, "colorbar": True}
    if clim is not None:
        opts["clim"] = clim
    if style.log_color:
        opts["cnorm"] = "log"
    return opts


# ---------------------------------------------------------------------------
# per-type renderers
# ---------------------------------------------------------------------------


def _scatter(ds: Dataset, spec: LayerSpec, clim: tuple[float, float] | None, title: str):
    if spec.x == spec.y:
        return SAME_COLUMNS
    s = spec.style
    xlabel = ds.display_names.get(spec.x or "", spec.x)
    ylabel = ds.display_names.get(spec.y or "", spec.y)
    z = spec.z
    zlabel = ds.display_names.get(z, z) if z else None
    points = hv.Points(ds.df, kdims=[spec.x, spec.y], vdims=[z] if z else [])
    base = {
        "width": PLOT_WIDTH,
        "height": PLOT_HEIGHT,
        "xlabel": xlabel,
        "ylabel": ylabel,
        "title": title,
    }
    if spec.use_datashader:
        # Count density without z; mean of z when a color column is selected.
        # Note: rasterize ignores `column=` for Points and aggregates the FIRST
        # vdim; `points` here has vdims=[z], so the mean targets z.
        if z and zlabel:
            layer = rasterize(points, aggregator="mean")
            metric = f"mean {zlabel}"
        else:
            layer = rasterize(points)
            metric = "point density"
        opts = base | _color_opts(s, clim)
        if title:
            opts["title"] = f"{title} (datashader: {metric})"
        return layer.opts(**opts)
    opts: dict[str, object] = base | {
        "size": s.size,
        "alpha": s.alpha,
        "marker": s.symbol,
        "tools": ["hover"],
    }
    if z:
        opts |= {"color": z} | _color_opts(s, clim)
    else:
        opts["color"] = s.color
    return points.opts(**opts)


def _kde_curve(values: np.ndarray, edges: np.ndarray | None, *, scale_to_counts: bool) -> hv.Curve:
    sample = values
    if values.size > KDE_SAMPLE_ROWS:
        sample = np.random.default_rng(0).choice(values, KDE_SAMPLE_ROWS)
    density = gaussian_kde(sample)
    xs = np.linspace(values.min(), values.max(), 256)
    ys = density(xs)
    if scale_to_counts and edges is not None:
        ys = ys * values.size * (edges[1] - edges[0])
    return hv.Curve((xs, ys))


def _histogram(ds: Dataset, spec: LayerSpec, title: str) -> hv.Element | pn.pane.Markdown:
    if not spec.x:
        return NO_DATA
    values = ds.df[spec.x].dropna().to_numpy()
    if values.size == 0:
        return NO_VALUES
    counts, edges = np.histogram(values, bins=spec.bins)
    xlabel = ds.display_names.get(spec.x, spec.x)
    hist = hv.Histogram((edges, counts)).opts(
        width=PLOT_WIDTH,
        height=PLOT_HEIGHT,
        color=spec.style.color,
        alpha=spec.style.alpha,
        xlabel=xlabel,
        ylabel="Count",
        logy=spec.log_y,
        tools=["hover"],
        title=title,
    )
    if not spec.kde_overlay:
        return hist
    curve = _kde_curve(values, edges, scale_to_counts=True)
    return (hist * curve).opts(hv.opts.Curve(width=PLOT_WIDTH, height=PLOT_HEIGHT, color="crimson"))


def _density1d(
    ds: Dataset, spec: LayerSpec, title: str, hist_scale: tuple[int, float] | None
) -> hv.Element | pn.pane.Markdown:
    """KDE curve; scaled to histogram counts when overlaid on one (design §4.2)."""
    if not spec.x:
        return NO_DATA
    values = ds.df[spec.x].dropna().to_numpy()
    if values.size == 0:
        return NO_VALUES
    curve = _kde_curve(values, edges=None, scale_to_counts=False)
    if hist_scale is not None:
        n, bin_width = hist_scale
        ys = curve.dimension_values(1) * n * bin_width
        curve = hv.Curve((curve.dimension_values(0), ys))
    return curve.opts(
        width=PLOT_WIDTH,
        height=PLOT_HEIGHT,
        color="crimson",
        xlabel=ds.display_names.get(spec.x, spec.x),
        ylabel="Density" if hist_scale is None else "Count",
        tools=["hover"],
        title=title,
    )


def _density2d(ds: Dataset, spec: LayerSpec, clim: tuple[float, float] | None, title: str):
    if spec.x == spec.y:
        return SAME_COLUMNS
    s = spec.style
    xlabel = ds.display_names.get(spec.x or "", spec.x)
    ylabel = ds.display_names.get(spec.y or "", spec.y)
    z = spec.z
    points = hv.Points(ds.df, kdims=[spec.x, spec.y], vdims=[z] if z else [])
    if spec.aggregation == "mean" and z:
        layer = rasterize(points, aggregator="mean")  # vdims=[z]: first vdim aggregated
        metric = f"mean {ds.display_names.get(z, z)}"
    else:
        layer = rasterize(points)
        metric = "point density"
    opts = {
        "width": PLOT_WIDTH,
        "height": PLOT_HEIGHT,
        "xlabel": xlabel,
        "ylabel": ylabel,
    } | _color_opts(s, clim)
    opts["title"] = f"{title} (datashader: {metric})" if title else metric
    return layer.opts(**opts)


def _line(ds: Dataset, spec: LayerSpec, title: str) -> hv.Element | pn.pane.Markdown:
    if spec.x == spec.y:
        return SAME_COLUMNS
    s = spec.style
    assert spec.x and spec.y
    sub = ds.df[[spec.x, spec.y]].dropna().sort_values(spec.x)
    if sub.empty:
        return NO_VALUES
    decimated = False
    if not spec.use_datashader and len(sub) > LINE_DECIMATE_ROWS:
        stride = -(-len(sub) // LINE_DECIMATE_ROWS)  # ceil division
        sub = sub.iloc[::stride]
        decimated = True
    curve = hv.Curve(sub, kdims=[spec.x], vdims=[spec.y])
    xlabel = ds.display_names.get(spec.x, spec.x)
    ylabel = ds.display_names.get(spec.y, spec.y)
    if spec.use_datashader:
        return rasterize(curve).opts(
            width=PLOT_WIDTH,
            height=PLOT_HEIGHT,
            cmap=s.cmap,
            colorbar=True,
            xlabel=xlabel,
            ylabel=ylabel,
            title=f"{title} (datashader)" if title else "",
        )
    if not title:
        title = "(decimated)" if decimated else ""
    elif decimated:
        title = f"{title} (decimated)"
    return curve.opts(
        width=PLOT_WIDTH,
        height=PLOT_HEIGHT,
        color=s.color,
        alpha=s.alpha,
        line_width=s.line_width,
        tools=["hover"],
        xlabel=xlabel,
        ylabel=ylabel,
        title=title,
    )


def _polar_graticule(r_max: float) -> hv.Overlay:
    """Polar grid: radius circles, azimuth spokes, angle labels (matplotlib-polar look).

    Bokeh has no polar projection, so this graticule is drawn over the transformed
    points; 0 degrees is at the right (east), increasing counter-clockwise.
    """
    tick_angles = np.arange(0, 360, 45)
    spoke_angles = np.arange(0, 360, 15)
    t = np.linspace(0, 2 * np.pi, 181)
    r_ticks = [r_max * f for f in (0.25, 0.5, 0.75, 1.0)]

    circles = hv.Path([np.column_stack([rt * np.cos(t), rt * np.sin(t)]) for rt in r_ticks])
    spokes = hv.Path(
        [
            np.array([[0.0, 0.0], [r_max * np.cos(np.deg2rad(a)), r_max * np.sin(np.deg2rad(a))]])
            for a in spoke_angles
        ]
    )
    labels = hv.Overlay(
        [
            hv.Text(
                1.07 * r_max * np.cos(np.deg2rad(a)),
                1.07 * r_max * np.sin(np.deg2rad(a)),
                f"{a}\u00b0",
            )
            for a in tick_angles
        ]
    )
    r_labels = hv.Overlay([hv.Text(rt, r_max * 0.03, f"{rt:g}") for rt in r_ticks[:-1]])
    grid_style = {"color": "gray", "line_width": 0.6, "alpha": 0.6}
    return (
        spokes.opts(**grid_style)
        * circles.opts(**grid_style)
        * labels.opts(hv.opts.Text(color="gray", text_font_size="9pt"))
        * r_labels.opts(hv.opts.Text(color="gray", text_font_size="8pt"))
    )


def _polar_layer(
    ds: Dataset, spec: LayerSpec, clim: tuple[float, float] | None, title: str
) -> tuple[hv.Element, float] | pn.pane.Markdown:
    """Transformed points + the max radius (for the shared graticule)."""
    s = spec.style
    # Convention (design §1#9): azimuth used as-is (0-360 degrees), r = |column|.
    # Bokeh has no native polar axes, so points are placed by transform and drawn
    # under a polar graticule (matplotlib polar-chart look).
    if not spec.theta or not spec.r:
        return NO_DATA
    t = ds.df[spec.theta].to_numpy()
    radius = np.abs(ds.df[spec.r].to_numpy())
    rad = np.deg2rad(t)
    z = spec.z
    cols = {
        "x": radius * np.cos(rad),
        "y": radius * np.sin(rad),
        spec.theta: t,
        spec.r: radius,
    }
    if z:
        cols[z] = ds.df[z].to_numpy()
    frame = pd.DataFrame(cols).dropna()
    if frame.empty:
        return NO_VALUES
    r_max = float(frame["x"].pow(2).add(frame["y"].pow(2)).pow(0.5).max()) or 1.0
    points = hv.Points(frame, kdims=["x", "y"], vdims=[spec.theta, spec.r] + ([z] if z else []))
    theta_label = ds.display_names.get(spec.theta, spec.theta)
    r_label = ds.display_names.get(spec.r, spec.r)
    layer_title = title or f"{ds.name} (polar: θ={theta_label}, r=|{r_label}|)"
    polar_opts: dict[str, object] = {
        "width": PLOT_WIDTH,
        "height": PLOT_HEIGHT,
        "aspect": "equal",
        "xaxis": None,
        "yaxis": None,
        "show_grid": False,
        "title": layer_title,
    }
    if spec.use_datashader:
        if z:
            # rasterize aggregates the FIRST vdim, so shade dedicated points with
            # vdims=[z] (the main `points` carries θ/r for hover in vector mode).
            shade_points = hv.Points(frame, kdims=["x", "y"], vdims=[z])
            layer = rasterize(shade_points, aggregator="mean")
            metric = f"mean {ds.display_names.get(z, z)}"
        else:
            layer = rasterize(points)
            metric = "point density"
        opts = polar_opts | _color_opts(s, clim)
        opts["title"] = f"{layer_title} [{metric}]"
        return layer.opts(**opts), r_max
    opts = polar_opts | {
        "size": s.size,
        "alpha": s.alpha,
        "marker": s.symbol,
        "tools": ["hover"],
    }
    if z:
        opts |= {"color": z} | _color_opts(s, clim)
    else:
        opts["color"] = s.color
    return points.opts(**opts), r_max


# ---------------------------------------------------------------------------
# plot assembly
# ---------------------------------------------------------------------------


def _visible(spec: PlotSpec) -> list[LayerSpec]:
    return [layer for layer in spec.layers if layer.visible]


def _shared_clim(spec: PlotSpec) -> tuple[float, float] | None:
    if spec.color_locked and spec.clim_min is not None and spec.clim_max is not None:
        return spec.clim_min, spec.clim_max
    return None


def render_plot(
    datasets: Mapping[str, Dataset], spec: PlotSpec
) -> hv.Element | hv.Overlay | pn.pane.Markdown:
    """Render all visible layers of one plot into a single HoloViews object."""
    layers = _visible(spec)
    resolved: list[tuple[LayerSpec, Dataset]] = []
    for layer in layers:
        if layer.file and layer.file in datasets:
            resolved.append((layer, datasets[layer.file]))
    if not resolved:
        return NO_DATA

    shared = _shared_clim(spec)
    first_title = spec.title
    family = None

    elements: list[hv.Element] = []
    graticule_r_max = 0.0
    hist_scale: tuple[int, float] | None = None

    for i, (layer, ds) in enumerate(resolved):
        title = first_title if i == 0 else ""
        if family is None:
            family = layer_family(layer.plot_type)
        z_values = ds.df[layer.z].dropna().to_numpy() if layer.z else np.array([])
        clim = resolve_clim(z_values, layer.style, shared)
        match layer.plot_type:
            case "scatter":
                el = _scatter(ds, layer, clim, title or ds.name)
                if isinstance(el, pn.pane.Markdown):
                    if i == 0:
                        return el
                    continue
                elements.append(el)
            case "density2d":
                el = _density2d(ds, layer, clim, title or ds.name)
                if isinstance(el, pn.pane.Markdown):
                    if i == 0:
                        return el
                    continue
                elements.append(el)
            case "line":
                el = _line(ds, layer, title or ds.name)
                if isinstance(el, pn.pane.Markdown):
                    if i == 0:
                        return el
                    continue
                elements.append(el)
            case "histogram":
                el = _histogram(ds, layer, title or ds.name)
                if isinstance(el, pn.pane.Markdown):
                    if i == 0:
                        return el
                    continue
                hist_el = el if isinstance(el, hv.Histogram) else next(iter(el))
                if hist_scale is None:
                    edges = hist_el.edges
                    counts = hist_el.dimension_values(1)
                    hist_scale = int(np.asarray(counts).sum()), float(edges[1] - edges[0])
                elements.append(el)
            case "density1d":
                el = _density1d(ds, layer, title or ds.name, hist_scale)
                if isinstance(el, pn.pane.Markdown):
                    if i == 0:
                        return el
                    continue
                elements.append(el)
            case "polar":
                result = _polar_layer(ds, layer, clim, title)
                if isinstance(result, pn.pane.Markdown):
                    if i == 0:
                        return result
                    continue
                el, r_max = result
                graticule_r_max = max(graticule_r_max, r_max)
                elements.append(el)

    if not elements:
        return NO_VALUES
    if family == "polar":
        # One graticule shared by all polar layers.
        pts = hv.Overlay(elements) if len(elements) > 1 else elements[0]
        return pts * _polar_graticule(graticule_r_max)
    if len(elements) == 1:
        return elements[0]
    return hv.Overlay(elements)


def fit_container_hook(plot: ElementPlot, _element: object) -> None:
    """Bokeh sizing-policy hook: make the figure fill its Panel container.

    HoloViews does not expose width_policy/height_policy as opts, so they are
    set on the bokeh figure at render time. width/height opts still serve as
    initial hints; the plot then tracks the available space (1920x1080 target:
    the plot gets everything the sidebar/drawer leave over).
    """
    fig = plot.state
    fig.width_policy = "fit"
    fig.height_policy = "fit"


def apply_plot_opts(
    element: hv.Element | hv.Overlay | pn.pane.Markdown,
    spec: PlotSpec,
    has_xy_layers: bool,
) -> hv.Element | hv.Overlay | pn.pane.Markdown:
    """Plot-level opts (labels, limits, log axes, legend, aspect) on the composite."""
    if isinstance(element, pn.pane.Markdown):
        return element
    opts: dict[str, object] = {}
    if spec.x_label:
        opts["xlabel"] = spec.x_label
    if spec.y_label:
        opts["ylabel"] = spec.y_label
    if spec.xlim:
        opts["xlim"] = spec.xlim
    if spec.ylim:
        opts["ylim"] = spec.ylim
    if spec.log_x:
        opts["logx"] = True
    if spec.log_y:
        opts["logy"] = True
    if spec.equal_aspect and has_xy_layers:
        opts["aspect"] = "equal"
    if not spec.legend:
        opts["show_legend"] = False
    elif spec.legend_position != "right":
        opts["legend_position"] = spec.legend_position
    opts["hooks"] = [fit_container_hook]
    return element.opts(**opts)


def vector_warning(datasets: Mapping[str, Dataset], spec: PlotSpec) -> str | None:
    """Non-blocking banner text when a visible vector layer is large (UX §8)."""
    for layer in _visible(spec):
        if layer.use_datashader or layer.plot_type in ("histogram", "density1d"):
            continue
        if layer.file and layer.file in datasets:
            n = datasets[layer.file].n_rows
            if n > VECTOR_WARN_ROWS:
                return f"Rendering {n:,} points as vectors — consider enabling Datashader."
    return None


def has_xy_layers(spec: PlotSpec) -> bool:
    return any(
        layer.visible and layer.plot_type in ("scatter", "density2d", "line")
        for layer in spec.layers
    )
