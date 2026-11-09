"""FastAPI hub (design §9, handoff M3.2): the process the SPA talks to.

Owns the workspace (core), the render worker (via :class:`WorkerManager`),
and the ``plot.py`` watcher. Serves the REST + SSE API under ``/api`` (the
full contract is handoff §4) plus the built SPA from ``web/dist`` when it
exists; otherwise a minimal placeholder page.

**Reserved:** ``/api/chat/*`` belongs to the M3.3 chat sidecar proxy (design
§9.1, pwb transport contract) and is deliberately NOT implemented here —
nothing else may live under that path until the sidecar lands.

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

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from better_mad.core.dataset import Dataset
from better_mad.core.loading import LoaderError, ParserSettings
from better_mad.core.workspace import Workspace
from better_mad.hub.watcher import PlotWatcher
from better_mad.worker.manager import WorkerManager

logger = logging.getLogger("better_mad.hub")

SSE_HEARTBEAT_S = 20.0  # heartbeat comment cadence on /api/events (handoff §4)

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
        watcher_interval_s: float = 0.3,
        watcher_stability_s: float = 0.5,
    ) -> None:
        self.ws = ws
        self.manager = manager
        self.auto_run = True  # handoff §6: auto-run defaults ON, togglable
        self.last_run: dict | None = None
        self._queues: list[asyncio.Queue[dict]] = []
        self._run_task: asyncio.Task | None = None
        self._unsubscribe_manager: Callable[[], None] | None = None
        self._started = False
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
        self.watcher.start()

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self.watcher.stop()
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            self._run_task = None
        if self._unsubscribe_manager is not None:
            self._unsubscribe_manager()
            self._unsubscribe_manager = None
        await self.manager.stop()

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
    static_dir: Path | None = None,
    watcher_interval_s: float = 0.3,
    watcher_stability_s: float = 0.5,
) -> FastAPI:
    """Assemble the hub FastAPI app (routes + lifespan + optional SPA mount).

    ``static_dir``: a built SPA (``web/dist``). When ``None``, a couple of
    conventional locations are probed (cwd, dev checkout root); if nothing is
    found, ``/`` serves a placeholder page. The mount is registered **after**
    all ``/api`` routes, so static files can never shadow the API.
    """
    state = HubState(
        ws,
        manager,
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
        # Total by design: worker state comes from the manager, which degrades
        # to "down" — the hub itself never dies with the worker (handoff §5.5).
        return {"ok": True, "workspace": str(ws.path), "worker": manager.worker_info()}

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

    # --- events (SSE) ---------------------------------------------------------

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
