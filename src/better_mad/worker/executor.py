"""In-process execution of ``plot.py`` inside the render worker (design.md §5).

This replaces the M2 subprocess+pickle runner for the live-document
architecture: scripts run on the worker's bokeh IOLoop thread in a **fresh
namespace**. Module/global state pollution across runs is an accepted
trade-off (design §5 "Isolation trade-off"); a fatal script only costs its own
worker process.

Figures produced via ``bm.show()`` are rendered eagerly by the attach
transport (``better_mad.sdk._ATTACH``, see :class:`FigureCollector`) into a
pending slot; only when the script finishes without exception does the worker
attach the last figure to the shared document.

**Watchdog semantics** (handoff §4.3): a run exceeding its timeout kills the
whole worker process — a watchdog thread calls ``os._exit``, skipping all
cleanup, because arbitrary Python cannot be interrupted cleanly. The hub
detects the dead worker and restarts it (M3.2). Consequently a timed-out run
never returns here; the ``timeout`` status exists for API-contract symmetry
and future execution strategies.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Literal

DEFAULT_RUN_TIMEOUT_S = 60.0
TAIL_CHARS = 2000
HANG_EXIT_CODE = 69  # os._exit code a hub can recognize as watchdog kill


@dataclass
class RunResult:
    """Outcome of one in-process script run.

    Attributes:
        status: ``ok`` (ran without exception), ``error`` (script exception,
            missing script file, or ``bm.show()`` never called),
            ``timeout`` (contract-only: a real timeout kills the process and
            returns nothing — see the module docstring).
        run_seconds: wall-clock seconds of the exec.
        stdout_tail: last ~2000 chars of captured stdout.
        stderr_tail: last ~2000 chars of captured stderr (traceback appended
            on ``error``).
        plot: the last rendered HoloViews plot object (kept referenced — its
            DynamicMap range callbacks re-rasterize on zoom), or ``None``.
    """

    status: Literal["ok", "error", "timeout"]
    run_seconds: float
    stdout_tail: str
    stderr_tail: str
    plot: object | None = None


class FigureCollector:
    """Attach transport installed as ``better_mad.sdk._ATTACH`` during a run.

    Renders eagerly — ``hv.renderer("bokeh").get_plot(obj)`` — so invalid
    ``.opts()`` raise *inside* the run (a clean error result) instead of at
    some later render. Rendered plot objects are kept referenced here:
    dropping them garbage-collects the DynamicMap range callbacks that
    re-rasterize on zoom (trap T6). Attachment to the document is deferred —
    the worker swaps document roots only after the script finished cleanly.
    """

    def __init__(self) -> None:
        self.plots: list[object] = []

    def __call__(self, obj: object) -> None:
        import holoviews as hv  # deferred: keeps this module import-light

        plot = hv.renderer("bokeh").get_plot(obj)
        self.plots.append(plot)

    @property
    def last_plot(self) -> object | None:
        return self.plots[-1] if self.plots else None


def _tail(text: str) -> str:
    return text[-TAIL_CHARS:]


def run_script(
    script_path: Path,
    *,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
    document: object | None = None,
) -> RunResult:
    """Execute one script in a fresh namespace; never raises for script errors.

    ``document``: if given, ``bokeh.io.curdoc()`` targets it for the duration
    of the run (the previous document is restored afterwards), so scripts may
    rely on the ambient document. Pass the shared session document when
    running inside the worker.

    stdout/stderr are captured per run and returned as tails; a script
    exception (including ``SystemExit`` — the worker must survive any script)
    becomes ``status="error"`` with the traceback in ``stderr_tail``.
    """
    from bokeh.io.state import curstate

    import better_mad.sdk as bm_sdk

    if not script_path.is_file():
        return RunResult("error", 0.0, "", f"script not found: {script_path}")

    stdout, stderr = StringIO(), StringIO()
    collector = FigureCollector()
    prev_attach = bm_sdk._ATTACH
    prev_document = curstate().document
    started = time.perf_counter()

    # Watchdog: plain thread-timer; when it fires, os._exit tears down the
    # whole process immediately (no finally/cleanup handlers run) — by design,
    # see the module docstring.
    watchdog = threading.Timer(timeout_s, os._exit, args=(HANG_EXIT_CODE,))
    watchdog.daemon = True
    watchdog.start()

    bm_sdk._ATTACH = collector
    if document is not None:
        # bokeh's setter accepts None at runtime; its type stub is narrower.
        curstate().document = document  # ty: ignore[invalid-assignment]
    status: Literal["ok", "error"] = "ok"
    try:
        source = script_path.read_text(encoding="utf-8")
        code = compile(source, str(script_path), "exec")
        namespace: dict[str, object] = {
            "__name__": "__main__",
            "__file__": str(script_path),
        }
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(code, namespace)  # scripts are user-privileged code (design §10)
    except (Exception, SystemExit):
        status = "error"
        stderr.write(traceback.format_exc())
    finally:
        watchdog.cancel()
        bm_sdk._ATTACH = prev_attach
        curstate().document = prev_document

    return RunResult(
        status=status,
        run_seconds=time.perf_counter() - started,
        stdout_tail=_tail(stdout.getvalue()),
        stderr_tail=_tail(stderr.getvalue()),
        plot=collector.last_plot if status == "ok" else None,
    )
