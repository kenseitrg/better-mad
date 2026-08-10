"""UI building blocks: dataset sidebar panes and plot tabs (M2)."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import holoviews as hv
import panel as pn
from holoviews.operation.datashader import rasterize

from better_mad.app.state import AppState

#: Vector rendering above this row count raises the non-blocking warning banner (UX §8).
VECTOR_WARN_ROWS = 100_000

_PLOT_WIDTH = 850
_PLOT_HEIGHT = 650


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


class PlotTab:
    """One scatter plot tab: file + column pickers, datashader toggle, view.

    Headless-constructible; the Panel layout is only assembled in :meth:`layout`.
    """

    def __init__(self, state: AppState, index: int):
        self.state = state
        self.index = index
        names = list(state.datasets)
        self.file_sel = pn.widgets.Select(
            label="File", options=names, value=names[0] if names else None
        )
        self.x_sel = pn.widgets.Select(label="X column")
        self.y_sel = pn.widgets.Select(label="Y column")
        self.z_sel = pn.widgets.Select(label="Color (z)")
        self.ds_toggle = pn.widgets.Checkbox(label="Datashader")
        self._sync_columns()
        self.file_sel.param.watch(self._on_file_change, "value")
        # Explicit binds: PlotTab is a plain class (no .param), so string-form
        # @pn.depends cannot resolve widget parameters here. Widgets supply the
        # arguments, so both are effectively zero-arg callables.
        self.view = cast(
            Callable[[], hv.Element | pn.pane.Markdown],
            pn.bind(
                self._render_view,
                self.file_sel,
                self.x_sel,
                self.y_sel,
                self.z_sel,
                self.ds_toggle,
            ),
        )
        self.banner = cast(
            Callable[[], pn.pane.Alert | None],
            pn.bind(self._render_banner, self.file_sel, self.ds_toggle),
        )

    def _on_file_change(self, _event: object) -> None:
        self._sync_columns()

    def _sync_columns(self) -> None:
        if not self.file_sel.value:
            self.x_sel.options = {}
            self.y_sel.options = {}
            self.z_sel.options = {}
            return
        options = self.state.column_options(self.file_sel.value)
        self.x_sel.options = dict(options)
        self.y_sel.options = dict(options)
        self.z_sel.options = {"(none)": "", **options}
        cols = list(options.values())
        self.x_sel.value = cols[0] if cols else None
        self.y_sel.value = cols[1] if len(cols) > 1 else None
        self.z_sel.value = ""
        # Sensible default: datashader for large files (UX §8).
        self.ds_toggle.value = self.state.datasets[self.file_sel.value].n_rows > VECTOR_WARN_ROWS

    def _render_view(
        self,
        file_name: str | None,
        x: str | None,
        y: str | None,
        z: str,
        use_datashader: bool,
    ) -> hv.Element | pn.pane.Markdown:
        if not file_name or not x or not y:
            return pn.pane.Markdown("*Load a file and add a plot to get started.*")
        ds = self.state.datasets[file_name]
        xlabel = ds.display_names.get(x, x)
        ylabel = ds.display_names.get(y, y)
        zlabel = ds.display_names.get(z, z) if z else None
        points = hv.Points(ds.df, kdims=[x, y], vdims=[z] if z else [])
        if use_datashader:
            # Count density without z; mean of z when a color column is selected.
            if z and zlabel:
                layer = rasterize(points, column=z, aggregator="mean")
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

    def _render_banner(self, file_name: str | None, use_datashader: bool) -> pn.pane.Alert | None:
        if not file_name or use_datashader:
            return None
        n = self.state.datasets[file_name].n_rows
        if n <= VECTOR_WARN_ROWS:
            return None
        return pn.pane.Alert(
            f"Rendering {n:,} points as vectors — consider enabling Datashader.",
            alert_type="warning",
        )

    def layout(self) -> pn.Column:
        controls = pn.Row(
            self.file_sel,
            self.x_sel,
            self.y_sel,
            self.z_sel,
            self.ds_toggle,
            sizing_mode="stretch_width",
        )
        return pn.Column(controls, self.banner, self.view, sizing_mode="stretch_width")
