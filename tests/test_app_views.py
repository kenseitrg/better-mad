"""Headless tests for app state, plot tabs, layers, and M4 workflows."""

from pathlib import Path
from typing import cast

import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
import pytest

from better_mad.app.controls import LayerRow
from better_mad.app.state import AppState
from better_mad.app.views import LINE_DECIMATE_ROWS, VECTOR_WARN_ROWS, PlotTab
from better_mad.core.dataset import Dataset
from better_mad.core.loading import ParserSettings

hv.extension("bokeh")

FIXTURES = Path(__file__).parent / "fixtures"
WS = FIXTURES / "sample_ws.txt"
CSV = FIXTURES / "sample_csv_nulls.csv"


def _state(*paths: Path) -> AppState:
    state = AppState()
    state.load_files(list(paths))
    return state


def _synthetic_state(name: str, df: pd.DataFrame) -> AppState:
    df.attrs["display_names"] = {c: c for c in df.columns}
    ds = Dataset(
        name=name,
        path=Path(f"/synthetic/{name}"),
        df=df,
        settings=ParserSettings(),
        load_time_s=0.0,
        from_cache=False,
    )
    return AppState(datasets={name: ds})


class TestAppState:
    def test_loads_files(self) -> None:
        state = _state(WS, CSV)
        assert list(state.datasets) == ["sample_ws", "sample_csv_nulls"]
        assert state.datasets["sample_ws"].n_rows == 1000

    def test_missing_file_recorded_not_raised(self) -> None:
        state = _state(WS, Path("/nonexistent/file"))
        assert len(state.datasets) == 1
        assert len(state.failures) == 1
        assert "not found" in state.failures[0].error

    def test_duplicate_stems_get_unique_names(self) -> None:
        state = _state(WS, WS)
        assert list(state.datasets) == ["sample_ws", "sample_ws_2"]

    def test_column_options_use_display_labels(self) -> None:
        state = _state(WS)
        options = state.column_options("sample_ws")
        assert options["TR.DOMFREQ"] == "TR_DOMFREQ"
        assert next(iter(options)) == "CMP"


class TestPlotTab:
    def test_defaults(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        assert row.file_sel.value == "sample_ws"
        assert row.x_sel.value == "CMP"  # first column
        assert row.y_sel.value == "XCORD_MIDPT"  # second column
        assert row.z_sel.value == ""  # no color by default
        assert row.ds_toggle.value is False  # 1000 rows < threshold

    def test_view_vector_and_datashader(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.x_sel.value = "TR_DOMFREQ"
        row.y_sel.value = "TR_RMSAMP"

        row.ds_toggle.value = False
        element = tab.view()
        assert isinstance(element, hv.Points)

        row.ds_toggle.value = True
        shaded = tab.view()
        assert isinstance(shaded, hv.DynamicMap)  # dynamic: re-aggregates on pan/zoom

    def test_color_scatter(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        row.z_sel.value = "TR_DOMFREQ"

        row.ds_toggle.value = False
        element = tab.view()
        assert isinstance(element, hv.Points)
        assert "TR_DOMFREQ" in [d.name for d in element.vdims]  # hover includes z

        row.ds_toggle.value = True
        shaded = tab.view()
        assert isinstance(shaded, hv.DynamicMap)  # mean aggregation over z

    def test_file_change_keeps_common_columns(self) -> None:
        tab = PlotTab(_state(WS, CSV), 1)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.file_sel.value = "sample_csv_nulls"
        assert "TR_HILBSTATIC" in row.x_sel.options.values()
        # comparison workflow: same-schema columns survive a file swap (design §4.5)
        assert row.x_sel.value == "XCORD_MIDPT"

    def test_warn_banner_only_for_large_vector(self, monkeypatch) -> None:
        tab = PlotTab(_state(WS), 1)
        assert tab.banner() is None  # small file

        monkeypatch.setattr(Dataset, "n_rows", property(lambda self: VECTOR_WARN_ROWS + 1))
        tab2 = PlotTab(_state(WS), 1)
        assert tab2.rows[0].ds_toggle.value is True  # large file defaults to datashader
        tab2.rows[0].ds_toggle.value = False
        assert isinstance(tab2.banner(), pn.pane.Alert)
        tab2.rows[0].ds_toggle.value = True
        assert tab2.banner() is None

    def test_empty_state_view_is_placeholder(self) -> None:
        tab = PlotTab(AppState(), 1)
        assert isinstance(tab.view(), pn.pane.Markdown)

    def test_layout_builds(self) -> None:
        tab = PlotTab(_state(WS), 1)
        layout = tab.layout()
        assert isinstance(layout, pn.Column)


class TestPlotTypes:
    def test_histogram(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.type_sel.value = "histogram"
        row.x_sel.value = "TR_DOMFREQ"
        row.bins_slider.value = 40
        hist = tab.view()
        assert isinstance(hist, hv.Histogram)
        assert len(hist.edges) - 1 == 40

    def test_histogram_kde_overlay(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.type_sel.value = "histogram"
        row.x_sel.value = "TR_DOMFREQ"
        row.kde_cb.value = True
        assert isinstance(tab.view(), hv.Overlay)

    def test_density1d(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.type_sel.value = "density1d"
        row.x_sel.value = "TR_RMSAMP"
        assert isinstance(tab.view(), hv.Curve)

    def test_density2d_count_and_mean(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.type_sel.value = "density2d"
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        assert isinstance(tab.view(), hv.DynamicMap)
        row.z_sel.value = "TR_DOMFREQ"
        row.agg_sel.value = "mean"
        assert isinstance(tab.view(), hv.DynamicMap)

    def test_line_vector(self) -> None:
        tab = PlotTab(_state(FIXTURES / "sample_2d_lines.txt"), 1)
        row = tab.rows[0]
        row.type_sel.value = "line"
        row.x_sel.value = "CMP"
        row.y_sel.value = "TR_RMSAMP"
        row.ds_toggle.value = False
        assert isinstance(tab.view(), hv.Curve)
        row.ds_toggle.value = True
        assert isinstance(tab.view(), hv.DynamicMap)

    def test_line_decimation(self) -> None:
        n = LINE_DECIMATE_ROWS * 3
        df = pd.DataFrame({"x": np.arange(n, dtype=float), "y": np.random.random(n)})
        tab = PlotTab(_synthetic_state("big", df), 1)
        row = tab.rows[0]
        row.type_sel.value = "line"
        row.x_sel.value = "x"
        row.y_sel.value = "y"
        row.ds_toggle.value = False
        curve = tab.view()
        assert isinstance(curve, hv.Curve)
        assert len(curve.data) <= LINE_DECIMATE_ROWS
        assert "decimated" in curve.opts.get().kwargs["title"]

    def test_polar_transform_uses_abs_radius(self) -> None:
        # θ: 0°, 90°, 180°; r: 1, 2, -3 (abs → 3)
        df = pd.DataFrame({"az": [0.0, 90.0, 180.0], "off": [1.0, 2.0, -3.0]})
        tab = PlotTab(_synthetic_state("pol", df), 1)
        row = tab.rows[0]
        row.type_sel.value = "polar"
        row.theta_sel.value = "az"
        row.r_sel.value = "off"
        row.ds_toggle.value = False
        overlay = tab.view()
        assert isinstance(overlay, hv.Overlay)  # points + graticule
        points = next(el for el in overlay if isinstance(el, hv.Points))
        x = points.data["x"].to_numpy()
        y = points.data["y"].to_numpy()
        assert x[0] == np.cos(0) and abs(y[0]) < 1e-9
        assert abs(x[1]) < 1e-9 and y[1] == 2.0
        assert x[2] == -3.0 and abs(y[2]) < 1e-9  # |-3| at 180°
        # original θ/r values kept for hover
        assert "az" in [d.name for d in points.vdims]

    def test_polar_with_z_and_datashader(self) -> None:
        df = pd.DataFrame(
            {
                "az": [0.0, 90.0, 180.0, 270.0],
                "off": [1.0, 2.0, 3.0, 4.0],
                "amp": [5.0, 6.0, 7.0, 8.0],
            }
        )
        tab = PlotTab(_synthetic_state("polz", df), 1)
        row = tab.rows[0]
        row.type_sel.value = "polar"
        row.theta_sel.value = "az"
        row.r_sel.value = "off"
        row.z_sel.value = "amp"
        row.ds_toggle.value = False
        overlay = tab.view()
        points = next(el for el in overlay if isinstance(el, hv.Points))
        assert "amp" in [d.name for d in points.vdims]
        row.ds_toggle.value = True
        shaded = tab.view()
        # DynamicMap absorbs the static graticule overlay when combined
        assert isinstance(shaded, hv.DynamicMap)

    def test_polar_datashader_aggregates_z_not_azimuth(self) -> None:
        # Regression: rasterize ignores column= and aggregates the FIRST vdim;
        # mean-of-z must not fall back to azimuth or point counts.
        rng = np.random.default_rng(0)
        n = 20_000
        df = pd.DataFrame(
            {
                "az": rng.uniform(0, 360, n),
                "off": rng.uniform(1, 10, n),
                "amp": rng.uniform(0, 5, n),
            }
        )
        tab = PlotTab(_synthetic_state("polagg", df), 1)
        row = tab.rows[0]
        row.type_sel.value = "polar"
        row.theta_sel.value = "az"
        row.r_sel.value = "off"
        row.z_sel.value = "amp"
        row.ds_toggle.value = True
        view = cast(hv.DynamicMap, tab.view())
        overlay = cast(hv.Overlay, view[()])
        image = next(el for el in overlay if isinstance(el, hv.Image))
        values = image.dimension_values(2)
        assert np.nanmax(values) <= 5.0  # mean of amp, not azimuth (~360) or counts

    def test_visibility_by_type(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.type_sel.value = "polar"
        assert not row.x_sel.visible
        assert row.theta_sel.visible and row.r_sel.visible and row.z_sel.visible
        row.type_sel.value = "histogram"
        assert row.bins_slider.visible and row.kde_cb.visible
        assert not row.y_sel.visible
        row.type_sel.value = "scatter"
        assert row.x_sel.visible and row.y_sel.visible
        assert not row.theta_sel.visible

    def test_nan_only_column_placeholder(self) -> None:
        df = pd.DataFrame({"a": [np.nan, np.nan], "b": [1.0, 2.0]})
        tab = PlotTab(_synthetic_state("nan", df), 1)
        row = tab.rows[0]
        row.type_sel.value = "histogram"
        row.x_sel.value = "a"
        assert isinstance(tab.view(), pn.pane.Markdown)


class TestLayers:
    """M4 layer manager: add/remove/reorder, cross-file layers, composition."""

    def test_add_remove_move(self) -> None:
        tab = PlotTab(_state(WS), 1)
        assert len(tab.rows) == 1
        row2 = tab.add_layer()
        assert len(tab.rows) == 2
        assert len(tab.layers_area) == 2

        tab.move_layer(row2, -1)
        assert tab.rows[0] is row2
        tab.move_layer(row2, -1)  # already first: no-op
        assert tab.rows[0] is row2

        tab.remove_layer(row2)
        assert len(tab.rows) == 1
        tab.remove_layer(tab.rows[0])
        assert len(tab.rows) == 1  # a plot keeps its last layer

    def test_cross_file_layers_overlay(self) -> None:
        tab = PlotTab(_state(WS, CSV), 1)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        row.ds_toggle.value = False
        row2 = tab.add_layer()
        row2.file_sel.value = "sample_csv_nulls"
        row2.x_sel.value = "XCORD_MIDPT"
        row2.y_sel.value = "YCORD_MIDPT"
        row2.ds_toggle.value = False
        view = tab.view()
        assert isinstance(view, hv.Overlay)
        points = [el for el in view if isinstance(el, hv.Points)]
        assert len(points) == 2
        assert len(points[0].data) == 1000
        assert len(points[1].data) == 751

    def test_layer_hidden_excluded_from_view(self) -> None:
        tab = PlotTab(_state(WS, CSV), 1)
        row2 = tab.add_layer()
        row2.file_sel.value = "sample_csv_nulls"
        row2.visible_cb.value = False
        view = tab.view()
        assert isinstance(view, hv.Points)  # single visible layer, no overlay

    def test_scatter_line_density2d_compose(self) -> None:
        tab = PlotTab(_state(FIXTURES / "sample_2d_lines.txt"), 1)
        row = tab.rows[0]
        row.x_sel.value = "CMP"
        row.y_sel.value = "TR_RMSAMP"
        row.ds_toggle.value = False
        line = tab.add_layer()
        line.type_sel.value = "line"
        line.x_sel.value = "CMP"
        line.y_sel.value = "TR_DOMFREQ"
        line.ds_toggle.value = False
        view = tab.view()
        assert isinstance(view, hv.Overlay)
        assert sum(isinstance(el, hv.Points) for el in view) == 1
        assert sum(isinstance(el, hv.Curve) for el in view) == 1

    def test_histogram_and_density1d_compose(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.type_sel.value = "histogram"
        row.x_sel.value = "TR_DOMFREQ"
        density = tab.add_layer()
        density.type_sel.value = "density1d"
        density.x_sel.value = "TR_DOMFREQ"
        view = tab.view()
        assert isinstance(view, hv.Overlay)
        assert any(isinstance(el, hv.Histogram) for el in view)
        curves = [el for el in view if isinstance(el, hv.Curve)]
        assert curves
        # shared normalization: density scaled to histogram counts (design §4.2)
        hist = next(el for el in view if isinstance(el, hv.Histogram))
        assert curves[0].dimension_values(1).max() <= hist.dimension_values(1).max() * 1.5

    def test_type_select_values_are_internal_keys(self) -> None:
        # Regression: Panel Select treats dict options as {display: value}; the
        # inverted mapping leaked 'Scatter' labels into .value and crashed
        # family() once the browser round-tripped the widget.
        from better_mad.core.composition import PLOT_TYPE_LABELS

        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        assert row.type_sel.options["Scatter"] == "scatter"
        assert row.type_sel.value == "scatter"
        row2 = tab.add_layer()
        assert row2.type_sel.value in PLOT_TYPE_LABELS
        row.type_sel.value = "line"
        assert row.type_sel.value == "line"
        assert tab.rows[0].type_sel.value in PLOT_TYPE_LABELS

    def test_type_options_restricted_to_family(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row2 = tab.add_layer()
        # first layer is scatter → second restricted to the xy family (UX §5)
        assert set(row2.type_sel.options.values()) == {"scatter", "line", "density2d"}
        # single-layer plots keep all types
        tab2 = PlotTab(_state(WS), 1)
        assert len(tab2.rows[0].type_sel.options) == 6

    def test_invalid_type_switch_snaps_back(self) -> None:
        tab = PlotTab(_state(WS), 1)
        tab.add_layer()
        row = tab.rows[0]
        row.type_sel.value = "histogram"  # invalid: other layer is scatter
        assert row.type_sel.value == "scatter"  # snapped back to a valid type

    def test_layer_style_opts(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        row.ds_toggle.value = False
        row.size_slider.value = 5.0
        row.alpha_slider.value = 0.25
        row.symbol_sel.value = "square"
        kwargs = tab.view().opts.get().kwargs
        assert kwargs["size"] == 5.0
        assert kwargs["alpha"] == 0.25
        assert kwargs["marker"] == "square"

    def test_plot_level_opts(self) -> None:
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        row.ds_toggle.value = False
        c = tab.controls
        c.title_input.value = "my plot"
        c.x_label.value = "easting"
        c.y_label.value = "northing"
        c.x_min.value = 0.0
        c.x_max.value = 100.0
        c.log_y_cb.value = True
        c.equal_aspect_cb.value = True
        c.legend_cb.value = False
        kwargs = tab.view().opts.get().kwargs
        assert kwargs["xlabel"] == "easting"
        assert kwargs["ylabel"] == "northing"
        assert kwargs["xlim"] == (0.0, 100.0)
        assert kwargs["logy"] is True
        assert kwargs["aspect"] == "equal"
        assert kwargs["show_legend"] is False


class TestColorSystem:
    """M4 color handling: percentile clip, explicit limits, locked shared scale."""

    def _z_tab(self) -> tuple[PlotTab, LayerRow, np.ndarray]:
        n = 1000
        z = np.arange(n, dtype=float)
        df = pd.DataFrame({"x": z, "y": z[::-1].copy(), "z": z})
        tab = PlotTab(_synthetic_state("c", df), 1)
        row = tab.rows[0]
        row.x_sel.value = "x"
        row.y_sel.value = "y"
        row.z_sel.value = "z"
        row.ds_toggle.value = False
        return tab, row, z

    def test_default_percentile_clip_2_98(self) -> None:
        tab, _row, z = self._z_tab()
        lo, hi = tab.view().opts.get().kwargs["clim"]
        assert lo == pytest.approx(np.percentile(z, 2))
        assert hi == pytest.approx(np.percentile(z, 98))

    def test_explicit_min_max_beats_clip(self) -> None:
        tab, row, _ = self._z_tab()
        row.clim_min.value = 10.0
        row.clim_max.value = 90.0
        assert tab.view().opts.get().kwargs["clim"] == (10.0, 90.0)

    def test_full_range_when_clip_0_100(self) -> None:
        tab, row, _ = self._z_tab()
        row.clip_lo.value = 0.0
        row.clip_hi.value = 100.0
        assert "clim" not in tab.view().opts.get().kwargs

    def test_locked_color_scale_shared_across_layers(self) -> None:
        tab, _row, _ = self._z_tab()
        row2 = tab.add_layer()
        row2.x_sel.value = "x"
        row2.y_sel.value = "y"
        row2.z_sel.value = "z"
        row2.ds_toggle.value = False
        c = tab.controls
        c.lock_color_cb.value = True
        c.clim_min.value = 20.0
        c.clim_max.value = 80.0
        view = tab.view()
        assert isinstance(view, hv.Overlay)
        for points in (el for el in view if isinstance(el, hv.Points)):
            assert points.opts.get().kwargs["clim"] == (20.0, 80.0)

    def test_locked_scale_beats_per_layer_explicit(self) -> None:
        tab, row, _ = self._z_tab()
        row.clim_min.value = 1.0
        row.clim_max.value = 2.0
        c = tab.controls
        c.lock_color_cb.value = True
        c.clim_min.value = 20.0
        c.clim_max.value = 80.0
        assert tab.view().opts.get().kwargs["clim"] == (20.0, 80.0)

    def test_cmap_and_log_color(self) -> None:
        tab, row, _ = self._z_tab()
        row.cmap_sel.value = "magma"
        row.log_color_cb.value = True
        kwargs = tab.view().opts.get().kwargs
        assert kwargs["cmap"] == "magma"
        assert kwargs["cnorm"] == "log"


class TestDuplicateSwap:
    """M4 comparison workflow: duplicate plot, swap file (design §4.5, UX §7)."""

    def test_duplicate_captures_spec(self) -> None:
        state = _state(WS, CSV)
        created: list = []
        tab = PlotTab(state, 1, on_duplicate=created.append)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        tab.dup_btn.clicks += 1
        assert len(created) == 1
        spec = created[0]
        assert spec.layers[0].file == "sample_ws"  # "(keep same)"
        assert spec.layers[0].x == "XCORD_MIDPT"

    def test_duplicate_with_swap_file(self) -> None:
        state = _state(WS, CSV)
        created: list = []
        tab = PlotTab(state, 1, on_duplicate=created.append)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        tab.dup_file_sel.value = "sample_csv_nulls"
        tab.dup_btn.clicks += 1
        spec = created[0]
        assert spec.layers[0].file == "sample_csv_nulls"
        assert spec.layers[0].x == "XCORD_MIDPT"
        assert spec.layers[0].y == "YCORD_MIDPT"

    def test_workspace_duplicate_creates_comparable_tab(self) -> None:
        from better_mad.app.server import build_workspace

        state = _state(WS, CSV)
        button, tabs, plot_tabs = build_workspace(state)
        button.clicks += 1
        tab = plot_tabs[0]
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        row.ds_toggle.value = False
        tab.dup_file_sel.value = "sample_csv_nulls"
        tab.dup_btn.clicks += 1
        assert len(tabs) == 2
        tab2 = plot_tabs[1]
        assert tabs.active == 1
        row2 = tab2.rows[0]
        assert row2.file_sel.value == "sample_csv_nulls"
        # identical parameters preserved across the swap
        assert row2.x_sel.value == "XCORD_MIDPT"
        assert row2.y_sel.value == "YCORD_MIDPT"

        # locked color scale → identical clim on both plots
        for t in (tab, tab2):
            t.rows[0].z_sel.value = "TR_DOMFREQ" if t is tab else "TR_HILBSTATIC"
            t.controls.lock_color_cb.value = True
            t.controls.clim_min.value = 0.0
            t.controls.clim_max.value = 100.0
        clim1 = tab.view().opts.get().kwargs["clim"]
        clim2 = tab2.view().opts.get().kwargs["clim"]
        assert clim1 == clim2 == (0.0, 100.0)


class TestLayoutSplit:
    """M4 UI refinement: center workspace holds only the plot; all controls
    move to a right style drawer that follows the active tab (UX §1)."""

    def test_plot_layout_has_no_controls(self) -> None:
        tab = PlotTab(_state(WS), 1)
        plot_col = tab.plot_layout()
        # only banner + plot containers; no widgets anywhere in the center pane
        assert isinstance(plot_col, pn.Column)
        widgets = [o for o in plot_col if isinstance(o, pn.widgets.Widget)]
        assert widgets == []

    def test_settings_layout_holds_controls(self) -> None:
        tab = PlotTab(_state(WS), 1)
        settings = tab.settings_layout()
        assert tab.layers_area in settings
        assert tab.controls.layout() in settings
        assert tab.plot_area not in settings

    def test_drawer_follows_active_tab(self) -> None:
        from better_mad.app.server import build_drawer, build_workspace

        button, tabs, plot_tabs = build_workspace(_state(WS, CSV))
        drawer = build_drawer(tabs, plot_tabs)
        assert "*Add a plot" in drawer.objects[0].object
        button.clicks += 1
        assert drawer.objects[0] is plot_tabs[0].settings_layout()
        button.clicks += 1
        assert drawer.objects[0] is plot_tabs[1].settings_layout()
        tabs.active = 0
        assert drawer.objects[0] is plot_tabs[0].settings_layout()

    def test_template_main_is_workspace_plus_drawer(self) -> None:
        from better_mad.app.server import DRAWER_WIDTH, build_main, build_workspace

        _button, tabs, _plot_tabs = build_workspace(_state(WS))
        drawer = pn.Column(width=DRAWER_WIDTH)
        main_row = build_main(tabs, drawer)
        assert isinstance(main_row, pn.Row)
        assert isinstance(main_row[0], pn.Tabs)
        assert main_row[1] is drawer
        assert drawer.width == DRAWER_WIDTH  # fixed-width drawer

    def test_plot_figure_fits_container(self) -> None:
        # The bokeh figure must stretch to fill the freed center area.
        tab = PlotTab(_state(WS), 1)
        row = tab.rows[0]
        row.x_sel.value = "XCORD_MIDPT"
        row.y_sel.value = "YCORD_MIDPT"
        row.ds_toggle.value = False
        fig = hv.render(tab.view(), backend="bokeh")
        assert str(fig.width_policy) == "fit"
        assert str(fig.height_policy) == "fit"


class TestAddPlotFlow:
    """Regression: clicking 'Add plot' crashed with
    "'PlotTab' object has no attribute 'param'" (string-form pn.depends)."""

    def test_click_adds_tab(self) -> None:
        from better_mad.app.server import build_workspace

        button, tabs, _plot_tabs = build_workspace(_state(WS, CSV))
        assert len(tabs) == 0
        button.clicks += 1  # fires the on_click callback
        assert len(tabs) == 1
        assert tabs.active == 0  # new tab auto-selected
        assert isinstance(tabs.objects[0], pn.Column)
        button.clicks += 1
        assert len(tabs) == 2
        assert tabs.active == 1

    def test_template_builds_with_and_without_data(self) -> None:
        from better_mad.app.server import build_template

        assert build_template(_state(WS)) is not None
        assert build_template(AppState()) is not None
