"""Widget construction and synchronization (M4).

Two building blocks:

- :class:`LayerRow` — all widgets for one layer (file/type/columns/options/style),
  synced to/from :class:`~better_mad.app.layers.LayerSpec`.
- :class:`PlotControls` — plot-level style widgets (labels, limits, log axes,
  legend, equal aspect, locked color scale), synced to
  :class:`~better_mad.app.layers.PlotSpec`.

Panel gotcha (AGENTS.md): plain classes have no `.param`, so every widget change
goes through an explicit `on_change` callback — no string-form `pn.depends`.
"""

from __future__ import annotations

from collections.abc import Callable

import panel as pn

from better_mad.app.layers import VECTOR_WARN_ROWS, LayerSpec, PlotSpec
from better_mad.app.state import AppState
from better_mad.core.composition import PLOT_TYPE_LABELS
from better_mad.core.styles import COLORMAPS, SYMBOLS, LayerStyle

#: Panel Select treats dicts as {display label: value}; PLOT_TYPE_LABELS is
#: {internal key: label}, so invert for widgets. The type selector's *value*
#: must always be an internal key — family() depends on it.
_TYPE_OPTIONS = {label: key for key, label in PLOT_TYPE_LABELS.items()}

#: Widget options presented as "(none)".
_NONE = "(none)"


def _req(value, default):
    """Panel widget values are typed `X | None`; fall back to a sane default."""
    return default if value is None else value


def _watch(widgets: list, callback: Callable[[object], None]) -> None:
    for w in widgets:
        w.param.watch(callback, "value")


class LayerRow:
    """Widgets for one layer, initialized from (and readable back into) a LayerSpec."""

    def __init__(self, state: AppState, spec: LayerSpec, on_change: Callable[[object], None]):
        self.state = state
        self.on_change = on_change
        names = list(state.datasets)

        self.visible_cb = pn.widgets.Checkbox(label="Show", value=spec.visible, width=60)
        self.file_sel = pn.widgets.Select(
            label="File",
            options=names,
            value=spec.file if spec.file in names else (names[0] if names else None),
        )
        self.type_sel = pn.widgets.Select(
            label="Type",
            options=_TYPE_OPTIONS,
            value=spec.plot_type,
        )
        self.x_sel = pn.widgets.Select(label="X column")
        self.y_sel = pn.widgets.Select(label="Y column")
        self.z_sel = pn.widgets.Select(label="Color (z)")
        self.theta_sel = pn.widgets.Select(label="θ column (degrees)")
        self.r_sel = pn.widgets.Select(label="r column (|value| used)")
        self.agg_sel = pn.widgets.Select(
            label="Aggregation", options={"count": "count", "mean of z": "mean"}
        )
        self.bins_slider = pn.widgets.IntSlider(
            label="Bins", start=5, end=200, step=5, value=spec.bins
        )
        self.log_y_cb = pn.widgets.Checkbox(label="Log y", value=spec.log_y)
        self.kde_cb = pn.widgets.Checkbox(label="KDE overlay", value=spec.kde_overlay)
        self.ds_toggle = pn.widgets.Checkbox(label="Datashader", value=spec.use_datashader)

        s = spec.style
        self.color_sel = pn.widgets.ColorPicker(label="Color", value=s.color)
        self.cmap_sel = pn.widgets.Select(label="Colormap", options=COLORMAPS, value=s.cmap)
        self.alpha_slider = pn.widgets.FloatSlider(
            label="Opacity", start=0.05, end=1.0, step=0.05, value=s.alpha
        )
        self.size_slider = pn.widgets.FloatSlider(
            label="Size", start=0.5, end=10.0, step=0.5, value=s.size
        )
        self.symbol_sel = pn.widgets.Select(label="Symbol", options=SYMBOLS, value=s.symbol)
        self.linewidth_slider = pn.widgets.FloatSlider(
            label="Line width", start=0.5, end=6.0, step=0.5, value=s.line_width
        )
        lo, hi = s.clip_percentiles if s.clip_percentiles else (0.0, 100.0)
        self.clip_lo = pn.widgets.FloatInput(label="Clip % low", value=lo, start=0, end=100)
        self.clip_hi = pn.widgets.FloatInput(label="Clip % high", value=hi, start=0, end=100)
        self.clim_min = pn.widgets.FloatInput(label="Color min (explicit)", value=s.clim_min)
        self.clim_max = pn.widgets.FloatInput(label="Color max (explicit)", value=s.clim_max)
        self.log_color_cb = pn.widgets.Checkbox(label="Log color scale", value=s.log_color)

        # Seed picker values from the spec (duplicate/session restore); the
        # sync below keeps them when valid for the selected file.
        self.x_sel.value = spec.x
        self.y_sel.value = spec.y
        self.z_sel.value = spec.z
        self.theta_sel.value = spec.theta
        self.r_sel.value = spec.r
        self.agg_sel.value = spec.aggregation

        self._sync_columns(
            keep_selection=True,
            initial={
                self.x_sel: spec.x,
                self.y_sel: spec.y,
                self.z_sel: spec.z,
                self.theta_sel: spec.theta,
                self.r_sel: spec.r,
            },
        )
        self._sync_visibility()

        _watch(
            [
                self.visible_cb,
                self.file_sel,
                self.type_sel,
                self.x_sel,
                self.y_sel,
                self.z_sel,
                self.theta_sel,
                self.r_sel,
                self.agg_sel,
                self.bins_slider,
                self.log_y_cb,
                self.kde_cb,
                self.ds_toggle,
                self.color_sel,
                self.cmap_sel,
                self.alpha_slider,
                self.size_slider,
                self.symbol_sel,
                self.linewidth_slider,
                self.clip_lo,
                self.clip_hi,
                self.clim_min,
                self.clim_max,
                self.log_color_cb,
            ],
            self._changed,
        )

    # --- widget bookkeeping -------------------------------------------------

    def _changed(self, event: object) -> None:
        if event is not None and getattr(event, "obj", None) is self.file_sel:  # type: ignore[union-attr]
            self._sync_columns(keep_selection=True)
        elif event is not None and getattr(event, "obj", None) is self.type_sel:  # type: ignore[union-attr]
            self._sync_visibility()
        self.on_change(event)

    def _sync_columns(self, *, keep_selection: bool, initial: dict | None = None) -> None:
        """Fill column pickers from the selected file.

        With ``keep_selection`` existing choices survive a file swap when the new
        file has the same column — the basis of the duplicate-and-swap comparison
        workflow (design §4.5). ``initial`` seeds first-time values from a spec.
        """
        if not self.file_sel.value:
            for w in (self.x_sel, self.y_sel, self.z_sel, self.theta_sel, self.r_sel):
                w.options = {}
            return
        options = dict(self.state.column_options(self.file_sel.value))
        prev = {
            w: (initial or {}).get(w, w.value)
            for w in (self.x_sel, self.y_sel, self.z_sel, self.theta_sel, self.r_sel)
        }
        self.x_sel.options = dict(options)
        self.y_sel.options = dict(options)
        self.theta_sel.options = dict(options)
        self.r_sel.options = dict(options)
        self.z_sel.options = {_NONE: "", **options}
        cols = list(options.values())
        defaults = {
            self.x_sel: cols[0] if cols else None,
            self.y_sel: cols[1] if len(cols) > 1 else None,
            self.theta_sel: cols[0] if cols else None,
            self.r_sel: cols[1] if len(cols) > 1 else None,
            self.z_sel: "",
        }
        for w, default in defaults.items():
            kept = prev.get(w) if keep_selection else None
            valid = set(options.values()) | ({""} if w is self.z_sel else set())
            w.value = kept if kept in valid else default
        # Sensible default: datashader for large files (UX §8).
        if (
            self.state.datasets[self.file_sel.value].n_rows > VECTOR_WARN_ROWS
            and not self.ds_toggle.value
        ):
            self.ds_toggle.value = True

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
        self.log_y_cb.visible = t == "histogram"
        self.kde_cb.visible = t == "histogram"
        self.ds_toggle.visible = t in ("scatter", "density2d", "line", "polar")
        marker_styling = t in ("scatter", "polar")
        self.size_slider.visible = marker_styling
        self.symbol_sel.visible = marker_styling
        self.linewidth_slider.visible = t == "line"
        if t == "density2d":
            self.ds_toggle.value = True

    def set_type_options(self, allowed: list[str]) -> None:
        """Restrict the type picker to composition-valid types (UX §5, design §4.2)."""
        options = {label: key for key, label in PLOT_TYPE_LABELS.items() if key in allowed}
        if self.type_sel.value not in allowed:
            self.type_sel.options = options
            self.type_sel.value = allowed[0]
        else:
            self.type_sel.options = options

    # --- spec round-trip ------------------------------------------------------

    def to_spec(self) -> LayerSpec:
        clip_lo = _req(self.clip_lo.value, 0.0)
        clip_hi = _req(self.clip_hi.value, 100.0)
        clip = None if (clip_lo, clip_hi) == (0.0, 100.0) else (clip_lo, clip_hi)
        return LayerSpec(
            file=self.file_sel.value,
            plot_type=_req(self.type_sel.value, "scatter"),
            x=self.x_sel.value,
            y=self.y_sel.value,
            z=self.z_sel.value or "",
            theta=self.theta_sel.value,
            r=self.r_sel.value,
            aggregation=_req(self.agg_sel.value, "count"),
            bins=_req(self.bins_slider.value, 50),
            log_y=self.log_y_cb.value,
            kde_overlay=self.kde_cb.value,
            use_datashader=self.ds_toggle.value,
            visible=self.visible_cb.value,
            style=LayerStyle(
                color=_req(self.color_sel.value, "steelblue"),
                cmap=_req(self.cmap_sel.value, "viridis"),
                alpha=_req(self.alpha_slider.value, 0.6),
                size=_req(self.size_slider.value, 2.0),
                symbol=_req(self.symbol_sel.value, "circle"),
                line_width=_req(self.linewidth_slider.value, 1.0),
                clip_percentiles=clip,
                clim_min=self.clim_min.value,
                clim_max=self.clim_max.value,
                log_color=self.log_color_cb.value,
            ),
        )

    # --- layout ---------------------------------------------------------------

    def layout(self, actions: pn.Row) -> pn.Column:
        """One layer block for the narrow right drawer (~360px): at most two
        widgets per row, style card collapsed by default (UX §6)."""
        style_card = pn.Card(
            pn.Row(self.color_sel, self.cmap_sel, sizing_mode="stretch_width"),
            pn.Row(self.alpha_slider, self.size_slider, sizing_mode="stretch_width"),
            pn.Row(self.symbol_sel, self.linewidth_slider, sizing_mode="stretch_width"),
            pn.Row(self.clip_lo, self.clip_hi, sizing_mode="stretch_width"),
            pn.Row(self.clim_min, self.clim_max, sizing_mode="stretch_width"),
            pn.Row(self.log_color_cb, sizing_mode="stretch_width"),
            title="Layer style",
            collapsed=True,
            collapsible=True,
            sizing_mode="stretch_width",
        )
        return pn.Column(
            pn.Row(self.visible_cb, actions, sizing_mode="stretch_width"),
            pn.Row(self.file_sel, self.type_sel, sizing_mode="stretch_width"),
            pn.Row(self.x_sel, self.y_sel, sizing_mode="stretch_width"),
            # visibility toggles decide which of these pairs actually shows
            pn.Row(
                self.z_sel, self.agg_sel, self.theta_sel, self.r_sel, sizing_mode="stretch_width"
            ),
            pn.Row(self.bins_slider, sizing_mode="stretch_width"),
            pn.Row(self.log_y_cb, self.kde_cb, self.ds_toggle, sizing_mode="stretch_width"),
            style_card,
            sizing_mode="stretch_width",
        )


class PlotControls:
    """Plot-level style widgets (design §4.3/§4.4); collapsed by default."""

    def __init__(self, spec: PlotSpec, on_change: Callable[[object], None]):
        self.on_change = on_change
        self._card: pn.Card | None = None
        self.title_input = pn.widgets.TextInput(label="Title", value=spec.title)
        self.x_label = pn.widgets.TextInput(label="X label", value=spec.x_label)
        self.y_label = pn.widgets.TextInput(label="Y label", value=spec.y_label)
        self.x_min = pn.widgets.FloatInput(label="X min", value=spec.xlim[0] if spec.xlim else None)
        self.x_max = pn.widgets.FloatInput(label="X max", value=spec.xlim[1] if spec.xlim else None)
        self.y_min = pn.widgets.FloatInput(label="Y min", value=spec.ylim[0] if spec.ylim else None)
        self.y_max = pn.widgets.FloatInput(label="Y max", value=spec.ylim[1] if spec.ylim else None)
        self.log_x_cb = pn.widgets.Checkbox(label="Log x", value=spec.log_x)
        self.log_y_cb = pn.widgets.Checkbox(label="Log y", value=spec.log_y)
        self.legend_cb = pn.widgets.Checkbox(label="Legend", value=spec.legend)
        self.legend_pos = pn.widgets.Select(
            label="Legend position",
            options=["right", "left", "top", "bottom", "top_right"],
            value=spec.legend_position,
        )
        self.equal_aspect_cb = pn.widgets.Checkbox(
            label="Equal aspect (maps)", value=spec.equal_aspect
        )
        self.lock_color_cb = pn.widgets.Checkbox(label="Lock color scale", value=spec.color_locked)
        self.clim_min = pn.widgets.FloatInput(label="Locked color min", value=spec.clim_min)
        self.clim_max = pn.widgets.FloatInput(label="Locked color max", value=spec.clim_max)

        _watch(
            [
                self.title_input,
                self.x_label,
                self.y_label,
                self.x_min,
                self.x_max,
                self.y_min,
                self.y_max,
                self.log_x_cb,
                self.log_y_cb,
                self.legend_cb,
                self.legend_pos,
                self.equal_aspect_cb,
                self.lock_color_cb,
                self.clim_min,
                self.clim_max,
            ],
            self._changed,
        )

    def _changed(self, event: object) -> None:
        self.on_change(event)

    def plot_spec_fields(self) -> dict:
        """Current plot-level fields for PlotSpec (layers filled in by PlotTab)."""
        xlim = ylim = None
        if self.x_min.value is not None and self.x_max.value is not None:
            xlim = (self.x_min.value, self.x_max.value)
        if self.y_min.value is not None and self.y_max.value is not None:
            ylim = (self.y_min.value, self.y_max.value)
        return {
            "title": self.title_input.value or "",
            "x_label": self.x_label.value or "",
            "y_label": self.y_label.value or "",
            "xlim": xlim,
            "ylim": ylim,
            "log_x": self.log_x_cb.value,
            "log_y": self.log_y_cb.value,
            "legend": self.legend_cb.value,
            "legend_position": self.legend_pos.value,
            "equal_aspect": self.equal_aspect_cb.value,
            "color_locked": self.lock_color_cb.value,
            "clim_min": self.clim_min.value,
            "clim_max": self.clim_max.value,
        }

    def layout(self) -> pn.Card:
        """Collapsed-by-default card sized for the narrow right drawer."""
        if self._card is None:
            self._card = pn.Card(
                pn.Row(self.title_input, sizing_mode="stretch_width"),
                pn.Row(self.x_label, self.y_label, sizing_mode="stretch_width"),
                pn.Row(self.x_min, self.x_max, sizing_mode="stretch_width"),
                pn.Row(self.y_min, self.y_max, sizing_mode="stretch_width"),
                pn.Row(
                    self.log_x_cb, self.log_y_cb, self.equal_aspect_cb, sizing_mode="stretch_width"
                ),
                pn.Row(self.legend_cb, self.legend_pos, sizing_mode="stretch_width"),
                pn.Row(self.lock_color_cb, sizing_mode="stretch_width"),
                pn.Row(self.clim_min, self.clim_max, sizing_mode="stretch_width"),
                title="Plot options",
                collapsed=True,
                collapsible=True,
                sizing_mode="stretch_width",
            )
        return self._card
