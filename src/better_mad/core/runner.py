"""Subprocess runner for ``plot.py`` (design.md §5).

The script runs in a child process, never in the app process. Data flows in via
``better_mad.sdk`` (parquet snapshots in the workspace's ``.data/`` directory);
the figure flows out through ``bm.show()`` pickling to a runner-provided path.

The HoloViews figure is unpickled lazily so this module stays import-light;
unpickling necessarily imports holoviews itself.
"""

from __future__ import annotations

import contextlib
import os
import pickle
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Literal

from better_mad.core.workspace import Workspace

ENV_DATA_DIR = "BETTER_MAD_DATA_DIR"
ENV_OUTPUT = "BETTER_MAD_OUTPUT"
DEFAULT_TIMEOUT_S = 60.0


@dataclass
class RunResult:
    """Outcome of one ``plot.py`` execution.

    Attributes:
        status: ``ok`` (ran to exit 0), ``error`` (non-zero exit), ``timeout``.
        figure: the last ``bm.show()`` payload, or None if none was shown.
        duration_s: wall-clock seconds of the run.
        stdout: captured standard output.
        stderr: captured standard error (traceback on error).
    """

    status: Literal["ok", "error", "timeout"]
    figure: object | None
    duration_s: float
    stdout: str
    stderr: str


def run_script(workspace: Workspace, timeout: float = DEFAULT_TIMEOUT_S) -> RunResult:
    """Execute the workspace's ``plot.py`` in a subprocess and collect the figure.

    Raises ``FileNotFoundError`` if the script does not exist. On timeout the
    whole process group is killed (scripts may spawn their own children).
    """
    script = workspace.script_path
    if not script.exists():
        raise FileNotFoundError(f"no script to run: {script}")

    fd, output_path = tempfile.mkstemp(prefix="bm_out_", suffix=".pkl")
    os.close(fd)
    os.unlink(output_path)  # presence after the run means show() was called

    env = os.environ | {
        ENV_DATA_DIR: str(workspace.data_dir),
        ENV_OUTPUT: output_path,
    }
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(workspace.path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        try:
            out_b, err_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            out_b, err_b = proc.communicate()
            return RunResult(
                status="timeout",
                figure=None,
                duration_s=time.perf_counter() - t0,
                stdout=_decode(out_b),
                stderr=_decode(err_b),
            )

        stdout, stderr = _decode(out_b), _decode(err_b)
        duration = time.perf_counter() - t0

        if proc.returncode != 0:
            return RunResult("error", None, duration, stdout, stderr)
        figure = _load_figure(output_path) if os.path.exists(output_path) else None
        return RunResult("ok", figure, duration, stdout, stderr)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(output_path)


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _load_figure(output_path: str) -> object:
    with open(output_path, "rb") as fh:
        payload = pickle.load(fh)  # local transport from our own subprocess
    if not (isinstance(payload, dict) and "figure" in payload):
        return payload  # legacy: raw figure
    _reapply_options(payload["figure"], payload.get("options") or [])
    return payload["figure"]


def _reapply_options(figure: object, options: list[dict[str, dict[str, object]] | None]) -> None:
    """Restore options captured by ``bm.show()`` (lost in pickle; see sdk docs)."""
    if not options:
        return
    import holoviews as hv

    if "bokeh" not in hv.Store.loaded_backends():
        hv.extension("bokeh")
    items: list[object] = []
    figure.traverse(lambda x: items.append(x))  # type: ignore
    for item, entry in zip(items, options, strict=False):
        if not entry:
            continue
        kwargs: dict[str, object] = {}
        for group in ("plot", "style", "norm"):
            kwargs.update(entry.get(group) or {})
        if kwargs:
            item.opts(**kwargs)  # type: ignore


def _decode(data: bytes | None) -> str:
    return (data or b"").decode(errors="replace")
