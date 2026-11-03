"""Tornado control API for the render worker (handoff §4.2).

Served on the same port as the ``/bm`` bokeh application via ``extra_patterns``
(localhost only; consumed by the hub in M3.2):

- ``POST /run``  ``{"script": <abs path>, "timeout_s"?: float}`` → executes the
  script, returns ``{"status", "run_seconds", "stdout_tail", "stderr_tail"}``.
  Busy policy: ``409 {"error": "run in progress"}`` if a run is active.
- ``GET /health`` → ``{"ok", "session_id", "runs", "last_status", "started"}``.
- ``GET /doc.json`` → the shared-document snapshot JSON, or ``{"empty": true}``
  before any figure was shown successfully.

Handlers are plain classes taking ``state`` via tornado's per-route ``initialize``
kwargs (3-tuple route form); the routes are built by :func:`build_control_handlers`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tornado.web
from bokeh.core.json_encoder import serialize_json

from better_mad.worker.executor import DEFAULT_RUN_TIMEOUT_S, RunResult, run_script

if TYPE_CHECKING:
    from bokeh.server.session import ServerSession


@dataclass
class WorkerState:
    """Server-side mutable state shared by the control handlers and boot code.

    All access happens on the bokeh IOLoop thread — runs execute there too
    (trap T8) — so plain attributes suffice; no locks.
    """

    session_id: str
    started: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    session: ServerSession | None = None
    runs: int = 0
    last_status: str | None = None
    running: bool = False
    ever_shown: bool = False
    last_plot: Any = None  # hv Plot, kept referenced: DynamicMap callbacks (trap T6)


class RunBusyError(RuntimeError):
    """A run was requested while another run is still active (API contract §4.2)."""


class RunHandler(tornado.web.RequestHandler):
    """POST /run — executes plot.py in-process, synchronously on the IOLoop.

    Note on the busy policy: synchronous runs occupy the IOLoop thread, so
    concurrent /run requests cannot even be accepted while one executes — the
    409 guard exists for the window where a finished run is still awaiting the
    document-lock swap, and for any future async scheduling.
    """

    state: WorkerState

    def initialize(self, *args: Any, **kwargs: Any) -> None:
        # tornado injects per-route kwargs (3-tuple route form, see
        # build_control_handlers); signatures match RequestHandler's *args/**kwargs.
        self.state = kwargs["state"]

    # ty: ignore[invalid-method-override] - tornado types handlers as sync callables; async post is the sanctioned form
    async def post(self, *args: Any, **kwargs: Any) -> None:
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "request body must be JSON"})
            return
        script = body.get("script")
        if not isinstance(script, str) or not script:
            self.set_status(400)
            self.write({"error": "missing 'script': absolute path to plot.py"})
            return
        timeout_s = body.get("timeout_s", DEFAULT_RUN_TIMEOUT_S)
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or timeout_s <= 0:
            self.set_status(400)
            self.write({"error": "'timeout_s' must be a positive number"})
            return

        try:
            result = await execute_run(self.state, Path(script), float(timeout_s))
        except RunBusyError:
            self.set_status(409)
            self.write({"error": "run in progress"})
            return
        self.write(
            {
                "status": result.status,
                "run_seconds": result.run_seconds,
                "stdout_tail": result.stdout_tail,
                "stderr_tail": result.stderr_tail,
            }
        )


class HealthHandler(tornado.web.RequestHandler):
    """GET /health — liveness plus run counters for the hub."""

    state: WorkerState

    def initialize(self, *args: Any, **kwargs: Any) -> None:
        self.state = kwargs["state"]

    def get(self, *args: Any, **kwargs: Any) -> None:
        self.write(
            {
                "ok": True,
                "session_id": self.state.session_id,
                "runs": self.state.runs,
                "last_status": self.state.last_status,
                "started": self.state.started,
            }
        )


class DocJsonHandler(tornado.web.RequestHandler):
    """GET /doc.json — snapshot of the shared document for hub-side retention.

    ``serialize_json(doc.to_json())`` because plain ``to_json()`` returns a
    ``Serialized`` wrapper, not a dict (trap T3).

    Fidelity note for M3.2: bokeh 3.x keeps ndarray buffers *out* of this JSON
    (refs by id only), so ``Document.from_json_string`` on this payload raises
    ``DeserializationError`` — the snapshot is structural, not restorable.
    Hub-side last-good restore should re-execute the stored last-good script
    (handoff §4.4 fallback) or speak the websocket protocol.
    """

    state: WorkerState

    def initialize(self, *args: Any, **kwargs: Any) -> None:
        self.state = kwargs["state"]

    def get(self, *args: Any, **kwargs: Any) -> None:
        self.set_header("Content-Type", "application/json")
        if self.state.ever_shown and self.state.session is not None:
            self.write(serialize_json(self.state.session.document.to_json()))
        else:
            self.write({"empty": True})


def build_control_handlers(
    state: WorkerState,
) -> list[tuple[str, type[tornado.web.RequestHandler], dict]]:
    """Control-API routes bound to ``state`` — pass as ``Server(extra_patterns=...)``."""
    return [
        (r"/run$", RunHandler, {"state": state}),
        (r"/health$", HealthHandler, {"state": state}),
        (r"/doc\.json$", DocJsonHandler, {"state": state}),
    ]


async def execute_run(
    state: WorkerState,
    script_path: Path,
    timeout_s: float = DEFAULT_RUN_TIMEOUT_S,
) -> RunResult:
    """Run a script against the shared document and update worker state.

    Run semantics (handoff §4.3):
    - success with ≥1 ``bm.show()`` → the last figure's bokeh model atomically
      replaces the document roots under the session lock;
    - script exception → error result, document untouched (last good stays);
    - no ``bm.show()`` at all → error result with the placeholder message,
      document untouched;
    - watchdog timeout → the *process* dies inside :func:`run_script` and this
      never returns (hub-side restart, M3.2);
    - the worker itself stays up after error runs (recycling is the hub's call).
    """
    if state.running:
        raise RunBusyError("run in progress")
    if state.session is None:
        raise RuntimeError("worker session not initialized")

    state.running = True
    try:
        result = run_script(script_path, timeout_s=timeout_s, document=state.session.document)
        if result.status == "ok":
            if result.plot is None:
                result = RunResult(
                    status="error",
                    run_seconds=result.run_seconds,
                    stdout_tail=result.stdout_tail,
                    stderr_tail="script ran but never called bm.show()",
                )
            else:
                await _attach_last_plot(state, result.plot)
        state.runs += 1
        state.last_status = result.status
        return result
    finally:
        state.running = False


def _apply_fit_sizing(root: Any) -> None:
    """Fit the attached figure to its embedding container (design §7).

    The v1 ``fit_container_hook`` equivalent (``archive/codebase``,
    app/layers.py) — now applied by the worker at attach time instead of via
    per-element opts hooks. Scripts are told (skills) not to set width/height;
    zero values from copy-pasted snippets are cleared rather than trusted.
    """
    from bokeh.models import Plot

    def apply(model: Any) -> None:
        model.sizing_mode = "stretch_both"
        if getattr(model, "width", None) == 0:
            model.width = None
        if getattr(model, "height", None) == 0:
            model.height = None

    if isinstance(root, Plot):
        apply(root)
    else:  # Layout/NdLayout state: bokeh Column/Row — stretch it and its plots
        apply(root)
        for plot_model in root.select({"type": Plot}):
            apply(plot_model)


async def _attach_last_plot(state: WorkerState, plot: Any) -> None:
    """Swap the shared document's roots to ``plot.state`` under the session lock.

    ``plot`` is the HoloViews plot object produced inside the run; rendering
    already happened there (eager), so a script that failed never reaches this
    and the document keeps its previous roots. The lock is what pushes changes
    to connected websocket clients (trap T2).
    """
    assert state.session is not None
    session = state.session

    _apply_fit_sizing(plot.state)  # before add_root: not yet in a doc → no patches

    def swap() -> None:
        doc = session.document
        for root in list(doc.roots):
            doc.remove_root(root)
        doc.add_root(plot.state)

    # ty: ignore[invalid-await] - @_needs_document_lock turns this into a coroutine at runtime
    await session.with_document_locked(swap)
    # Keep the hv plot alive: dropping it GCs the DynamicMap range callbacks (trap T6).
    state.last_plot = plot
    state.ever_shown = True
