"""``plot.py`` watcher (handoff M3.2 §6): poll-based, dependency-free.

Detects changes by polling ``mtime_ns``+``size`` every ``interval_s`` (~0.3 s)
and fires only once the file has **stayed unchanged for ``stability_s``
(~500 ms)** — agents write in bursts, so stability beats raw inotify-style
events (PLAN R3). On fire, the ``on_change`` callback runs on the event loop;
the hub decides what happens next (``plot_changed`` SSE event, then an
auto-run if enabled).

Coalescing lives in the hub's run orchestrator, not here: when a run is
already in flight the hub sets :attr:`pending` and runs once after the
current run completes — the **latest** script, never a queued backlog.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path


class PlotWatcher:
    """Polls ``script_path`` and calls ``on_change`` after a stable change.

    ``last_change_ts``/``pending`` are exposed for ``/api/status``.
    """

    def __init__(
        self,
        script_path: Path,
        on_change: Callable[[], None],
        *,
        interval_s: float = 0.3,
        stability_s: float = 0.5,
    ) -> None:
        self.script_path = Path(script_path)
        self._on_change = on_change
        self._interval_s = interval_s
        self._stability_s = stability_s

        self.last_change_ts: float | None = None  # wall-clock, for /api/status
        self.pending = False  # hub sets this when coalescing behind a run

        self._task: asyncio.Task | None = None
        self._last_stat: tuple[int, int] | None = None
        self._changed_at: float | None = None  # monotonic, debounce clock

    def start(self) -> None:
        """Start polling (must be called on a running event loop). Idempotent."""
        if self._task is not None:
            return
        self._last_stat = self._stat()
        self._task = asyncio.create_task(self._loop(), name="plot-watcher")

    def stop(self) -> None:
        """Stop polling; safe to call multiple times."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _stat(self) -> tuple[int, int] | None:
        """``(mtime_ns, size)`` fingerprint, or ``None`` while the file is
        missing (a deletion counts as a change, as does recreation)."""
        try:
            st = self.script_path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            stat = self._stat()
            if stat != self._last_stat:
                self._last_stat = stat
                self._changed_at = time.monotonic()
                continue
            if (
                self._changed_at is not None
                and time.monotonic() - self._changed_at >= self._stability_s
            ):
                self._changed_at = None
                self.last_change_ts = time.time()
                self._on_change()
