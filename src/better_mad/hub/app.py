"""FastAPI hub (design §9, handoff M3.2): the process the SPA talks to.

Owns the workspace (core), the render worker (via :class:`WorkerManager`), the
chat sidecar (via :class:`SidecarManager`, M3.3), and the ``plot.py`` watcher.
Serves the REST + SSE API under ``/api`` (the full contract is handoff §4)
plus the built SPA from ``web/dist`` when it exists; otherwise a minimal
placeholder page.

``/api/chat/*`` (M3.3): a dumb same-path passthrough proxy to the sidecar's
identical routes (§4) — bytes in, bytes out; the hub parses nothing beyond
transport (design §4.2). Sidecar down/starting → ``503``; SSE is streamed
with read-timeout disabled and the upstream closed on client disconnect
(trap T9).

Testability (trap W6): no uvicorn is booted in pytest. :meth:`HubState.start`
/:meth:`HubState.stop` are idempotent callables wired to the FastAPI lifespan,
so tests can call them directly on their own event loop and drive the app
with ``httpx.AsyncClient(transport=ASGITransport(app))``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from better_mad.core.dataset import Dataset
from better_mad.core.loading import LoaderError, ParserSettings
from better_mad.core.workspace import Workspace
from better_mad.hub.sidecar import SidecarManager
from better_mad.hub.watcher import PlotWatcher
from better_mad.worker.manager import WorkerManager

logger = logging.getLogger("better_mad.hub")

SSE_HEARTBEAT_S = 20.0  # heartbeat comment cadence on /api/events (handoff §4)
#: Buffered chat-proxy calls (send/stop/new/history answer immediately; the
#: sidecar streams over SSE, so a bounded timeout only guards a dead sidecar).
CHAT_PROXY_TIMEOUT_S = 10.0

_PLACEHOLDER_PAGE = (
    "<!doctype html><html><head><title>better-mad</title></head><body>"
    "<h1>better-mad hub</h1><p>API is up. The SPA is not built yet "
    "(<code>web/dist</code> missing — PLAN.md M3.4); endpoints live under "
    "<code>/api</code>.</p></body></html>"
)

_SETTING_KEYS = {"delimiter", "decimal", "sentinels", "comment", "float64_columns"}


class HubState:
    """Mutable hub state and run orchestration (watcher + auto-run coalescing).

    One instance per hub app; reachable as ``app.state.hub``. All methods run
    on the hub event loop.
    """

    def __init__(
        self,
        ws: Workspace,
        manager: WorkerManager,
        *,
        sidecar: SidecarManager | None = None,
        watcher_interval_s: float = 0.3,
        watcher_stability_s: float = 0.5,
    ) -> None:
        self.ws = ws
        self.manager = manager
        self.sidecar = sidecar  # None → /api/chat/* serves 503 (tests without Node)
        self.auto_run = True  # handoff §6: auto-run defaults ON, togglable
        self.last_run: dict | None = None
        self._queues: list[asyncio.Queue[dict]] = []
        self._run_task: asyncio.Task | None = None
        self._unsubscribe_manager: Callable[[], None] | None = None
        self._unsubscribe_sidecar: Callable[[], None] | None = None
        self._chat_client: httpx.AsyncClient | None = None
        self._started = False
        # Subscribe at construction (unlike the worker, whose subscription
        # happens in start()): tests may drive the sidecar directly without
        # HubState.start() (which also spawns the render worker), and chat
        # transitions must still fan out. One-shot apps: stop() unsubscribes.
        if sidecar is not None:
            self._unsubscribe_sidecar = sidecar.subscribe(self._on_sidecar_event)
        self.watcher = PlotWatcher(
            ws.script_path,
            self._on_plot_changed,
            interval_s=watcher_interval_s,
            stability_s=watcher_stability_s,
        )

    # --- lifecycle (idempotent; wired to the FastAPI lifespan) -----------

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._unsubscribe_manager = self.manager.subscribe(self._on_manager_event)
        await self.manager.start()
        if self.sidecar is not None:
            await self.sidecar.start()
        self.watcher.start()

    async def stop(self) -> None:
        started = self._started
        self._started = False
        if started:
            self.watcher.stop()
            if self._run_task is not None and not self._run_task.done():
                self._run_task.cancel()
                self._run_task = None
            if self._unsubscribe_manager is not None:
                self._unsubscribe_manager()
                self._unsubscribe_manager = None
            await self.manager.stop()
        if self.sidecar is not None:
            if self._unsubscribe_sidecar is not None:
                self._unsubscribe_sidecar()
                self._unsubscribe_sidecar = None
            await self.sidecar.stop()
        await self._close_chat_client()

    def _on_sidecar_event(self, event: dict) -> None:
        """Chat lifecycle events flow to SSE verbatim (``{"type": "chat", ...}``)."""
        self.emit(event)

    # --- shared proxy client ---------------------------------------------

    @property
    def chat_client(self) -> httpx.AsyncClient:
        """One shared client for /api/chat/* proxying (created lazily so tests
        that never proxy pay nothing)."""
        if self._chat_client is None:
            self._chat_client = httpx.AsyncClient(timeout=httpx.Timeout(CHAT_PROXY_TIMEOUT_S))
        return self._chat_client

    async def _close_chat_client(self) -> None:
        if self._chat_client is not None:
            await self._chat_client.aclose()
            self._chat_client = None

    # --- events (SSE fan-out) --------------------------------------------

    def subscribe_queue(self) -> asyncio.Queue[dict]:
        """Register one SSE client's event queue (call at stream connect)."""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._queues.append(queue)
        return queue

    def unsubscribe_queue(self, queue: asyncio.Queue[dict]) -> None:
        with contextlib.suppress(ValueError):
            self._queues.remove(queue)

    def emit(self, event: dict) -> None:
        """Fan an event out to every connected SSE client (loop-thread only)."""
        for queue in list(self._queues):
            queue.put_nowait(event)

    def _on_manager_event(self, event: dict) -> None:
        if event.get("type") == "run_result":
            self.last_run = {k: v for k, v in event.items() if k != "type"}
        self.emit(event)

    # --- status / orchestration ------------------------------------------

    def status(self) -> dict:
        """The ``/api/status`` snapshot (also embedded in the SSE ``hello``)."""
        return {
            "auto_run": self.auto_run,
            "last_run": self.last_run,
            "worker": self.manager.worker_info(),
            "watcher": {
                "last_change_ts": self.watcher.last_change_ts,
                "pending": self.watcher.pending,
            },
            "chat": self.sidecar.chat_info() if self.sidecar is not None else {"state": "disabled"},
        }

    def _on_plot_changed(self) -> None:
        """Watcher fired: announce, then auto-run if enabled (handoff §6)."""
        self.emit({"type": "plot_changed"})
        if self.auto_run:
            self._schedule_run()

    def _schedule_run(self) -> None:
        """Trigger an auto-run; coalesce changes that arrive mid-run.

        If a run is in flight, remember ``pending`` and run once afterwards —
        executing the LATEST script, not a queue (handoff §6).
        """
        if self._run_task is not None and not self._run_task.done():
            self.watcher.pending = True
            return
        self._run_task = asyncio.create_task(self._run_and_maybe_chain(), name="autorun")

    async def _run_and_maybe_chain(self) -> None:
        await self.manager.run()
        if self.watcher.pending:  # changes landed during the run: one follow-up
            self.watcher.pending = False
            self._run_task = asyncio.create_task(
                self._run_and_maybe_chain(), name="autorun-followup"
            )


class PlotContent(BaseModel):
    """Body of ``PUT /api/plot.py``."""

    content: str


class RunRequest(BaseModel):
    """Body of ``POST /api/run``."""

    timeout_s: float | None = None


class AutorunRequest(BaseModel):
    """Body of ``POST /api/autorun``."""

    on: bool


def create_app(
    ws: Workspace,
    manager: WorkerManager,
    *,
    sidecar: SidecarManager | None = None,
    static_dir: Path | None = None,
    watcher_interval_s: float = 0.3,
    watcher_stability_s: float = 0.5,
) -> FastAPI:
    """Assemble the hub FastAPI app (routes + lifespan + optional SPA mount).

    ``sidecar``: the chat sidecar manager (M3.3). ``None`` keeps ``/api/chat/*``
    total — it serves ``503`` with a "disabled" reason (tests without Node).

    ``static_dir``: a built SPA (``web/dist``). When ``None``, a couple of
    conventional locations are probed (cwd, dev checkout root); if nothing is
    found, ``/`` serves a placeholder page. The mount is registered **after**
    all ``/api`` routes, so static files can never shadow the API.
    """
    state = HubState(
        ws,
        manager,
        sidecar=sidecar,
        watcher_interval_s=watcher_interval_s,
        watcher_stability_s=watcher_stability_s,
    )
    plot_lock = asyncio.Lock()  # serializes PUT /api/plot.py writes

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await state.start()
        yield
        await state.stop()

    app = FastAPI(title="better-mad hub", lifespan=lifespan)
    app.state.hub = state

    # --- health & status ---------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        # Total by design: worker + sidecar state come from their managers,
        # which degrade to "down" — the hub itself never dies with either
        # (handoff §5.5 / §7).
        return {
            "ok": True,
            "workspace": str(ws.path),
            "worker": manager.worker_info(),
            "chat": sidecar.chat_info() if sidecar is not None else {"state": "disabled"},
        }

    @app.get("/api/status")
    async def status() -> dict:
        return state.status()

    # --- files ---------------------------------------------------------------

    @app.get("/api/files")
    async def list_files() -> dict:
        return {
            "datasets": [
                _dataset_summary(name, ds) for name, ds in ws.datasets.items()
            ]
        }

    @app.post("/api/files/import")
    async def import_file(
        file: Annotated[UploadFile, File()],
        settings: Annotated[str | None, Form()] = None,  # JSON of ParserSettings fields
    ) -> dict:
        parser_settings = _parse_settings(settings)
        original = Path(file.filename or "upload.txt").name or "upload.txt"
        dest = Path(tempfile.mkdtemp(prefix="better-mad-import-")) / original
        with dest.open("wb") as fh:
            await asyncio.to_thread(shutil.copyfileobj, file.file, fh)
        try:
            ds = await asyncio.to_thread(ws.add_file, dest, parser_settings)
        except LoaderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # W9: the temp path is recorded as the dataset source — accepted for M3.2.
        return _dataset_summary(ds.name, ds)

    # --- plot.py -------------------------------------------------------------

    @app.get("/api/plot.py")
    async def get_plot() -> dict:
        try:
            content = ws.script_path.read_text()
        except OSError as exc:
            raise HTTPException(status_code=404, detail=f"plot.py missing: {exc}") from exc
        return {"content": content}

    @app.put("/api/plot.py")
    async def put_plot(body: PlotContent) -> dict:
        async with plot_lock:
            try:
                current = ws.script_path.read_text()
            except OSError:
                current = None
            if current == body.content:
                return {"ok": True}  # unchanged write: don't churn the watcher
            tmp = ws.script_path.with_name(ws.script_path.name + ".tmp")
            tmp.write_text(body.content)
            os.replace(tmp, ws.script_path)  # atomic; the watcher takes it from here
        return {"ok": True}

    # --- run orchestration -----------------------------------------------------

    @app.post("/api/run")
    async def run(body: RunRequest | None = None) -> dict:
        result = await manager.run(timeout_s=body.timeout_s if body is not None else None)
        if result is None:
            # Worker down: degrade gracefully instead of raising (handoff §5.5).
            result = {
                "status": "error",
                "run_seconds": 0.0,
                "stdout_tail": "",
                "stderr_tail": "worker down",
                "epoch": manager.epoch,
            }
            state.last_run = result
            state.emit({"type": "run_result", **result})
        return result

    @app.post("/api/autorun")
    async def autorun(body: AutorunRequest) -> dict:
        state.auto_run = body.on
        return {"auto_run": state.auto_run}

    # --- embedding ---------------------------------------------------------------

    @app.get("/api/plot/embed")
    async def embed() -> dict:
        return manager.embed_info()

    # --- chat proxy (M3.3) -------------------------------------------------
    # Dumb same-path passthrough to the sidecar's identical routes (§4): no
    # interpretation, no rewriting — the hub parses nothing beyond transport
    # (design §4.2). Down/starting sidecar → 503 with its reason.

    def _chat_unavailable() -> JSONResponse:
        info = sidecar.chat_info() if sidecar is not None else {"state": "disabled"}
        reason = info.get("reason") or f"chat sidecar {info.get('state', 'disabled')}"
        return JSONResponse(
            {"error": "chat sidecar unavailable", "reason": reason}, status_code=503
        )

    async def _chat_passthrough(method: str, path: str, request: Request | None) -> Response:
        base = sidecar.base_url() if sidecar is not None else None
        if base is None:
            return _chat_unavailable()
        body = await request.body() if request is not None else None
        headers = (
            {"content-type": request.headers.get("content-type", "application/json")}
            if request is not None
            else None
        )
        try:
            upstream = await state.chat_client.request(
                method, f"{base}/api/chat/{path}", content=body, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.warning("chat proxy %s %s failed: %s", method, path, exc)
            return JSONResponse(
                {"error": "chat sidecar unavailable", "reason": f"sidecar unreachable: {exc}"},
                status_code=503,
            )
        # Status + body mapped through verbatim; content-type carried along.
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    @app.get("/api/chat/events")
    async def chat_events() -> Response:
        base = sidecar.base_url() if sidecar is not None else None
        if base is None:
            return _chat_unavailable()
        client = state.chat_client

        async def stream() -> AsyncIterator[bytes]:
            # Read-timeout disabled (trap T9): the sidecar heartbeats itself.
            # `async with` closes the upstream on client disconnect (the
            # generator is aclosed by StreamingResponse teardown).
            async with client.stream(
                "GET",
                f"{base}/api/chat/events",
                timeout=httpx.Timeout(CHAT_PROXY_TIMEOUT_S, read=None),
            ) as upstream:
                async for chunk in upstream.aiter_raw():
                    yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # W8
        )

    @app.post("/api/chat/send")
    async def chat_send(request: Request) -> Response:
        return await _chat_passthrough("POST", "send", request)

    @app.post("/api/chat/stop")
    async def chat_stop() -> Response:
        return await _chat_passthrough("POST", "stop", None)

    @app.post("/api/chat/new")
    async def chat_new() -> Response:
        return await _chat_passthrough("POST", "new", None)

    @app.get("/api/chat/history")
    async def chat_history() -> Response:
        return await _chat_passthrough("GET", "history", None)

    # --- events (SSE) ---------------------------------------------------------
    # Hub-side events: run results, worker lifecycle, chat lifecycle, embed
    # epoch (via hello). Chat-side SSE lives at /api/chat/events above.

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        queue = state.subscribe_queue()

        async def stream() -> AsyncIterator[str]:
            try:
                hello = {"type": "hello", **state.status()}
                yield f"data: {json.dumps(hello)}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_S)
                        yield f"data: {json.dumps(event)}\n\n"
                    except TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                state.unsubscribe_queue(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # W8
        )

    # --- SPA / placeholder -------------------------------------------------------

    resolved_static = static_dir if static_dir is not None else _default_static_dir()
    if resolved_static is not None and (resolved_static / "index.html").exists():
        # Mounted last so it can never shadow /api (routes match first).
        app.mount("/", StaticFiles(directory=resolved_static, html=True), name="spa")
    else:

        @app.get("/", include_in_schema=False)
        async def index() -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER_PAGE)

    return app


# --- helpers ---------------------------------------------------------------


def _dataset_summary(name: str, ds: Dataset) -> dict:
    """JSON view of one dataset — the ``/api/files`` entry shape (handoff §4)."""
    df = ds.df
    display: dict = df.attrs.get("display_names", {})
    columns = []
    for col in df.columns:
        s: pd.Series = df[col]
        has_values = bool(pd.api.types.is_numeric_dtype(s) and s.notna().any())
        columns.append(
            {
                "original": display.get(col, col),
                "script": col,
                "dtype": str(s.dtype),
                "min": float(s.min()) if has_values else None,
                "max": float(s.max()) if has_values else None,
                "nan_pct": round(100.0 * float(s.isna().mean()), 2),
            }
        )
    return {"name": name, "source": str(ds.path), "rows": len(df), "columns": columns}


def _parse_settings(raw: str | None) -> ParserSettings:
    """Parse the optional multipart ``settings`` JSON into ``ParserSettings``."""
    if not raw:
        return ParserSettings()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("settings must be a JSON object")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid settings: {exc}") from exc
    unknown = set(data) - _SETTING_KEYS
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown settings fields: {sorted(unknown)}")
    kwargs = dict(data)
    for key in ("sentinels", "float64_columns"):
        if kwargs.get(key) is not None:
            kwargs[key] = tuple(kwargs[key])
    try:
        return ParserSettings(**kwargs)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid settings: {exc}") from exc


def _default_static_dir() -> Path | None:
    """Probe conventional ``web/dist`` locations (M3.4 builds it there)."""
    candidates = [
        Path.cwd() / "web" / "dist",
        Path(__file__).resolve().parents[3] / "web" / "dist",  # dev checkout root
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return None
