"""Render worker server (design.md §5, handoff M3.1).

A programmatic ``bokeh.server.Server`` hosting **one shared session document**
at app path ``/bm`` — the live document the SPA will embed (M3.4) — plus the
control API (``POST /run``, ``GET /health``, ``GET /doc.json``) on the same
port via ``extra_patterns``.

Facts this is built on (verified against bokeh 3.9.2, handoff §5):
- ``Server(...)`` binds its socket immediately; nothing is served until
  ``io_loop.start()`` (trap T1) — the shared session is created beforehand via
  ``io_loop.run_sync``.
- Session creation is async/idempotent on the ApplicationContext
  (``srv._tornado._applications["/bm"]``); retrieval is
  ``srv.get_session("/bm", session_id)``.
- ``unused_session_lifetime_milliseconds`` must be raised: the default 15 s
  would garbage-collect the shared session with no browser connected (trap
  T5). Note the handoff's shorter name ``unused_session_lifetime`` is silently
  swallowed by BokehTornado's ``**kwargs`` — the long name is the real one.
- Document mutations happen under ``session.with_document_locked`` (trap T2).
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING
from urllib.parse import urlsplit

from bokeh.core.types import ID
from bokeh.server.server import Server

from better_mad.sdk import ENV_DATA_DIR
from better_mad.worker.control import WorkerState, build_control_handlers
from better_mad.worker.executor import DEFAULT_RUN_TIMEOUT_S

if TYPE_CHECKING:
    from bokeh.server.session import ServerSession

DEFAULT_SESSION_ID = "bm-main"
SESSION_LIFETIME_MS = 24 * 3600 * 1000  # shared session must outlive any browser (trap T5)
_CLIENT_HEADROOM_S = 15.0  # HTTP-client timeout on top of the run timeout


@dataclass
class WorkerHandle:
    """Handle to a render worker: identity fields, a thin HTTP client, stop().

    Two constructions:
    - **client-side** (tests / M3.2 hub via ``lifecycle.start_worker``):
      ``process`` is the spawned worker subprocess and ``stop()`` terminates it;
    - **server-side** (``boot_worker``, in-process): ``process`` is ``None`` and
      ``stop()`` unwinds the IOLoop instead.
    """

    process: subprocess.Popen | None
    port: int
    session_id: str
    base_url: str
    server: Server | None = field(default=None, repr=False)
    state: WorkerState | None = field(default=None, repr=False)
    _stderr_fh: IO[bytes] | None = field(default=None, repr=False)

    # --- HTTP client (explicit timeouts everywhere — trap T1) ---

    def run(
        self,
        script_path: Path | str,
        timeout_s: float | None = None,
    ) -> dict:
        """POST /run; returns the parsed result JSON.

        Raises ``urllib.error.HTTPError`` on non-200 (e.g. 409 busy). The
        client-side timeout is the run timeout plus headroom, so a slow-but-
        honest render never trips it while a hung run still fails fast enough.
        """
        body: dict = {"script": str(script_path)}
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        client_timeout = (timeout_s or DEFAULT_RUN_TIMEOUT_S) + _CLIENT_HEADROOM_S
        req = urllib.request.Request(
            f"{self.base_url}/run",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return json.loads(self._read(req, client_timeout))

    def health(self) -> dict:
        req = urllib.request.Request(f"{self.base_url}/health", method="GET")
        return json.loads(self._read(req, _CLIENT_HEADROOM_S))

    def doc_json(self) -> str:
        """GET /doc.json — the raw snapshot JSON text (or ``{"empty": true}``)."""
        req = urllib.request.Request(f"{self.base_url}/doc.json", method="GET")
        return self._read(req, _CLIENT_HEADROOM_S).decode("utf-8")

    @staticmethod
    def _read(req: urllib.request.Request, timeout: float) -> bytes:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    # --- lifecycle ---

    def stop(self, *, wait_s: float = 10.0) -> None:
        """Stop the worker: terminate the subprocess, or unwind the IOLoop for
        an in-process boot."""
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=wait_s)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=wait_s)
            if self.process.stdout is not None:
                self.process.stdout.close()
            if self._stderr_fh is not None:
                self._stderr_fh.close()
        elif self.server is not None:
            self.server.io_loop.add_callback(self.server.io_loop.stop)


def _patch_hv_callback_locking() -> None:
    """Mark hv's deferred stream processors as lock-taking (fixes zoom re-render).

    Root cause (verified 2026-11-03, user-confirmed in-browser): hv schedules
    `Callback.process_on_event/process_on_change` through panel's
    `async_execute`, which checks `getattr(func, "lock", False)`: without the
    marker the callback is scheduled **nolock**, so bokeh's session does not
    wrap it in `with_document_locked`, `_pending_writes` stays None, and the
    first model mutation inside `Stream.trigger` raises
    ``RuntimeError: _pending_writes should be non-None…`` — swallowed by
    panel's exception handler, so `rangesupdate` events (wheel zoom) never
    re-rasterize. With `.lock = True` panel schedules via
    ``doc.add_next_tick_callback`` → session-locked → pushes work.

    Exactly what panel does for its own server callbacks
    (``panel.io.document.with_lock`` sets the same marker); idempotent and
    process-wide — the worker is a dedicated process, so this is safe.
    """
    from holoviews.plotting.bokeh.callbacks import Callback

    Callback.process_on_event.lock = True  # ty: ignore[unresolved-attribute]
    Callback.process_on_change.lock = True  # ty: ignore[unresolved-attribute]


def boot_worker(
    data_dir: Path | str,
    *,
    port: int = 0,
    session_id: str = DEFAULT_SESSION_ID,
    extra_websocket_origins: Sequence[str] | None = None,
    ready: Callable[[WorkerHandle], None] | None = None,
) -> WorkerHandle:
    """Build the bokeh Server + shared session + control API and serve (blocks).

    Sets ``BETTER_MAD_DATA_DIR`` for the script environment (``bm.data()``
    reads it). ``ready`` fires with the handle just before the IOLoop starts —
    the process entry point uses it to announce the ephemeral port. Blocks
    until :meth:`WorkerHandle.stop` or process exit.

    ``extra_websocket_origins``: additional origins (``host:port`` or full
    URLs, scheme stripped) allowed to open the live-document websocket —
    needed when the embedding page is served from another origin (the M3.2
    hub/SPA, or the manual demo's static server). Bokeh's ``Server`` takes
    these as ``allow_websocket_origin``; note a **non-empty list REPLACES
    bokeh's default self-origin allowlist** (``create_hosts_allowlist`` only
    adds ``localhost:<port>`` when the list is empty) — include the worker's
    own origin explicitly if anything should embed from it. ``file://``
    pages (``Origin: null``) are always rejected.
    """
    os.environ[ENV_DATA_DIR] = str(Path(data_dir).resolve())

    _patch_hv_callback_locking()

    state = WorkerState(session_id=session_id)
    srv = Server(
        {"/bm": lambda doc: None},  # new sessions (browsers connecting w/o our id) start empty
        port=port,
        address="127.0.0.1",
        extra_patterns=build_control_handlers(state),
        unused_session_lifetime_milliseconds=SESSION_LIFETIME_MS,
        allow_websocket_origin=_normalize_origins(extra_websocket_origins),
    )
    app_ctx = srv._tornado._applications["/bm"]
    srv.start()  # install background tasks; the loop itself starts below

    # Create the shared session before serving: async and idempotent (§5.2).
    async def _create_session() -> ServerSession:
        return await app_ctx.create_session_if_needed(ID(session_id))

    state.session = srv.io_loop.run_sync(_create_session)

    if srv.port is None:  # unbound socket (cannot happen: bound in the constructor)
        raise RuntimeError("server socket is not bound")

    handle = WorkerHandle(
        process=None,
        port=srv.port,
        session_id=session_id,
        base_url=f"http://127.0.0.1:{srv.port}",
        server=srv,
        state=state,
    )
    if ready is not None:
        ready(handle)
    srv.io_loop.start()  # blocks; stop() unwinds it
    return handle


def _normalize_origins(origins: Sequence[str] | None) -> list[str]:
    """Allowlist entries as ``host:port`` netlocs (bokeh compares netlocs,
    not full URLs — ``ws.py`` extracts ``urlparse(origin).netloc``)."""
    out: list[str] = []
    for o in origins or []:
        netloc = urlsplit(o if "://" in o else f"//{o}").netloc.lower()
        if netloc:
            out.append(netloc)
    return out


__all__ = [
    "DEFAULT_SESSION_ID",
    "WorkerHandle",
    "WorkerState",
    "boot_worker",
]
