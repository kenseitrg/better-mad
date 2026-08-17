"""Headless tests for the preview app state machine (M2).

No browser, no server, no real timers: ticks are called explicitly, debounce
windows are faked via `_changed_at`, and the worker thread is replaced by a
direct queue injection where determinism matters.
"""

import time
from pathlib import Path

import holoviews as hv

from better_mad.app.preview import DEBOUNCE_S, PreviewApp
from better_mad.core.runner import RunResult
from better_mad.core.workspace import create_workspace

FIXTURES = Path(__file__).parent / "fixtures"
GOOD_SCRIPT = (
    "import holoviews as hv\n"
    "import better_mad.sdk as bm\n"
    "df = bm.data('sample_csv_nulls')\n"
    "bm.show(hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT']))\n"
)


def _app(tmp_path: Path, script: str = GOOD_SCRIPT) -> PreviewApp:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    ws.script_path.write_text(script)
    return PreviewApp(ws)


def _wait_debounce(app: PreviewApp) -> None:
    """Let the watcher see the current mtime, then fake the quiet window."""
    app.tick()  # registers mtime, sets _changed_at
    assert app._changed_at is not None
    app._changed_at = time.monotonic() - DEBOUNCE_S - 0.1


def test_debounce_holds_run_until_quiet(tmp_path: Path) -> None:
    app = _app(tmp_path)
    started: list[float] = []
    app.start_run = lambda: started.append(time.monotonic())  # type: ignore

    app.tick()  # first sighting of the mtime — too fresh to run
    assert started == []
    app._changed_at = time.monotonic() - DEBOUNCE_S - 0.1
    app.tick()
    assert len(started) == 1


def test_no_rerun_without_change(tmp_path: Path) -> None:
    app = _app(tmp_path)
    started = 0

    def fake_start() -> None:
        nonlocal started
        started += 1
        app._running = True
        app._ran_mtime = app.script_mtime()

    app.start_run = fake_start  # type: ignore
    _wait_debounce(app)
    app.tick()
    assert started == 1
    # mtime unchanged → _ran_mtime matches → nothing further
    app._running = False
    _wait_debounce(app)
    app.tick()
    assert started == 1


def test_autorun_toggle_off(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.auto_run.value = False
    started = 0

    def fake_start() -> None:
        nonlocal started
        started += 1

    app.start_run = fake_start  # type: ignore
    app.tick()
    app._changed_at = time.monotonic() - 10
    app.tick()
    assert started == 0
    assert not app._running


def test_ok_result_renders_figure(tmp_path: Path) -> None:
    app = _app(tmp_path)
    fig = hv.Curve([1, 2, 3])
    app._apply_result(RunResult("ok", fig, 0.8, "", ""))
    assert app.figure_pane.object is fig
    assert app.figure_pane.visible is True
    assert app.banner.visible is False
    assert "✓" in app.status.object
    assert app._last_good is not None


def test_ok_without_show_gives_placeholder(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app._apply_result(RunResult("ok", None, 0.3, "", ""))
    assert app.figure_pane.visible is False
    assert "no figure" in app._placeholder.object
    assert "bm.show" in app._placeholder.object


def test_error_keeps_last_good_and_shows_banner(tmp_path: Path) -> None:
    app = _app(tmp_path)
    fig = hv.Curve([1, 2, 3])
    app._apply_result(RunResult("ok", fig, 0.5, "", ""))
    app._apply_result(RunResult("error", None, 0.2, "", "ValueError: boom"))
    # last good plot stays up
    assert app.figure_pane.object is fig
    assert app.figure_pane.visible is True
    # staleness note + traceback tail in the banner
    assert app.banner.visible is True
    assert "last good result" in app.banner.object
    assert "ValueError: boom" in app.banner.object
    assert "✗" in app.status.object


def test_error_with_no_previous_figure(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app._apply_result(RunResult("error", None, 0.2, "", "SyntaxError: x"))
    assert app.figure_pane.visible is False
    assert app.banner.visible is True
    assert "SyntaxError: x" in app.banner.object


def test_timeout_banner(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.timeout = 5.0
    app._apply_result(RunResult("timeout", None, 5.0, "", ""))
    assert app.banner.visible is True
    assert "timed out after 5 s" in app.banner.object


def test_recovery_clears_banner(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app._apply_result(RunResult("error", None, 0.2, "", "boom"))
    assert app.banner.visible is True
    fig = hv.Curve([1])
    app._apply_result(RunResult("ok", fig, 0.4, "", ""))
    assert app.banner.visible is False
    assert app.figure_pane.object is fig


def test_full_loop_with_real_subprocess(tmp_path: Path) -> None:
    """Watcher-triggered run end to end (worker thread + queue drain)."""
    app = _app(tmp_path)
    _wait_debounce(app)
    app.tick()  # starts the real worker thread
    deadline = time.monotonic() + 30
    while app._running and time.monotonic() < deadline:
        time.sleep(0.1)
        app.tick()  # drains the result queue when the worker finishes
    assert not app._running
    assert isinstance(app.figure_pane.object, hv.Points)
    assert "✓" in app.status.object
