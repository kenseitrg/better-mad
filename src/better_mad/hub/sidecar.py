"""SidecarManager (handoff M3.3 §7): hub-side supervision of the chat sidecar.

Mirrors :class:`better_mad.worker.manager.WorkerManager` — spawn, ready-line
handshake, health-poll, restart-once-on-crash, then degrade — but stricter
(design §9.1: at most ONE restart per 60 s window; a chat process must not
crash-loop). The sidecar is the Node process hosting the pi SDK AgentSession
(``sidecar/src/main.ts``); this module depends on it only through the
``SIDECAR_READY {json}`` stdout contract (``{"port", "model", "session_file"}``)
and its HTTP ``/health``.

Gates (R1): a missing Node, or Node older than 22.18 (native ``.ts`` execution
— trap T7), degrades to state ``down`` with a reason string; nothing is
spawned on a failed gate. Boot failures (missing ``~/.pi/agent``, unknown
``--model``, missing ``npm install`` — traps T10/T12) are captured from the
sidecar's stderr tail as the down-reason after one retry — never a tight
crash loop. A hung turn is NOT unhealthiness: ``/health`` stays responsive
while ``busy: true``, so health checks only judge process liveness.

Events are pushed to subscribers as ``{"type": "chat", "state": ..., "port",
"model", "restarts", "reason"?}`` dicts — the hub fans them out on its SSE
stream exactly like worker events. All public methods must be called on the
hub event loop; blocking internals (spawn handshake, process stop) run in
threads (W1 analog).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import httpx

logger = logging.getLogger("better_mad.hub.sidecar")

READY_PREFIX = "SIDECAR_READY "
BOOT_TIMEOUT_S = 60.0  # pi SDK import is heavy; pwb boots in seconds
NODE_MIN = (22, 18, 0)  # native type stripping default (handoff §9)
RESTART_LIMIT = 1  # design §9.1: stricter than the worker's 3
RESTART_WINDOW_S = 60.0
HEALTH_FAILURE_LIMIT = 2  # jitter tolerance before forcing a restart
STOP_WAIT_S = 5.0  # terminate → wait → kill (§6.4)
#: Dev override: an alternate sidecar entry file (still run under node).
ENV_ENTRY = "BETTER_MAD_SIDECAR"

_V_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def node_version() -> tuple[int, int, int] | None:
    """The node on PATH as ``(major, minor, patch)``, or ``None`` if unusable."""
    node = shutil.which("node")
    if node is None:
        return None
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = _V_RE.match(out)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def node_gate_reason() -> str | None:
    """A human reason the default command cannot run, or ``None`` if clear."""
    version = node_version()
    if version is None:
        return "node not found — chat disabled (install Node ≥22.18)"
    if version < NODE_MIN:
        joined = ".".join(str(p) for p in version)
        return (
            f"node {joined} too old — chat disabled (the sidecar needs ≥22.18 "
            f"for native TypeScript execution)"
        )
    return None


def _default_entry() -> Path:
    """Locate ``sidecar/src/main.ts`` (cwd first, then the dev checkout root)."""
    candidates = [
        Path.cwd() / "sidecar" / "src" / "main.ts",
        Path(__file__).resolve().parents[3] / "sidecar" / "src" / "main.ts",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "sidecar sources not found (sidecar/src/main.ts missing — wrong cwd?)"
    )


def _stderr_tail(path: Path, max_bytes: int = 2000) -> str:
    """Last non-empty line of a log file (the sidecar's boot-error channel)."""
    try:
        raw = path.read_bytes()[-max_bytes:]
    except OSError:
        return ""
    lines = [ln.strip() for ln in raw.decode("utf-8", "replace").splitlines()]
    lines = [ln for ln in lines if ln]
    return lines[-1][:500] if lines else ""


class SidecarManager:
    """Owns exactly one chat sidecar subprocess.

    States: ``starting | ready | restarting | down`` (+ ``reason`` when not
    healthy). :meth:`chat_info` is the ``/api/health`` & ``/api/status`` view;
    :meth:`base_url` is the proxy target (``None`` unless ``ready``).
    """

    def __init__(
        self,
        workspace: Path,
        *,
        command: list[str] | None = None,
        model: str | None = None,
        health_interval_s: float = 2.0,
        boot_timeout_s: float = BOOT_TIMEOUT_S,
        restart_limit: int = RESTART_LIMIT,
        restart_window_s: float = RESTART_WINDOW_S,
    ) -> None:
        self._workspace = Path(workspace)
        self._command = command  # None → default node command (gated)
        self._model = model
        self._health_interval_s = health_interval_s
        self._boot_timeout_s = boot_timeout_s
        self._restart_limit = restart_limit
        self._restart_window_s = restart_window_s
        self._stderr_log = self._workspace / ".data" / "sidecar.log"

        self._state = "down"
        self._port: int | None = None
        self._model_id: str | None = None
        self._reason: str | None = None
        self._restarts = 0
        self._process: subprocess.Popen | None = None
        self._stderr_fh = None
        self._restart_times: deque[float] = deque()

        self._subscribers: list[Callable[[dict], None]] = []
        self._client: httpx.AsyncClient | None = None
        self._health_task: asyncio.Task | None = None
        self._restart_task: asyncio.Task | None = None
        self._started = False

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Gate → spawn → handshake → health loop. Idempotent; never raises —
        every failure degrades to ``down`` with a reason (the hub never dies
        with the sidecar)."""
        if self._started:
            return
        self._started = True
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(2.0))
        self._set_state("starting")
        if self._command is None:  # injected commands skip the node gate
            gate = node_gate_reason()
            if gate is not None:
                self._down(gate)
                return
        try:
            payload = await self._spawn()
        except Exception as exc:
            # §7.3: one boot retry before down (pi SDK boot is the fragile part).
            reason = self._reason_from(exc)
            logger.warning("sidecar boot failed (%s); retrying once", reason)
            self._restarts += 1  # a boot retry IS a restart (chat_info shows it)
            self._set_state("restarting")
            try:
                payload = await self._spawn()
            except Exception as retry_exc:
                self._down(self._reason_from(retry_exc))
                return
        self._adopt(payload)
        self._set_state("ready")
        self._health_task = asyncio.create_task(self._health_loop(), name="sidecar-health")

    async def stop(self) -> None:
        """Stop supervision and terminate the sidecar. Idempotent."""
        for task in (self._health_task, self._restart_task):
            if task is not None and not task.done():
                task.cancel()
        self._health_task = self._restart_task = None
        await asyncio.to_thread(self._reap)
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._state = "down"
        self._started = False

    # --- public surface used by the hub ----------------------------------

    def chat_info(self) -> dict:
        """``/api/health`` + ``/api/status`` view (handoff §7): ``{"state",
        "port", "model", "restarts", "reason"?}``."""
        info: dict = {
            "state": self._state,
            "port": self._port,
            "model": self._model_id,
            "restarts": self._restarts,
        }
        if self._reason is not None:
            info["reason"] = self._reason
        return info

    def base_url(self) -> str | None:
        """``http://127.0.0.1:<port>`` while ready (the proxy target), else None."""
        if self._state == "ready" and self._port is not None:
            return f"http://127.0.0.1:{self._port}"
        return None

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        """Register an event sink; returns an idempotent unsubscribe callable."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    @property
    def sidecar_pid(self) -> int | None:
        """PID of the current sidecar process (ops/tests); None if unknown."""
        if self._process is not None and self._process.poll() is None:
            return self._process.pid
        return None

    # --- internals --------------------------------------------------------

    def _build_command(self) -> list[str]:
        if self._command is not None:
            return list(self._command)
        entry = os.environ.get(ENV_ENTRY) or _default_entry()
        cmd = ["node", str(entry), "--workspace", str(self._workspace), "--port", "0"]
        if self._model:
            cmd += ["--model", self._model]
        return cmd

    async def _spawn(self) -> dict:
        """Spawn the sidecar and read the ``SIDECAR_READY {json}`` line.

        stderr is appended to ``<workspace>/.data/sidecar.log`` (mirror of
        worker.log, §7.3). Raises with the stderr tail when the ready line
        never arrives (exit, EOF, or timeout) — the half-spawned process is
        killed first. After the handshake the reader thread keeps draining
        stdout so an unexpected post-boot write can never fill the pipe and
        wedge the child.
        """
        self._stderr_log.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command()
        stderr_fh = open(self._stderr_log, "ab")  # noqa: SIM115 - closed in _reap
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=stderr_fh
            )
        except BaseException:
            stderr_fh.close()
            raise
        try:
            payload = await asyncio.to_thread(self._read_ready, proc)
        except BaseException:
            # Never leave a half-spawned process (or pipe/log handle) behind.
            await asyncio.to_thread(self._kill_quiet, proc)
            if proc.stdout is not None:
                proc.stdout.close()
            stderr_fh.close()
            raise
        # Success: proc.stdout stays open — the reader thread keeps draining it
        # until EOF, so an unexpected post-boot write can never fill the pipe
        # and wedge the child. Closed by the next _reap.
        self._process = proc
        self._stderr_fh = stderr_fh  # adopted: closed by the next _reap
        return payload

    def _read_ready(self, proc: subprocess.Popen) -> dict:
        """Scan stdout for ``SIDECAR_READY {json}`` with a hard timeout (a
        sidecar that never announces must not hang the caller). Raises with
        the exit code (if any) plus the last stderr log line — that tail is
        the boot-error channel (trap T10)."""
        result: dict | None = None
        error: str | None = None
        done = threading.Event()

        def reader() -> None:
            nonlocal result, error
            assert proc.stdout is not None
            announced = False
            for line in proc.stdout:
                text = line.decode("utf-8", "replace")
                if not announced and text.startswith(READY_PREFIX):
                    try:
                        result = json.loads(text[len(READY_PREFIX) :])
                    except ValueError as exc:
                        error = f"malformed {READY_PREFIX.strip()} payload: {exc}"
                        break
                    announced = True
                    done.set()  # handshake done; keep draining quietly
                # post-handshake lines are discarded (stdout hygiene by the sidecar)
            if not announced:
                code = proc.poll()
                tail = _stderr_tail(self._stderr_log)
                error = (
                    f"sidecar exited before ready (exit={code})"
                    + (f": {tail}" if tail else "")
                )
            done.set()

        threading.Thread(target=reader, daemon=True).start()
        if not done.wait(self._boot_timeout_s):
            error = f"sidecar did not announce ready within {self._boot_timeout_s:g}s"
        if result is None:
            raise RuntimeError(error or "sidecar never announced ready")
        return result

    def _reason_from(self, exc: Exception) -> str:
        """Human reason for a spawn failure, with an npm-install hint when the
        entry's package dir has dependencies declared but not installed
        (trap T12)."""
        reason = str(exc) or type(exc).__name__
        try:
            entry = Path(self._build_command()[1]).resolve()
            pkg_dir = entry.parents[1]  # <pkg>/src/<entry>.ts → <pkg>
            if (pkg_dir / "package.json").exists() and not (pkg_dir / "node_modules").exists():
                reason += f" — run `npm install` in {pkg_dir.name}/"
        except Exception:
            pass
        return reason

    def _adopt(self, payload: dict) -> None:
        """Record the handshake payload; the process was stored by ``_spawn``."""
        self._port = int(payload["port"])
        self._model_id = payload.get("model")
        self._reason = None

    def _kill_quiet(self, proc: subprocess.Popen) -> None:
        """Kill and reap a half-spawned process. Blocking — run in a thread."""
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.SubprocessError):
            proc.wait(timeout=10)

    def _reap(self) -> None:
        """Terminate the current process (terminate → 5 s → kill); close the
        stdout pipe and the stderr log handle. Blocking — run in a thread."""
        proc, self._process = self._process, None
        fh, self._stderr_fh = self._stderr_fh, None
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                proc.wait(timeout=STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
                with contextlib.suppress(subprocess.SubprocessError):
                    proc.wait(timeout=STOP_WAIT_S)
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
        if fh is not None:
            fh.close()

    def _emit(self, event: dict) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:  # a bad sink must not kill supervision
                logger.exception("sidecar event subscriber raised")

    def _set_state(self, state: str) -> None:
        self._state = state
        if state == "ready":
            self._reason = None
        self._emit({"type": "chat", **self.chat_info()})

    def _down(self, reason: str) -> None:
        self._reason = reason
        logger.error("chat sidecar down: %s", reason)
        self._set_state("down")

    # --- restart path -----------------------------------------------------

    def _schedule_restart(self, reason: str) -> None:
        """Kick a background restart unless one is already in progress."""
        if self._restart_task is not None and not self._restart_task.done():
            return
        logger.warning("scheduling sidecar restart: %s", reason)
        self._restart_task = asyncio.create_task(self._restart(reason), name="sidecar-restart")

    async def _restart(self, last_reason: str) -> None:
        """Replace a dead sidecar. Crash-loop guard: at most ``restart_limit``
        restarts within ``restart_window_s`` (design §9.1: ONE); the next
        death in the window flips the manager ``down`` — hub and worker keep
        running, ``/api/chat/*`` serves 503."""
        await asyncio.to_thread(self._reap)
        while True:
            self._prune_restart_times()
            if len(self._restart_times) >= self._restart_limit:
                reason = _stderr_tail(self._stderr_log) or last_reason
                self._down(f"sidecar crashed repeatedly; chat disabled — {reason}")
                return
            self._restart_times.append(time.monotonic())
            self._restarts += 1
            self._set_state("restarting")
            try:
                payload = await self._spawn()
                break
            except Exception as exc:
                last_reason = self._reason_from(exc)
                logger.warning("sidecar spawn failed (%s); retrying", last_reason)
                await asyncio.sleep(0.5)  # loop re-checks the crash guard
        self._adopt(payload)
        self._set_state("ready")

    def _prune_restart_times(self) -> None:
        cutoff = time.monotonic() - self._restart_window_s
        while self._restart_times and self._restart_times[0] < cutoff:
            self._restart_times.popleft()

    # --- health polling ----------------------------------------------------

    async def _health_loop(self) -> None:
        """Poll ``GET /health``; restart on process death or repeated failure.

        A hung turn is NOT unhealthiness (§7.4): the sidecar serves /health
        while busy. Process death is checked first via ``poll()`` — that is
        the definitive signal and needs no HTTP."""
        assert self._client is not None
        failures = 0
        while True:
            await asyncio.sleep(self._health_interval_s)
            if self._state in ("starting", "restarting", "down"):
                continue
            proc = self._process
            if proc is not None and proc.poll() is not None:
                self._schedule_restart(f"sidecar process exited (code={proc.returncode})")
                continue
            healthy = await self._check_health()
            failures = 0 if healthy else failures + 1
            if failures >= HEALTH_FAILURE_LIMIT:
                failures = 0
                self._schedule_restart("sidecar health check failed")

    async def _check_health(self) -> bool:
        assert self._client is not None
        base = self.base_url()
        if base is None:
            return False
        try:
            resp = await self._client.get(f"{base}/health")
            return resp.status_code == 200 and bool(resp.json().get("ok"))
        except Exception:
            return False


__all__ = [
    "BOOT_TIMEOUT_S",
    "NODE_MIN",
    "RESTART_LIMIT",
    "SidecarManager",
    "node_gate_reason",
    "node_version",
]
