"""WorkerManager (handoff M3.2 §5): hub-side supervision of the render worker.

Spawns the worker subprocess, health-polls it, and restarts it on death —
re-executing the **last-good script** (the ``plot.py`` content of the most
recent ``ok`` run). The failing script is never auto-re-run, so a poison
script cannot crash-loop; the crash-loop guard below stops even last-good
re-runs from looping. This module lives under ``worker/`` per the handoff code
map but is consumed only by the hub (and tests); it imports nothing from the
hub package.

Port/origin decision (handoff §5.1): the hub port must be known **before**
the worker is spawned because ``allow_websocket_origin`` replaces bokeh's
default allowlist (trap W3) and the SPA lives on the hub. The CLI therefore
picks the port up front (fixed default, or an explicitly allocated free port
for ``--port 0``) and passes ``hub_origin="127.0.0.1:<port>"``; uvicorn binds
that port afterwards. The allocate-then-close window can in principle lose
the port to a racer — accepted for a localhost dev tool.

Traps honoured here: every blocking call (``start_worker``, ``handle.run``,
``handle.stop``) goes through :func:`asyncio.to_thread` (W1); health checks
are skipped while a run is in flight because the worker serves ``/run`` and
``/health`` on one IOLoop (W2); a worker 409 is surfaced as ``status: "busy"``
(W4); exit code 69 (watchdog kill) is just a death like any other (W5).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import urllib.error
from collections import deque
from collections.abc import Callable
from pathlib import Path

import httpx

from better_mad.worker.lifecycle import start_worker
from better_mad.worker.server import DEFAULT_SESSION_ID, WorkerHandle

logger = logging.getLogger("better_mad.worker.manager")

#: Consecutive failed health checks (while idle) before a restart is forced.
#: One failure is normal jitter; a second means the worker is wedged.
HEALTH_FAILURE_LIMIT = 2


class WorkerManager:
    """Owns exactly one render worker process and its last-good script.

    Events: state changes and run results are pushed to subscribers as JSON
    dicts (``{"type": "worker", ...}`` / ``{"type": "run_result", ...}``) —
    the hub fans them out to SSE clients. :meth:`subscribe` returns an
    unsubscribe callable. All public methods must be called on the hub event
    loop; blocking internals run in threads.
    """

    def __init__(
        self,
        data_dir: Path,
        hub_origin: str,
        *,
        script_path: Path,
        health_interval_s: float = 2.0,
        restart_limit: int = 3,
        restart_window_s: float = 60.0,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._hub_origin = hub_origin
        self._script_path = Path(script_path)
        self._stderr_log = self._data_dir / "worker.log"  # handoff §11
        self._health_interval_s = health_interval_s
        self._restart_limit = restart_limit
        self._restart_window_s = restart_window_s

        self._state = "down"  # starting | ready | restarting | down
        self._epoch = 1
        self._restarts = 0
        self._port: int | None = None
        self._session_id: str = DEFAULT_SESSION_ID
        self._handle: WorkerHandle | None = None

        self._last_good: str | None = None  # plot.py content of the last ok run
        self._restart_times: deque[float] = deque()

        self._subscribers: list[Callable[[dict], None]] = []
        self._run_lock = asyncio.Lock()
        self._run_in_flight = False
        self._health_failures = 0
        self._client: httpx.AsyncClient | None = None
        self._health_task: asyncio.Task | None = None
        self._restart_task: asyncio.Task | None = None
        self._started = False

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Spawn the worker (epoch 1) and start the health-poll task. Idempotent.

        A failed initial spawn degrades to state ``down`` instead of raising —
        the hub never dies with the worker (handoff §5.5).
        """
        if self._started:
            return
        self._started = True
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._health_timeout_s()))
        self._set_state("starting")
        try:
            handle = await self._spawn()
        except Exception as exc:
            logger.error("initial worker spawn failed: %s", exc)
            self._set_state("down")
            return
        self._adopt(handle)
        self._set_state("ready")
        self._health_task = asyncio.create_task(self._health_loop(), name="worker-health")

    async def stop(self) -> None:
        """Stop supervision and terminate the worker process. Idempotent."""
        for task in (self._health_task, self._restart_task):
            if task is not None and not task.done():
                task.cancel()
        self._health_task = self._restart_task = None
        handle, self._handle = self._handle, None
        if handle is not None:
            await asyncio.to_thread(handle.stop)
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._state = "down"

    # --- public surface used by the hub ----------------------------------

    async def run(self, timeout_s: float | None = None) -> dict | None:
        """Run ``plot.py`` on the worker (one at a time, serialized).

        Returns the worker ``/run`` result plus ``"epoch"``; ``None`` when the
        worker is down (nothing was attempted). Emits a ``run_result`` event
        for every attempted run. On success records ``plot.py``'s content
        (read **before** the run) as the last-good script (handoff §5.4).
        """
        if self._state == "down" or self._handle is None:
            return None
        async with self._run_lock:
            return await self._execute(self._script_path, timeout_s=timeout_s, track=True)

    def embed_info(self) -> dict:
        """``{"server_url", "session_id", "epoch"}`` for SPA embedding.

        ``server_url`` is the worker's bokeh application URL (``.../bm``), or
        ``None`` while no worker port is known; the epoch lets the SPA detect
        restarts and re-embed.
        """
        server_url = f"http://127.0.0.1:{self._port}/bm" if self._port is not None else None
        return {"server_url": server_url, "session_id": self._session_id, "epoch": self._epoch}

    def worker_info(self) -> dict:
        """Worker identity for ``/api/health`` and ``/api/status``."""
        return {
            "state": self._state,
            "port": self._port,
            "epoch": self._epoch,
            "restarts": self._restarts,
        }

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def worker_pid(self) -> int | None:
        """PID of the current worker process (ops/tests); ``None`` if unknown."""
        if self._handle is not None and self._handle.process is not None:
            return self._handle.process.pid
        return None

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        """Register an event sink; returns an idempotent unsubscribe callable."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    # --- internals --------------------------------------------------------

    def _health_timeout_s(self) -> float:
        """Per-request health timeout (handoff §5.3: ~2 s)."""
        return 2.0

    async def _spawn(self) -> WorkerHandle:
        """Spawn a fresh worker subprocess and wait for its ready line."""
        return await asyncio.to_thread(
            start_worker,
            self._data_dir,
            allow_websocket_origin=[self._hub_origin],
            stderr_log=self._stderr_log,
        )

    def _adopt(self, handle: WorkerHandle) -> None:
        self._handle = handle
        self._port = handle.port
        self._session_id = handle.session_id
        self._health_failures = 0

    def _emit(self, event: dict) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:  # a bad sink must not kill supervision
                logger.exception("worker event subscriber raised")

    def _set_state(self, state: str) -> None:
        self._state = state
        self._emit(
            {
                "type": "worker",
                "state": state,
                "port": self._port,
                "epoch": self._epoch,
                "restarts": self._restarts,
            }
        )

    async def _execute(
        self, target: Path, *, timeout_s: float | None, track: bool
    ) -> dict | None:
        """Issue one ``/run`` (caller holds ``_run_lock``). Returns the result
        dict (with ``epoch``) or ``None`` when the worker is down/unreachable.

        Transport failures while a run is in flight mean the worker died
        mid-run; the result is still an ``error`` dict, and a restart (with
        last-good re-execution) is scheduled in the background.
        """
        handle = self._handle
        if handle is None:
            return None
        if handle.process is not None and handle.process.poll() is not None:
            self._schedule_restart("worker process exited")
            return None

        content: str | None = None
        if track:
            try:
                content = self._script_path.read_text()
            except OSError as exc:
                return {
                    "status": "error",
                    "run_seconds": 0.0,
                    "stdout_tail": "",
                    "stderr_tail": f"plot.py unreadable: {exc}",
                    "epoch": self._epoch,
                }

        self._run_in_flight = True
        try:
            result: dict = await asyncio.to_thread(handle.run, target, timeout_s)
        except urllib.error.HTTPError as exc:  # 409 busy (W4) or worker-side 4xx/5xx
            detail = exc.read().decode("utf-8", "replace")
            status = "busy" if exc.code == 409 else "error"
            result = {
                "status": status,
                "run_seconds": 0.0,
                "stdout_tail": "",
                "stderr_tail": f"worker /run returned {exc.code}: {detail}",
            }
        except Exception as exc:  # URLError/Timeout/...: worker died mid-run
            result = {
                "status": "error",
                "run_seconds": 0.0,
                "stdout_tail": "",
                "stderr_tail": (
                    f"worker connection lost during run "
                    f"({type(exc).__name__}: {exc})"
                ),
            }
            self._schedule_restart("worker died during run")
        finally:
            self._run_in_flight = False

        result["epoch"] = self._epoch
        if track and result["status"] == "ok" and content is not None:
            self._last_good = content  # store only on ok (handoff §5.4)
        self._emit({"type": "run_result", **result})
        return result

    # --- restart path -----------------------------------------------------

    def _schedule_restart(self, reason: str) -> None:
        """Kick a background restart unless one is already in progress."""
        if self._restart_task is not None and not self._restart_task.done():
            return
        logger.warning("scheduling worker restart: %s", reason)
        self._restart_task = asyncio.create_task(self._restart(), name="worker-restart")

    async def _restart(self) -> None:
        """Replace a dead worker (epoch+1) and re-execute the last-good script.

        Crash-loop guard: at most ``restart_limit`` restarts within
        ``restart_window_s``; the next death after that flips the manager to
        state ``down`` and stops trying (handoff §5.5).
        """
        old, self._handle = self._handle, None
        if old is not None:
            await asyncio.to_thread(old.stop)  # reaps the process, closes pipes
        self._set_state("restarting")

        while True:
            self._prune_restart_times()
            if len(self._restart_times) >= self._restart_limit:
                logger.error("crash-loop limit hit; worker stays down")
                self._set_state("down")
                return
            self._restart_times.append(time.monotonic())
            self._restarts += 1
            self._epoch += 1
            try:
                handle = await self._spawn()
                break
            except Exception as exc:
                logger.warning("worker spawn failed (%s); retrying", exc)
                await asyncio.sleep(0.5)  # loop re-checks the crash guard

        self._adopt(handle)
        self._set_state("ready")
        await self._rerun_last_good()

    async def _rerun_last_good(self) -> None:
        """Re-execute the stored last-good script on the fresh worker.

        ``.data/last_good.py`` is written from the content stored at the last
        ``ok`` run — never the script that killed the worker (handoff §5.4).
        An error here leaves the worker up with an empty plot: degraded but
        alive; the next user/watcher run recovers normally.
        """
        if self._last_good is None:
            return
        last_good_path = self._data_dir / "last_good.py"
        last_good_path.write_text(self._last_good)
        async with self._run_lock:
            await self._execute(last_good_path, timeout_s=None, track=False)

    def _prune_restart_times(self) -> None:
        cutoff = time.monotonic() - self._restart_window_s
        while self._restart_times and self._restart_times[0] < cutoff:
            self._restart_times.popleft()

    # --- health polling ----------------------------------------------------

    async def _health_loop(self) -> None:
        """Poll ``GET /health``; restart on process death or repeated failure.

        While a run is in flight the loop does nothing (W2: the worker serves
        ``/run`` and ``/health`` on one IOLoop, so health legitimately times
        out during runs). Process death is checked first via ``poll()`` — that
        is the definitive signal and needs no HTTP.
        """
        assert self._client is not None
        while True:
            await asyncio.sleep(self._health_interval_s)
            if self._state in ("starting", "restarting", "down"):
                continue
            handle = self._handle
            if handle is None:
                continue
            if handle.process is not None and handle.process.poll() is not None:
                self._schedule_restart("worker process exited")
                continue
            if self._run_in_flight or self._run_lock.locked():
                continue
            healthy = await self._check_health(handle)
            self._health_failures = 0 if healthy else self._health_failures + 1
            if self._health_failures >= HEALTH_FAILURE_LIMIT:
                self._health_failures = 0
                self._schedule_restart("worker health check failed")

    async def _check_health(self, handle: WorkerHandle) -> bool:
        assert self._client is not None
        try:
            resp = await self._client.get(f"{handle.base_url}/health")
            return resp.status_code == 200 and bool(resp.json().get("ok"))
        except Exception:
            return False


__all__ = ["HEALTH_FAILURE_LIMIT", "WorkerManager"]
