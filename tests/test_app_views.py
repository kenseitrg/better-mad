"""Headless tests for app state and plot tab construction (M2)."""

from pathlib import Path

import holoviews as hv
import panel as pn

from better_mad.app.state import AppState
from better_mad.app.views import VECTOR_WARN_ROWS, PlotTab
from better_mad.core.dataset import Dataset

hv.extension("bokeh")

FIXTURES = Path(__file__).parent / "fixtures"
WS = FIXTURES / "sample_ws.txt"
CSV = FIXTURES / "sample_csv_nulls.csv"


def _state(*paths: Path) -> AppState:
    state = AppState()
    state.load_files(list(paths))
    return state


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


class TestAddPlotFlow:
    """Regression: clicking 'Add plot' crashed with
    "'PlotTab' object has no attribute 'param'" (string-form pn.depends)."""

    def test_click_adds_tab(self) -> None:
        from better_mad.app.server import build_workspace

        button, tabs = build_workspace(_state(WS, CSV))
        assert len(tabs) == 0
        button.clicks += 1  # fires the on_click callback
        assert len(tabs) == 1
        assert isinstance(tabs.objects[0], pn.Column)
        button.clicks += 1
        assert len(tabs) == 2

    def test_template_builds_with_and_without_data(self) -> None:
        from better_mad.app.server import build_template

        assert build_template(_state(WS)) is not None
        assert build_template(AppState()) is not None
