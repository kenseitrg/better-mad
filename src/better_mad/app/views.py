"""Plot tab orchestrator and sidebar panes (M4).

PlotTab owns an ordered list of :class:`~better_mad.app.controls.LayerRow`s plus
plot-level :class:`~better_mad.app.controls.PlotControls`; rendering is delegated
to :mod:`better_mad.app.layers`. See design §4.2-4.5 and UX §5-§7.
"""

from __future__ import annotations

from collections.abc import Callable

import panel as pn

from better_mad.app.controls import LayerRow, PlotControls
from better_mad.app.layers import (  # re-exported for tests and server
    LINE_DECIMATE_ROWS,
    VECTOR_WARN_ROWS,
    LayerSpec,
    PlotSpec,
    apply_plot_opts,
    has_xy_layers,
    render_plot,
    vector_warning,
)
from better_mad.app.state import AppState
from better_mad.core.composition import compatible_types

__all__ = [
    "LINE_DECIMATE_ROWS",
    "VECTOR_WARN_ROWS",
    "PlotTab",
    "dataset_pane",
    "failures_pane",
]


def dataset_pane(name: str, state: AppState) -> pn.pane.Markdown:
    """Sidebar summary of one loaded dataset."""
    ds = state.datasets[name]
    cols = "\n".join(f"- {ds.display_names.get(c, c)}" for c in ds.columns)
    return pn.pane.Markdown(
        f"**{name}**\n\n"
        f"{ds.n_rows:,} rows, {len(ds.columns)} cols, "
        f"loaded in {ds.load_time_s:.2f}s{' (cache)' if ds.from_cache else ''}\n\n"
        f"{cols}",
        sizing_mode="stretch_width",
    )


def failures_pane(state: AppState) -> pn.pane.Alert | None:
    if not state.failures:
        return None
    text = "\n".join(f"- {f.path}: {f.error}" for f in state.failures)
    return pn.pane.Alert(f"**Failed to load:**\n{text}", alert_type="danger")


class PlotTab:
    """One plot tab: layer list, plot options, live composite view.

    Headless-constructible; the Panel layout is only assembled in :meth:`layout`.
    """

    def __init__(
        self,
        state: AppState,
        index: int,
        spec: PlotSpec | None = None,
        on_duplicate: Callable[[PlotSpec], None] | None = None,
    ):
        self.state = state
        self.index = index
        #: Called with a PlotSpec when the user clicks "Duplicate"; the server
        #: turns it into a new tab (duplicate-and-swap workflow, design §4.5).
        self.on_duplicate = on_duplicate
        names = list(state.datasets)
        if spec is None:
            spec = PlotSpec(layers=[LayerSpec(file=names[0] if names else None)])
        self.controls = PlotControls(spec, self._update)
        self.rows: list[LayerRow] = [
            LayerRow(state, layer_spec, self._update)
            for layer_spec in (spec.layers or [LayerSpec(file=names[0] if names else None)])
        ]
        self.layers_area = pn.Column(sizing_mode="stretch_width")
        self.banner_area = pn.Column(sizing_mode="stretch_width")
        #: Stretching container: the HoloViews pane inside tracks its size and
        #: the figure follows via the bokeh sizing-policy hook (layers.py).
        self.plot_area = pn.Column(sizing_mode="stretch_both")

        self.add_layer_btn = pn.widgets.Button(label="+ Add layer", width=100)
        self.add_layer_btn.on_click(self._on_add_layer)
        # Duplicate-and-swap target file (design §4.5 / UX §7).
        self.dup_file_sel = pn.widgets.Select(
            label="Swap file",
            options={"(keep same)": "", **{n: n for n in names}},
            value="",
            sizing_mode="stretch_width",
        )
        self.dup_btn = pn.widgets.Button(label="⧉ Duplicate", width=100)
        self.dup_btn.on_click(self._duplicate_clicked)
        self._settings_col: pn.Column | None = None
        self._plot_col: pn.Column | None = None

        self._updating = False
        self._dirty = False
        self._rebuild_layers()
        self._update()

    # --- layer management ------------------------------------------------------

    def _on_add_layer(self, _event: object) -> None:
        self.add_layer()

    def add_layer(self, layer_spec: LayerSpec | None = None) -> LayerRow:
        if layer_spec is None:
            file_name = (
                self.rows[0].file_sel.value
                if self.rows
                else (list(self.state.datasets)[:1] or [None])[0]
            )
            layer_spec = LayerSpec(file=file_name)
        row = LayerRow(self.state, layer_spec, self._update)
        self.rows.append(row)
        self._rebuild_layers()
        self._update()
        return row

    def remove_layer(self, row: LayerRow) -> None:
        if len(self.rows) <= 1:
            return  # a plot keeps at least one layer
        self.rows.remove(row)
        self._rebuild_layers()
        self._update()

    def move_layer(self, row: LayerRow, delta: int) -> None:
        i = self.rows.index(row)
        j = i + delta
        if not 0 <= j < len(self.rows):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        self._rebuild_layers()
        self._update()

    def _rebuild_layers(self) -> None:
        blocks = []
        for i, row in enumerate(self.rows):
            up = pn.widgets.Button(label="↑", width=28)
            up.on_click(lambda _e, r=row: self.move_layer(r, -1))
            down = pn.widgets.Button(label="↓", width=28)
            down.on_click(lambda _e, r=row: self.move_layer(r, 1))
            rm = pn.widgets.Button(label="✕", color="danger", width=28)
            rm.on_click(lambda _e, r=row: self.remove_layer(r))
            actions = pn.Row(
                pn.pane.Markdown(f"**Layer {i + 1}**", margin=(4, 2, 0, 2)),
                pn.Spacer(),
                up,
                down,
                rm,
                sizing_mode="stretch_width",
            )
            blocks.append(row.layout(actions))
        self.layers_area[:] = blocks

    def _duplicate_clicked(self, _event: object) -> None:
        if not self.on_duplicate:
            return
        spec = self.to_spec()
        if swap := self.dup_file_sel.value:
            for layer in spec.layers:
                layer.file = swap
        self.on_duplicate(spec)

    # --- live update -----------------------------------------------------------

    def _update(self, _event: object = None) -> None:
        if self._updating:  # widget changes during a rebuild coalesce
            self._dirty = True
            return
        self._updating = True
        try:
            self._sync_composition()
            spec = self.to_spec()
            try:
                self.plot_area[:] = [pn.panel(self.view(), sizing_mode="stretch_both")]
                self.banner_area[:] = self._banner_panes(spec)
            except Exception as exc:  # degrade to a banner, never crash (UX §14)
                self.banner_area[:] = [pn.pane.Alert(f"Plot failed: {exc}", alert_type="danger")]
        finally:
            self._updating = False
        if self._dirty:
            self._dirty = False
            self._update()

    def _sync_composition(self) -> None:
        """Restrict each layer's type picker to composition-valid types (UX §5)."""
        for i, row in enumerate(self.rows):
            others = [r.type_sel.value for j, r in enumerate(self.rows) if j != i]
            row.set_type_options(compatible_types(row.type_sel.value, others))

    def _banner_panes(self, spec: PlotSpec) -> list:
        panes: list = []
        if msg := vector_warning(self.state.datasets, spec):
            panes.append(pn.pane.Alert(msg, alert_type="warning"))
        if spec.color_locked and (spec.clim_min is None or spec.clim_max is None):
            panes.append(
                pn.pane.Alert(
                    "Color scale is locked — set both locked min and max to apply it.",
                    alert_type="info",
                )
            )
        return panes

    # --- spec round-trip and rendering ------------------------------------------

    def to_spec(self) -> PlotSpec:
        """Full serializable state of this plot (M6 JSON starts here)."""
        return PlotSpec(
            layers=[row.to_spec() for row in self.rows],
            **self.controls.plot_spec_fields(),
        )

    def view(self):
        """Current composite HoloViews object (or placeholder Markdown)."""
        spec = self.to_spec()
        element = render_plot(self.state.datasets, spec)
        return apply_plot_opts(element, spec, has_xy_layers(spec))

    def banner(self) -> pn.pane.Alert | None:
        """The vector-render warning banner, if any (UX §8)."""
        if msg := vector_warning(self.state.datasets, self.to_spec()):
            return pn.pane.Alert(msg, alert_type="warning")
        return None

    # --- layout ------------------------------------------------------------------

    def plot_layout(self) -> pn.Column:
        """Center pane: banner + plot view, stretching to fill the workspace."""
        if self._plot_col is None:
            self._plot_col = pn.Column(self.banner_area, self.plot_area, sizing_mode="stretch_both")
        return self._plot_col

    def settings_layout(self) -> pn.Column:
        """Right-drawer content: layer management + plot/layer options (UX §1/§6)."""
        if self._settings_col is None:
            toolbar = pn.Row(
                self.add_layer_btn, self.dup_file_sel, self.dup_btn, sizing_mode="stretch_width"
            )
            self._settings_col = pn.Column(
                pn.pane.Markdown(
                    f"**Plot {self.index}** — layers & options", sizing_mode="stretch_width"
                ),
                toolbar,
                self.controls.layout(),
                self.layers_area,
                sizing_mode="stretch_width",
            )
        return self._settings_col

    def layout(self) -> pn.Column:
        """Combined settings + plot layout (headless tests; the served UI splits
        them between the center workspace and the right drawer)."""
        # Grouped rows, never one wide row: no horizontal scrolling (M3 feedback).
        return pn.Column(
            self.settings_layout(),
            self.plot_layout(),
            sizing_mode="stretch_width",
        )
