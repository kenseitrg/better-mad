"""Spawned-worker lifecycle helper (handoff §4.1: "thin, run-blocking, for tests").

Starts the worker as a real subprocess (``python -m better_mad.worker``), waits
for its ready announcement on stdout, and exposes a :class:`~better_mad.worker.server.WorkerHandle`
whose ``stop()`` terminates the process. The M3.2 hub will grow the same
pattern (spawn, health-poll, restart) on top of the same entry point.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from better_mad.worker.server import DEFAULT_SESSION_ID, WorkerHandle

READY_PREFIX = "WORKER_READY "
BOOT_TIMEOUT_S = 120.0  # interpreter + bokeh + holoviews import can take a while


def start_worker(
    data_dir: Path | str,
    *,
    port: int = 0,
    session_id: str = DEFAULT_SESSION_ID,
    allow_websocket_origin: Sequence[str] | None = None,
    stderr_log: Path | str | None = None,
    boot_timeout_s: float = BOOT_TIMEOUT_S,
) -> WorkerHandle:
    """Spawn a worker subprocess on an ephemeral port and wait until ready.

    ``allow_websocket_origin``: extra origins (``host:port`` or URLs) allowed
    to open the live-document websocket — the M3.2 hub passes its own origin
    here (the SPA is served from the hub, not the worker).

    ``stderr_log``: optional path to capture worker stderr (bokeh logs, script
    noise) for debugging; default discards it. Raises ``RuntimeError`` if no
    ready announcement arrives within ``boot_timeout_s`` (the half-spawned
    process is killed first).
    """
    args = [
        sys.executable,
        "-m",
        "better_mad.worker",
        "--data-dir",
        str(data_dir),
        "--port",
        str(port),
        "--session-id",
        session_id,
    ]
    for origin in allow_websocket_origin or []:
        args += ["--allow-websocket-origin", origin]
    stderr_fh = open(stderr_log, "wb") if stderr_log else None  # noqa: SIM115 - closed by handle.stop()
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=stderr_fh if stderr_fh is not None else subprocess.DEVNULL,
    )
    try:
        payload = _read_ready(proc, boot_timeout_s)
    except Exception:
        proc.kill()
        proc.wait(timeout=10)
        if proc.stdout is not None:
            proc.stdout.close()
        if stderr_fh is not None:
            stderr_fh.close()
        raise
    return WorkerHandle(
        process=proc,
        port=int(payload["port"]),
        session_id=str(payload["session_id"]),
        base_url=f"http://127.0.0.1:{int(payload['port'])}",
        _stderr_fh=stderr_fh,
    )


def _read_ready(proc: subprocess.Popen, timeout_s: float) -> dict:
    """Read the ``WORKER_READY {json}`` line from the worker's stdout, with a
    hard timeout (a worker that never announces must not hang the caller)."""
    result: dict = {}
    done = threading.Event()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.decode("utf-8", "replace")
            if text.startswith(READY_PREFIX):
                result["payload"] = json.loads(text[len(READY_PREFIX) :])
                break
        done.set()

    threading.Thread(target=reader, daemon=True).start()
    if not done.wait(timeout_s):
        raise RuntimeError(f"worker did not announce ready within {timeout_s:g}s")
    return result["payload"]
