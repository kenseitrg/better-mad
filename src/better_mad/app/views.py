"""UI building blocks: dataset sidebar panes and plot tabs (M2)."""

from __future__ import annotations

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
        self.ds_toggle = pn.widgets.Checkbox(label="Datashader")
        self._sync_columns()
        self.file_sel.param.watch(self._on_file_change, "value")

    def _on_file_change(self, _event: object) -> None:
        self._sync_columns()

    def _sync_columns(self) -> None:
        if not self.file_sel.value:
            self.x_sel.options = {}
            self.y_sel.options = {}
            return
        options = self.state.column_options(self.file_sel.value)
        self.x_sel.options = dict(options)
        self.y_sel.options = dict(options)
        cols = list(options.values())
        self.x_sel.value = cols[0] if cols else None
        self.y_sel.value = cols[1] if len(cols) > 1 else None
        # Sensible default: datashader for large files (UX §8).
        self.ds_toggle.value = self.state.datasets[self.file_sel.value].n_rows > VECTOR_WARN_ROWS

    @pn.depends("x_sel.value", "y_sel.value", "ds_toggle.value", "file_sel.value")
    def view(self) -> hv.Element | pn.pane.Markdown:
        if not self.file_sel.value or not self.x_sel.value or not self.y_sel.value:
            return pn.pane.Markdown("*Load a file and add a plot to get started.*")
        ds = self.state.datasets[self.file_sel.value]
        x, y = self.x_sel.value, self.y_sel.value
        xlabel = ds.display_names.get(x, x)
        ylabel = ds.display_names.get(y, y)
        points = hv.Points(ds.df, kdims=[x, y])
        if self.ds_toggle.value:
            return rasterize(points).opts(
                width=_PLOT_WIDTH,
                height=_PLOT_HEIGHT,
                cmap="viridis",
                colorbar=True,
                xlabel=xlabel,
                ylabel=ylabel,
                title=f"{ds.name} (datashader: point density)",
            )
        return points.opts(
            width=_PLOT_WIDTH,
            height=_PLOT_HEIGHT,
            size=2,
            alpha=0.6,
            color="steelblue",
            tools=["hover"],
            xlabel=xlabel,
            ylabel=ylabel,
            title=ds.name,
        )

    @pn.depends("ds_toggle.value", "file_sel.value")
    def banner(self) -> pn.pane.Alert | None:
        if not self.file_sel.value or self.ds_toggle.value:
            return None
        n = self.state.datasets[self.file_sel.value].n_rows
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
            self.ds_toggle,
            sizing_mode="stretch_width",
        )
        return pn.Column(controls, self.banner, self.view, sizing_mode="stretch_width")
