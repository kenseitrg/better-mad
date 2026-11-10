"""Hub end-to-end tests (handoff M3.2 §10) — headless.

A module-scoped fixture spawns ONE real worker (``python -m better_mad.worker``,
~3-5 s) under a WorkerManager and drives the FastAPI app two ways:

- REST: ``httpx.AsyncClient(ASGITransport(app))`` on the session loop, with
  ``HubState.start/stop`` called directly — no uvicorn boot for startup (W6).
- SSE: a REAL uvicorn server on an ephemeral port (lifespan off; the fixture
  still owns the lifecycle). Deviation from W6's letter, forced by httpx:
  ``ASGITransport`` buffers the whole body and yields it only after the ASGI
  call completes, so an infinite SSE stream is unreadable through it. W8's
  ``client.stream`` + readline contract needs a real socket; uvicorn serves
  exactly that, and the same app/lifecycle machinery is exercised.

The crash-loop scenario builds its own manager (short restart limit) on its
own workspace. Every wait has a hard timeout. Kill -9 happens while idle
(trap W2 allows idle or mid-run; ``poll()`` detects both).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import signal
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient

from better_mad.core.workspace import STARTER_SCRIPT, create_workspace
from better_mad.hub.app import create_app
from better_mad.worker.manager import WorkerManager

FIXTURES = Path(__file__).parent / "fixtures"

HUB_ORIGIN = "127.0.0.1:5999"  # placeholder: tests never open real websockets
EVENT_WINDOW_S = 45.0  # a run takes ~2-4 s; worker spawn ~3-5 s
SETTLE_GUARD_S = 2.5  # "nothing happened" observation window
STATE_WINDOW_S = 30.0  # worker (re)start waits

POINTS_SCRIPT = """
import holoviews as hv
import better_mad.sdk as bm
df = bm.data('sample_ws')
pts = hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT'], vdims=['TR_DOMFREQ'])
bm.show(pts.opts(color='TR_DOMFREQ', cmap='viridis', size=2))
"""

CURVE_SCRIPT = """
import holoviews as hv
import better_mad.sdk as bm
df = bm.data('sample_ws')
bm.show(hv.Curve(df, kdims=['XCORD_MIDPT'], vdims=['TR_DOMFREQ']))
"""

SECOND_CURVE_SCRIPT = (
    "# a distinct auto-run probe\n" + CURVE_SCRIPT
)  # different bytes, same glyph


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
async def hub(tmp_path_factory) -> AsyncIterator[SimpleNamespace]:
    """One real worker + hub app shared by the whole module (handoff §10).

    REST goes through ASGITransport (in-process); SSE through a real uvicorn
    on an ephemeral port — see the module docstring for why both exist.
    """
    root = tmp_path_factory.mktemp("hub")
    ws = create_workspace(root / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    manager = WorkerManager(
        ws.data_dir,
        HUB_ORIGIN,
        script_path=ws.script_path,
        health_interval_s=0.25,
    )
    app = create_app(ws, manager, watcher_interval_s=0.05, watcher_stability_s=0.15)
    state = app.state.hub
    await state.start()

    # Real-socket server for SSE (lifespan off: the fixture owns start/stop).
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, lifespan="off", log_level="warning"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(), name="sse-server")
    await _wait_until(lambda: server.started, 15)
    sse_port = int(server.servers[0].sockets[0].getsockname()[1])
    try:
        yield SimpleNamespace(
            app=app,
            state=state,
            manager=manager,
            ws=ws,
            sse_url=f"http://127.0.0.1:{sse_port}",
        )
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server_task, timeout=10)
        await state.stop()


def _client(hub: SimpleNamespace) -> AsyncClient:
    """Hub REST client (ASGI in-process — no uvicorn boot, trap W6)."""
    return AsyncClient(transport=ASGITransport(app=hub.app), base_url="http://test")


def _sse_client(hub: SimpleNamespace) -> AsyncClient:
    """Real-socket client for /api/events (ASGITransport buffers bodies)."""
    return AsyncClient(base_url=hub.sse_url)


def _worker_port(hub: SimpleNamespace) -> int:
    info = hub.manager.embed_info()
    assert info["server_url"] is not None
    return int(info["server_url"].split(":")[2].split("/")[0])


# --- waiting helpers --------------------------------------------------------


async def _wait_until(predicate, timeout_s: float, poll_s: float = 0.1) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"condition not met within {timeout_s:g}s")


async def _read_events(
    client: AsyncClient,
    *,
    action=None,
    until,
    window_s: float = EVENT_WINDOW_S,
    settle_s: float = 0.0,
) -> list[dict]:
    """Stream ``/api/events``: wait for ``hello``, run ``action`` (sync or
    async), read until ``until(events)`` is true, then keep reading
    ``settle_s`` more. Hard timeouts everywhere (trap W8)."""
    events: list[dict] = []
    hello_seen = asyncio.Event()

    async def reader() -> None:
        async with client.stream("GET", "/api/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue  # heartbeat comment or blank
                event = json.loads(line[len("data: ") :])
                events.append(event)
                if event.get("type") == "hello":
                    hello_seen.set()
                if until(events):
                    return

    task = asyncio.create_task(reader())
    try:
        await asyncio.wait_for(hello_seen.wait(), timeout=10)
        if action is not None:
            result = action()
            if inspect.isawaitable(result):
                await result
        loop = asyncio.get_running_loop()
        deadline = loop.time() + window_s
        while not until(events):
            if loop.time() > deadline:
                raise TimeoutError(f"events not observed within {window_s:g}s")
            await asyncio.sleep(0.05)
        if settle_s:
            await asyncio.sleep(settle_s)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return events


def _types(events: list[dict]) -> list[str]:
    return [str(e["type"]) for e in events]


# --- shared-hub tests ---------------------------------------------------------


async def test_health_and_hello_snapshot(hub: SimpleNamespace) -> None:
    async with _client(hub) as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["workspace"] == str(hub.ws.path)
        assert body["worker"]["state"] == "ready"
        assert body["worker"]["epoch"] == 1

        async with _sse_client(hub) as sse:
            events = await _read_events(sse, until=lambda evs: bool(evs))
        hello = events[0]
        assert hello["type"] == "hello"
        for key in ("auto_run", "last_run", "worker", "watcher"):
            assert key in hello
        assert hello["auto_run"] is True
        assert hello["worker"]["state"] == "ready"


async def test_file_import_and_list(hub: SimpleNamespace) -> None:
    content = (FIXTURES / "sample_ws.txt").read_bytes()
    async with _client(hub) as client:
        resp = await client.post(
            "/api/files/import",
            files={"file": ("sample_ws.txt", content, "text/plain")},
            data={"settings": json.dumps({"delimiter": "auto", "comment": "#"})},
        )
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["name"].startswith("sample_ws")  # stem collision -> _2
        assert summary["rows"] > 0
        by_script = {c["script"]: c for c in summary["columns"]}
        freq = by_script["TR_DOMFREQ"]  # sanitized from hostile "TR.DOMFREQ"
        assert freq["original"] == "TR.DOMFREQ"
        assert freq["dtype"] == "float32"
        assert freq["min"] is not None and freq["max"] is not None
        assert freq["nan_pct"] == 0.0

        listing = (await client.get("/api/files")).json()
        entry = next(
            d for d in listing["datasets"] if d["name"] == summary["name"]
        )
        assert entry["columns"] == summary["columns"]

        bad = await client.post(
            "/api/files/import",
            files={"file": ("junk.txt", b"", "text/plain")},
        )
        assert bad.status_code == 400  # loader error degrades to a JSON 400
        bad_settings = await client.post(
            "/api/files/import",
            files={"file": ("sample_ws.txt", content, "text/plain")},
            data={"settings": '{"nonsense": 1}'},
        )
        assert bad_settings.status_code == 400


async def test_plot_roundtrip_and_autorun_events(hub: SimpleNamespace) -> None:
    async with _client(hub) as client:
        got = (await client.get("/api/plot.py")).json()
        assert got["content"] == STARTER_SCRIPT  # fresh workspace starter

        resp = await client.put("/api/plot.py", json={"content": POINTS_SCRIPT})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert (await client.get("/api/plot.py")).json()["content"] == POINTS_SCRIPT

        async with _sse_client(hub) as sse:
            events = await _read_events(
                sse,
                until=lambda evs: "plot_changed" in _types(evs)
                and "run_result" in _types(evs),
            )
        assert _types(events).index("plot_changed") < _types(events).index(
            "run_result"
        )
        run = next(e for e in events if e["type"] == "run_result")
        assert run["status"] == "ok"
        assert run["run_seconds"] > 0


async def test_run_and_embed(hub: SimpleNamespace) -> None:
    async with _client(hub) as client, AsyncClient() as direct:
        # plot.py currently holds POINTS (test above); make the glyph explicit.
        await client.put("/api/plot.py", json={"content": CURVE_SCRIPT})
        result = (await client.post("/api/run", json={})).json()
        assert result["status"] == "ok"
        assert result["epoch"] == 1

        embed = (await client.get("/api/plot/embed")).json()
        assert embed["session_id"] == "bm-main"
        assert embed["server_url"].endswith("/bm")
        assert embed["epoch"] == result["epoch"]

        health = (await direct.get(f"http://127.0.0.1:{_worker_port(hub)}/health")).json()
        assert health["ok"] is True  # embed port serves the real worker
        assert health["session_id"] == "bm-main"
        doc = await direct.get(f"http://127.0.0.1:{_worker_port(hub)}/doc.json")
        assert '"Line"' in doc.text  # the curve glyph is in the live document


async def test_broken_script_error_and_last_good(hub: SimpleNamespace) -> None:
    manager = hub.manager
    # last-good is the previous ok script (test_run_and_embed ran CURVE_SCRIPT)
    assert manager._last_good == CURVE_SCRIPT  # private by design; unit-level check

    async with _client(hub) as client, AsyncClient() as direct, _sse_client(hub) as sse:
        events = await _read_events(
            sse,
            action=lambda: client.put(
                "/api/plot.py",
                json={"content": "raise ValueError('boom')\n"},
            ),
            until=lambda evs: any(
                e["type"] == "run_result" and e["status"] == "error" for e in evs
            ),
        )
        # The ERROR result, not the first run_result: a stale ok event from a
        # previous test's watcher auto-run can land first (observed flake).
        run = next(e for e in events if e["type"] == "run_result" and e["status"] == "error")
        assert run["status"] == "error"
        assert "ValueError: boom" in run["stderr_tail"]  # traceback surfaced

        status = (await client.get("/api/status")).json()
        assert status["last_run"]["status"] == "error"
        assert status["auto_run"] is True

        health = (await direct.get(f"http://127.0.0.1:{_worker_port(hub)}/health")).json()
        assert health["ok"] is True  # worker stays up after an error run
        assert health["last_status"] == "error"


async def test_kill9_restarts_and_replays_last_good(hub: SimpleNamespace) -> None:
    manager = hub.manager
    pid = manager.worker_pid
    assert pid is not None

    def kill() -> None:
        os.kill(pid, signal.SIGKILL)  # idle kill — poll() detects it (W2)

    async with _client(hub) as client, AsyncClient() as direct, _sse_client(hub) as sse:
        events = await _read_events(
            sse,
            action=kill,
            until=lambda evs: any(
                e["type"] == "run_result" and e["status"] == "ok" and e["epoch"] == 2
                for e in evs
            ),
        )
        states = [(e["state"], e["epoch"]) for e in events if e["type"] == "worker"]
        assert ("restarting", 2) in states  # SSE worker event on restart
        assert ("ready", 2) in states

        body = (await client.get("/api/health")).json()
        assert body["worker"]["epoch"] == 2
        assert body["worker"]["restarts"] == 1
        assert body["worker"]["state"] == "ready"

        embed = (await client.get("/api/plot/embed")).json()
        assert embed["epoch"] == 2  # SPA would re-embed here

        health = (await direct.get(f"http://127.0.0.1:{_worker_port(hub)}/health")).json()
        assert health["runs"] >= 1  # the last-good script was RE-EXECUTED
        doc = await direct.get(f"http://127.0.0.1:{_worker_port(hub)}/doc.json")
        assert '"Line"' in doc.text  # last-good glyph (CURVE), not the broken one


async def test_autorun_off_swallows_runs(hub: SimpleNamespace) -> None:
    async with _client(hub) as client, _sse_client(hub) as sse:
        toggle = await client.post("/api/autorun", json={"on": False})
        assert toggle.json() == {"auto_run": False}

        events = await _read_events(
            sse,
            action=lambda: client.put(
                "/api/plot.py", json={"content": SECOND_CURVE_SCRIPT}
            ),
            until=lambda evs: "plot_changed" in _types(evs),
            settle_s=SETTLE_GUARD_S,
        )
        assert "plot_changed" in _types(events)
        assert "run_result" not in _types(events)  # watcher fired, no auto-run

        status = (await client.get("/api/status")).json()
        assert status["auto_run"] is False

        # re-enable: the next PUT runs again (restores shared-fixture state)
        await client.post("/api/autorun", json={"on": True})
        events = await _read_events(
            sse,
            action=lambda: client.put(
                "/api/plot.py",
                json={"content": CURVE_SCRIPT},
            ),
            until=lambda evs: any(
                e["type"] == "run_result" and e["status"] == "ok" for e in evs
            ),
        )
        assert any(e["type"] == "run_result" for e in events)


# --- crash-loop scenario: own manager on its own workspace -------------------


async def test_crash_loop_goes_down_and_degrades_gracefully(tmp_path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    manager = WorkerManager(
        ws.data_dir,
        HUB_ORIGIN,
        script_path=ws.script_path,
        health_interval_s=0.15,
        restart_limit=2,  # kill 3x -> down (handoff §10.6, short window allowed)
        restart_window_s=60.0,
    )
    app = create_app(ws, manager, watcher_interval_s=0.05, watcher_stability_s=0.15)
    state = app.state.hub
    await state.start()
    try:
        async with _client(SimpleNamespace(app=app)) as client:
            for i in range(3):
                await _wait_until(
                    lambda: manager.worker_info()["state"] == "ready", STATE_WINDOW_S
                )
                pid = manager.worker_pid
                assert pid is not None
                os.kill(pid, signal.SIGKILL)
                if i < 2:  # restarts happen (epochs 2, 3)
                    await _wait_until(
                        lambda i=i: manager.epoch == i + 2
                        and manager.worker_info()["state"] == "ready",
                        STATE_WINDOW_S,
                    )
            await _wait_until(
                lambda: manager.worker_info()["state"] == "down", STATE_WINDOW_S
            )

            health = (await client.get("/api/health")).json()
            assert health["ok"] is True  # the hub itself never dies
            assert health["worker"]["state"] == "down"

            result = (await client.post("/api/run", json={})).json()
            assert result["status"] == "error"
            assert result["stderr_tail"] == "worker down"

            files = (await client.get("/api/files")).json()
            assert files["datasets"]  # everything except /run keeps working
    finally:
        await state.stop()
