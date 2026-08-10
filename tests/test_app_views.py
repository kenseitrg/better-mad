"""Headless tests for app state and plot tab construction (M2/M3)."""

from pathlib import Path

import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn

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
        state = _state(WS)
        tab = PlotTab(state, 1)
        assert tab.file_sel.value == "sample_ws"
        assert tab.x_sel.value == "CMP"  # first column
        assert tab.y_sel.value == "XCORD_MIDPT"  # second column
        assert tab.z_sel.value == ""  # no color by default
        assert tab.ds_toggle.value is False  # 1000 rows < threshold

    def test_view_vector_and_datashader(self) -> None:
        state = _state(WS)
        tab = PlotTab(state, 1)
        tab.x_sel.value = "TR_DOMFREQ"
        tab.y_sel.value = "TR_RMSAMP"

        tab.ds_toggle.value = False
        element = tab.view()
        assert isinstance(element, hv.Points)

        tab.ds_toggle.value = True
        shaded = tab.view()
        assert isinstance(shaded, hv.DynamicMap)  # dynamic: re-aggregates on pan/zoom

    def test_color_scatter(self) -> None:
        state = _state(WS)
        tab = PlotTab(state, 1)
        tab.x_sel.value = "XCORD_MIDPT"
        tab.y_sel.value = "YCORD_MIDPT"
        tab.z_sel.value = "TR_DOMFREQ"

        tab.ds_toggle.value = False
        element = tab.view()
        assert isinstance(element, hv.Points)
        assert "TR_DOMFREQ" in [d.name for d in element.vdims]  # hover includes z

        tab.ds_toggle.value = True
        shaded = tab.view()
        assert isinstance(shaded, hv.DynamicMap)  # mean aggregation over z

    def test_file_change_resyncs_columns(self) -> None:
        state = _state(WS, CSV)
        tab = PlotTab(state, 1)
        tab.file_sel.value = "sample_csv_nulls"
        assert "TR_HILBSTATIC" in tab.x_sel.options.values()
        assert tab.x_sel.value == "CMP"

    def test_warn_banner_only_for_large_vector(self, monkeypatch) -> None:
        state = _state(WS)
        tab = PlotTab(state, 1)
        assert tab.banner() is None  # small file

        monkeypatch.setattr(Dataset, "n_rows", property(lambda self: VECTOR_WARN_ROWS + 1))
        tab2 = PlotTab(_state(WS), 1)
        assert tab2.ds_toggle.value is True  # large file defaults to datashader
        tab2.ds_toggle.value = False
        assert isinstance(tab2.banner(), pn.pane.Alert)
        tab2.ds_toggle.value = True
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
        tab.type_sel.value = "histogram"
        tab.x_sel.value = "TR_DOMFREQ"
        tab.bins_slider.value = 40
        hist = tab.view()
        assert isinstance(hist, hv.Histogram)
        assert len(hist.edges) - 1 == 40

    def test_histogram_kde_overlay(self) -> None:
        tab = PlotTab(_state(WS), 1)
        tab.type_sel.value = "histogram"
        tab.x_sel.value = "TR_DOMFREQ"
        tab.kde_overlay.value = True
        assert isinstance(tab.view(), hv.Overlay)

    def test_density1d(self) -> None:
        tab = PlotTab(_state(WS), 1)
        tab.type_sel.value = "density1d"
        tab.x_sel.value = "TR_RMSAMP"
        assert isinstance(tab.view(), hv.Curve)

    def test_density2d_count_and_mean(self) -> None:
        tab = PlotTab(_state(WS), 1)
        tab.type_sel.value = "density2d"
        tab.x_sel.value = "XCORD_MIDPT"
        tab.y_sel.value = "YCORD_MIDPT"
        assert isinstance(tab.view(), hv.DynamicMap)
        tab.z_sel.value = "TR_DOMFREQ"
        tab.agg_sel.value = "mean"
        assert isinstance(tab.view(), hv.DynamicMap)

    def test_line_vector(self) -> None:
        tab = PlotTab(_state(FIXTURES / "sample_2d_lines.txt"), 1)
        tab.type_sel.value = "line"
        tab.x_sel.value = "CMP"
        tab.y_sel.value = "TR_RMSAMP"
        tab.ds_toggle.value = False
        assert isinstance(tab.view(), hv.Curve)
        tab.ds_toggle.value = True
        assert isinstance(tab.view(), hv.DynamicMap)

    def test_line_decimation(self) -> None:
        n = LINE_DECIMATE_ROWS * 3
        df = pd.DataFrame({"x": np.arange(n, dtype=float), "y": np.random.random(n)})
        tab = PlotTab(_synthetic_state("big", df), 1)
        tab.type_sel.value = "line"
        tab.x_sel.value = "x"
        tab.y_sel.value = "y"
        tab.ds_toggle.value = False
        curve = tab.view()
        assert isinstance(curve, hv.Curve)
        assert len(curve.data) <= LINE_DECIMATE_ROWS
        assert "decimated" in curve.opts.get().kwargs["title"]

    def test_polar_transform_uses_abs_radius(self) -> None:
        # θ: 0°, 90°, 180°; r: 1, 2, -3 (abs → 3)
        df = pd.DataFrame({"az": [0.0, 90.0, 180.0], "off": [1.0, 2.0, -3.0]})
        tab = PlotTab(_synthetic_state("pol", df), 1)
        tab.type_sel.value = "polar"
        tab.theta_sel.value = "az"
        tab.r_sel.value = "off"
        tab.ds_toggle.value = False
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
        tab.type_sel.value = "polar"
        tab.theta_sel.value = "az"
        tab.r_sel.value = "off"
        tab.z_sel.value = "amp"
        tab.ds_toggle.value = False
        overlay = tab.view()
        points = next(el for el in overlay if isinstance(el, hv.Points))
        assert "amp" in [d.name for d in points.vdims]
        tab.ds_toggle.value = True
        shaded = tab.view()
        # DynamicMap absorbs the static graticule overlay when combined
        assert isinstance(shaded, hv.DynamicMap)

    def test_visibility_by_type(self) -> None:
        tab = PlotTab(_state(WS), 1)
        tab.type_sel.value = "polar"
        assert not tab.x_sel.visible
        assert tab.theta_sel.visible and tab.r_sel.visible and tab.z_sel.visible
        tab.type_sel.value = "histogram"
        assert tab.bins_slider.visible and tab.kde_overlay.visible
        assert not tab.y_sel.visible
        tab.type_sel.value = "scatter"
        assert tab.x_sel.visible and tab.y_sel.visible
        assert not tab.theta_sel.visible

    def test_nan_only_column_placeholder(self) -> None:
        df = pd.DataFrame({"a": [np.nan, np.nan], "b": [1.0, 2.0]})
        tab = PlotTab(_synthetic_state("nan", df), 1)
        tab.type_sel.value = "histogram"
        tab.x_sel.value = "a"
        assert isinstance(tab.view(), pn.pane.Markdown)


class TestAddPlotFlow:
    """Regression: clicking 'Add plot' crashed with
    "'PlotTab' object has no attribute 'param'" (string-form pn.depends)."""

    def test_click_adds_tab(self) -> None:
        from better_mad.app.server import build_workspace

        button, tabs = build_workspace(_state(WS, CSV))
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
