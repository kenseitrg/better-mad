"""Tests for the subprocess runner + SDK transport (M1).

Each test spawns a real subprocess running a generated plot.py, exercising the
same path the app uses: env wiring, sdk.data()/show(), figure pickle transport,
stdout/stderr capture, and the error/timeout paths.
"""

from pathlib import Path

import holoviews as hv

from better_mad.core.runner import run_script
from better_mad.core.workspace import Workspace, create_workspace

FIXTURES = Path(__file__).parent / "fixtures"


def _ws_with_data(tmp_path: Path) -> Workspace:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    return ws


def test_roundtrip_script_to_figure(tmp_path: Path) -> None:
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text(
        "import holoviews as hv\n"
        "import better_mad.sdk as bm\n"
        "df = bm.data('sample_ws')\n"
        "pts = hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT'])\n"
        "bm.show(pts)\n"
    )
    result = run_script(ws)
    assert result.status == "ok"
    assert isinstance(result.figure, hv.Points)
    assert len(result.figure) > 0
    assert result.duration_s > 0


def test_list_data_and_stdout_capture(tmp_path: Path) -> None:
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text("import better_mad.sdk as bm\nprint('datasets:', bm.list_data())\n")
    result = run_script(ws)
    assert result.status == "ok"
    assert result.figure is None  # show() never called
    assert "['sample_ws']" in result.stdout


def test_script_error_reports_traceback(tmp_path: Path) -> None:
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text("raise ValueError('boom')\n")
    result = run_script(ws)
    assert result.status == "error"
    assert result.figure is None
    assert "ValueError: boom" in result.stderr


def test_missing_dataset_surfaces_keyerror(tmp_path: Path) -> None:
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text("import better_mad.sdk as bm\nbm.data('nope')\n")
    result = run_script(ws)
    assert result.status == "error"
    assert "KeyError" in result.stderr
    assert "sample_ws" in result.stderr  # available names listed in the message


def test_timeout_kills_script(tmp_path: Path) -> None:
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text("import time\ntime.sleep(30)\n")
    result = run_script(ws, timeout=1.0)
    assert result.status == "timeout"
    assert result.figure is None
    assert result.duration_s < 10  # killed promptly, not after the sleep


def test_last_show_wins(tmp_path: Path) -> None:
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text(
        "import holoviews as hv\n"
        "import better_mad.sdk as bm\n"
        "df = bm.data('sample_ws')\n"
        "bm.show(hv.Curve([]))\n"
        "pts = hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT'])\n"
        "bm.show(pts)\n"
    )
    result = run_script(ws)
    assert result.status == "ok"
    assert isinstance(result.figure, hv.Points)


def test_opts_work_without_explicit_extension(tmp_path: Path) -> None:
    """SDK import bootstraps the bokeh backend; scripts may use .opts() directly."""
    ws = _ws_with_data(tmp_path)
    ws.script_path.write_text(
        "import holoviews as hv\n"
        "import better_mad.sdk as bm\n"
        "df = bm.data('sample_ws')\n"
        "pts = hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT'], vdims=['TR_DOMFREQ'])\n"
        "bm.show(pts.opts(color='TR_DOMFREQ', cmap='viridis', size=2))\n"
    )
    result = run_script(ws)
    assert result.status == "ok", result.stderr
    assert isinstance(result.figure, hv.Points)


def test_run_without_script_raises(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.script_path.unlink()
    try:
        run_script(ws)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass
