"""UI building blocks: dataset sidebar panes and plot tabs (M2/M3)."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
from holoviews.operation.datashader import rasterize
from scipy.stats import gaussian_kde

from better_mad.app.state import AppState

#: Vector rendering above this row count raises the non-blocking warning banner (UX §8).
VECTOR_WARN_ROWS = 100_000
#: Line graphs in vector mode are decimated above this row count (PLAN M3).
LINE_DECIMATE_ROWS = 50_000
#: KDE subsample size for large columns (density stays representative).
KDE_SAMPLE_ROWS = 100_000

_PLOT_WIDTH = 850
_PLOT_HEIGHT = 650

PLOT_TYPES: dict[str, str] = {
    "Scatter": "scatter",
    "Histogram": "histogram",
    "Density (1D)": "density1d",
    "Density (2D)": "density2d",
    "Line graph": "line",
    "Polar scatter": "polar",
}

_NO_DATA = pn.pane.Markdown("*Load a file and add a plot to get started.*")
_NO_VALUES = pn.pane.Markdown("*Selected column has no valid (non-NaN) values.*")


def dataset_pane(name: str, state: AppState) -> pn.pane.Markdown:
    """Sidebar summary of one loaded dataset."""
    ds = state.datasets[name]
    cols = "\n".join(f"- {ds.display_names.get(c, c)}" for c in ds.columns)
    return pn.pane.Markdown(
        f"**{name}**\n\n"
        f"{ds.n_rows:,} rows, {len(ds.columns)} cols, "
        f"loaded in {ds.load_time_s:.2f}s{' (cache)' if ds.from_cache else ''}\n\n"
        f"{cols}"
    )


def failures_pane(state: AppState) -> pn.pane.Alert | None:
    if not state.failures:
        return None
    text = "\n".join(f"- {f.path}: {f.error}" for f in state.failures)
    return pn.pane.Alert(f"**Failed to load:**\n{text}", alert_type="danger")


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


class PlotTab:
    """One plot tab: type selector, role slots, datashader toggle, view.

    Headless-constructible; the Panel layout is only assembled in :meth:`layout`.
    """

    def __init__(self, state: AppState, index: int):
        self.state = state
        self.index = index
        names = list(state.datasets)
        self.type_sel = pn.widgets.Select(label="Plot type", options=PLOT_TYPES, value="scatter")
        self.file_sel = pn.widgets.Select(
            label="File", options=names, value=names[0] if names else None
        )
        self.x_sel = pn.widgets.Select(label="X column")
        self.y_sel = pn.widgets.Select(label="Y column")
        self.z_sel = pn.widgets.Select(label="Color (z)")
        self.theta_sel = pn.widgets.Select(label="θ column (degrees)")
        self.r_sel = pn.widgets.Select(label="r column (|value| used)")
        self.agg_sel = pn.widgets.Select(
            label="Aggregation", options={"count": "count", "mean of z": "mean"}
        )
        self.bins_slider = pn.widgets.IntSlider(label="Bins", start=5, end=200, step=5, value=50)
        self.log_y = pn.widgets.Checkbox(label="Log y")
        self.kde_overlay = pn.widgets.Checkbox(label="KDE overlay")
        self.ds_toggle = pn.widgets.Checkbox(label="Datashader")
        self._sync_columns()
        self._sync_visibility()
        self.file_sel.param.watch(self._on_file_change, "value")
        self.type_sel.param.watch(lambda _e: self._sync_visibility(), "value")
        # Explicit binds: PlotTab is a plain class (no .param), so string-form
        # @pn.depends cannot resolve widget parameters here. Widgets supply the
        # arguments, so the bound attributes are effectively zero-arg callables.
        self.view = cast(
            Callable[[], hv.Element | pn.pane.Markdown],
            pn.bind(
                self._render_view,
                self.type_sel,
                self.file_sel,
                self.x_sel,
                self.y_sel,
                self.z_sel,
                self.theta_sel,
                self.r_sel,
                self.agg_sel,
                self.bins_slider,
                self.log_y,
                self.kde_overlay,
                self.ds_toggle,
            ),
        )
        self.banner = cast(
            Callable[[], pn.pane.Alert | None],
            pn.bind(self._render_banner, self.type_sel, self.file_sel, self.ds_toggle),
        )

    # --- widget bookkeeping -------------------------------------------------

    def _on_file_change(self, _event: object) -> None:
        self._sync_columns()

    def _sync_columns(self) -> None:
        if not self.file_sel.value:
            for w in (self.x_sel, self.y_sel, self.z_sel, self.theta_sel, self.r_sel):
                w.options = {}
            return
        options = self.state.column_options(self.file_sel.value)
        self.x_sel.options = dict(options)
        self.y_sel.options = dict(options)
        self.theta_sel.options = dict(options)
        self.r_sel.options = dict(options)
        self.z_sel.options = {"(none)": "", **options}
        cols = list(options.values())
        self.x_sel.value = cols[0] if cols else None
        self.y_sel.value = cols[1] if len(cols) > 1 else None
        self.theta_sel.value = cols[0] if cols else None
        self.r_sel.value = cols[1] if len(cols) > 1 else None
        self.z_sel.value = ""
        # Sensible default: datashader for large files (UX §8).
        self.ds_toggle.value = self.state.datasets[self.file_sel.value].n_rows > VECTOR_WARN_ROWS

    def _sync_visibility(self) -> None:
        t = self.type_sel.value
        self.x_sel.label = "Column" if t in ("histogram", "density1d") else "X column"
        self.z_sel.label = "Color (mean of)" if t == "density2d" else "Color (z)"
        self.x_sel.visible = t != "polar"
        self.y_sel.visible = t in ("scatter", "density2d", "line")
        self.z_sel.visible = t in ("scatter", "density2d", "polar")
        self.theta_sel.visible = t == "polar"
        self.r_sel.visible = t == "polar"
        self.agg_sel.visible = t == "density2d"
        self.bins_slider.visible = t == "histogram"
        self.log_y.visible = t == "histogram"
        self.kde_overlay.visible = t == "histogram"
        self.ds_toggle.visible = t in ("scatter", "density2d", "line", "polar")
        if t == "density2d":
            self.ds_toggle.value = True

    # --- rendering ------------------------------------------------------------

    def _render_view(
        self,
        plot_type: str,
        file_name: str | None,
        x: str | None,
        y: str | None,
        z: str,
        theta: str | None,
        r: str | None,
        aggregation: str,
        bins: int,
        log_y: bool,
        kde: bool,
        use_datashader: bool,
    ) -> hv.Element | pn.pane.Markdown:
        if not file_name:
            return _NO_DATA
        ds = self.state.datasets[file_name]
        match plot_type:
            case "histogram":
                return self._histogram(ds, x, bins, log_y, kde)
            case "density1d":
                return self._density1d(ds, x)
            case "polar":
                return self._polar(ds, theta, r, z, use_datashader)
        if not x or not y:
            return _NO_DATA
        match plot_type:
            case "line":
                return self._line(ds, x, y, use_datashader)
            case "density2d":
                return self._density2d(ds, x, y, z, aggregation)
            case _:  # scatter
                return self._scatter(ds, x, y, z, use_datashader)

    def _scatter(self, ds, x: str, y: str, z: str, use_datashader: bool):
        xlabel = ds.display_names.get(x, x)
        ylabel = ds.display_names.get(y, y)
        zlabel = ds.display_names.get(z, z) if z else None
        points = hv.Points(ds.df, kdims=[x, y], vdims=[z] if z else [])
        if use_datashader:
            # Count density without z; mean of z when a color column is selected.
            # Note: rasterize ignores `column=` for Points and aggregates the FIRST vdim;
            # `points` here has vdims=[z], so the mean targets z.
            if z and zlabel:
                layer = rasterize(points, aggregator="mean")
                metric = f"mean {zlabel}"
            else:
                layer = rasterize(points)
                metric = "point density"
            return layer.opts(
                width=_PLOT_WIDTH,
                height=_PLOT_HEIGHT,
                cmap="viridis",
                colorbar=True,
                xlabel=xlabel,
                ylabel=ylabel,
                title=f"{ds.name} (datashader: {metric})",
            )
        opts: dict[str, object] = {
            "width": _PLOT_WIDTH,
            "height": _PLOT_HEIGHT,
            "size": 2,
            "alpha": 0.6,
            "tools": ["hover"],
            "xlabel": xlabel,
            "ylabel": ylabel,
            "title": ds.name,
        }
        if z:
            opts |= {"color": z, "cmap": "viridis", "colorbar": True}
        else:
            opts["color"] = "steelblue"
        return points.opts(**opts)

    def _histogram(
        self, ds, x: str | None, bins: int, log_y: bool, kde: bool
    ) -> hv.Element | pn.pane.Markdown:
        if not x:
            return _NO_DATA
        values = ds.df[x].dropna().to_numpy()
        if values.size == 0:
            return _NO_VALUES
        counts, edges = np.histogram(values, bins=bins)
        xlabel = ds.display_names.get(x, x)
        hist = hv.Histogram((edges, counts)).opts(
            width=_PLOT_WIDTH,
            height=_PLOT_HEIGHT,
            color="steelblue",
            xlabel=xlabel,
            ylabel="Count",
            logy=log_y,
            tools=["hover"],
            title=ds.name,
        )
        if not kde:
            return hist
        curve = self._kde_curve(values, edges, scale_to_counts=True)
        return (hist * curve).opts(
            hv.opts.Curve(width=_PLOT_WIDTH, height=_PLOT_HEIGHT, color="crimson")
        )

    def _density1d(self, ds, x: str | None) -> hv.Element | pn.pane.Markdown:
        if not x:
            return _NO_DATA
        values = ds.df[x].dropna().to_numpy()
        if values.size == 0:
            return _NO_VALUES
        curve = self._kde_curve(values, edges=None, scale_to_counts=False)
        return curve.opts(
            width=_PLOT_WIDTH,
            height=_PLOT_HEIGHT,
            color="crimson",
            xlabel=ds.display_names.get(x, x),
            ylabel="Density",
            tools=["hover"],
            title=ds.name,
        )

    @staticmethod
    def _kde_curve(
        values: np.ndarray, edges: np.ndarray | None, *, scale_to_counts: bool
    ) -> hv.Curve:
        sample = values
        if values.size > KDE_SAMPLE_ROWS:
            sample = np.random.default_rng(0).choice(values, KDE_SAMPLE_ROWS)
        density = gaussian_kde(sample)
        xs = np.linspace(values.min(), values.max(), 256)
        ys = density(xs)
        if scale_to_counts and edges is not None:
            ys = ys * values.size * (edges[1] - edges[0])
        return hv.Curve((xs, ys))

    def _density2d(self, ds, x: str, y: str, z: str, aggregation: str):
        xlabel = ds.display_names.get(x, x)
        ylabel = ds.display_names.get(y, y)
        points = hv.Points(ds.df, kdims=[x, y], vdims=[z] if z else [])
        if aggregation == "mean" and z:
            layer = rasterize(points, aggregator="mean")  # vdims=[z]: first vdim is aggregated
            metric = f"mean {ds.display_names.get(z, z)}"
        else:
            layer = rasterize(points)
            metric = "point density"
        return layer.opts(
            width=_PLOT_WIDTH,
            height=_PLOT_HEIGHT,
            cmap="viridis",
            colorbar=True,
            xlabel=xlabel,
            ylabel=ylabel,
            title=f"{ds.name} (datashader: {metric})",
        )

    def _line(self, ds, x: str, y: str, use_datashader: bool):
        sub = ds.df[[x, y]].dropna().sort_values(x)
        if sub.empty:
            return _NO_VALUES
        decimated = False
        if not use_datashader and len(sub) > LINE_DECIMATE_ROWS:
            stride = -(-len(sub) // LINE_DECIMATE_ROWS)  # ceil division
            sub = sub.iloc[::stride]
            decimated = True
        curve = hv.Curve(sub, kdims=[x], vdims=[y])
        xlabel = ds.display_names.get(x, x)
        ylabel = ds.display_names.get(y, y)
        if use_datashader:
            return rasterize(curve).opts(
                width=_PLOT_WIDTH,
                height=_PLOT_HEIGHT,
                cmap="viridis",
                colorbar=True,
                xlabel=xlabel,
                ylabel=ylabel,
                title=f"{ds.name} (datashader)",
            )
        title = f"{ds.name} (decimated)" if decimated else ds.name
        return curve.opts(
            width=_PLOT_WIDTH,
            height=_PLOT_HEIGHT,
            color="steelblue",
            line_width=1,
            tools=["hover"],
            xlabel=xlabel,
            ylabel=ylabel,
            title=title,
        )

    def _polar(self, ds, theta: str | None, r: str | None, z: str, use_datashader: bool):
        # Convention (design §1#9): azimuth used as-is (0-360 degrees), r = |column|.
        # Bokeh has no native polar axes, so points are placed by transform and drawn
        # under a polar graticule (matplotlib polar-chart look).
        if not theta or not r:
            return _NO_DATA
        t = ds.df[theta].to_numpy()
        radius = np.abs(ds.df[r].to_numpy())
        rad = np.deg2rad(t)
        cols = {"x": radius * np.cos(rad), "y": radius * np.sin(rad), theta: t, r: radius}
        if z:
            cols[z] = ds.df[z].to_numpy()
        frame = pd.DataFrame(cols).dropna()
        if frame.empty:
            return _NO_VALUES
        r_max = float(frame["x"].pow(2).add(frame["y"].pow(2)).pow(0.5).max()) or 1.0
        points = hv.Points(frame, kdims=["x", "y"], vdims=[theta, r] + ([z] if z else []))
        theta_label = ds.display_names.get(theta, theta)
        r_label = ds.display_names.get(r, r)
        title = f"{ds.name} (polar: θ={theta_label}, r=|{r_label}|)"
        grid = _polar_graticule(r_max)
        lim = r_max * 1.12
        if use_datashader:
            if z:
                # rasterize aggregates the FIRST vdim, so shade dedicated points with
                # vdims=[z] (the main `points` carries θ/r for hover in vector mode).
                shade_points = hv.Points(frame, kdims=["x", "y"], vdims=[z])
                layer = rasterize(shade_points, aggregator="mean")
                metric = f"mean {ds.display_names.get(z, z)}"
            else:
                layer = rasterize(points)
                metric = "point density"
            shaded = layer.opts(
                width=_PLOT_WIDTH,
                height=_PLOT_HEIGHT,
                cmap="viridis",
                colorbar=True,
                aspect="equal",
                xlim=(-lim, lim),
                ylim=(-lim, lim),
                xaxis=None,
                yaxis=None,
                show_grid=False,
                title=f"{title} [{metric}]",
            )
            return shaded * grid
        opts: dict[str, object] = {
            "width": _PLOT_WIDTH,
            "height": _PLOT_HEIGHT,
            "size": 2,
            "alpha": 0.6,
            "tools": ["hover"],
            "aspect": "equal",
            "xlim": (-lim, lim),
            "ylim": (-lim, lim),
            "xaxis": None,
            "yaxis": None,
            "show_grid": False,
            "title": title,
        }
        if z:
            opts |= {"color": z, "cmap": "viridis", "colorbar": True}
        else:
            opts["color"] = "steelblue"
        return points.opts(**opts) * grid

    def _render_banner(
        self, plot_type: str, file_name: str | None, use_datashader: bool
    ) -> pn.pane.Alert | None:
        if not file_name or use_datashader or plot_type in ("histogram", "density1d"):
            return None
        n = self.state.datasets[file_name].n_rows
        if n <= VECTOR_WARN_ROWS:
            return None
        return pn.pane.Alert(
            f"Rendering {n:,} points as vectors — consider enabling Datashader.",
            alert_type="warning",
        )

    def layout(self) -> pn.Column:
        # Grouped rows instead of one wide row: avoids horizontal scrolling as the
        # control set grows (M3 feedback).
        controls = pn.Column(
            pn.Row(self.type_sel, self.file_sel, sizing_mode="stretch_width"),
            pn.Row(
                self.x_sel,
                self.y_sel,
                self.z_sel,
                self.theta_sel,
                self.r_sel,
                self.agg_sel,
                sizing_mode="stretch_width",
            ),
            pn.Row(
                self.bins_slider,
                self.log_y,
                self.kde_overlay,
                self.ds_toggle,
                sizing_mode="stretch_width",
            ),
        )
        return pn.Column(controls, self.banner, self.view, sizing_mode="stretch_width")
