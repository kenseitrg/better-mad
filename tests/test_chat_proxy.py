"""Chat proxy tests (handoff M3.3 §11): ``/api/chat/*`` passthrough from the
hub to a stub sidecar (tests/fixtures/stub_sidecar.py — no Node, no ~/.pi).

REST goes through ASGITransport (in-process, trap W6); SSE goes through a
real uvicorn on an ephemeral port — httpx ``ASGITransport`` buffers whole
bodies and cannot read an infinite stream (M3.2 deviation-1 pattern). The
SSE cases assert both directions: a stub ``text_delta`` read through the
hub, and the upstream SSE connection closed after the hub client disconnects
(trap T9 — the stub reports its open-connection count on /health).
``test_sidecar_down_serves_503`` runs last and leaves the sidecar down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from better_mad.core.workspace import create_workspace
from better_mad.hub.app import create_app
from better_mad.hub.sidecar import SidecarManager
from better_mad.worker.manager import WorkerManager

FIXTURES = Path(__file__).parent / "fixtures"
STUB = FIXTURES / "stub_sidecar.py"
SETTLE_S = 20.0


async def wait_until(predicate, timeout_s: float = SETTLE_S, poll_s: float = 0.1) -> None:
    """Poll a sync predicate on the loop until it holds (hard timeout)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(poll_s)
    raise TimeoutError("condition not met in time")


async def wait_ready(sidecar: SidecarManager, timeout_s: float = SETTLE_S) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if sidecar.chat_info()["state"] == "ready":
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"sidecar never reached ready: {sidecar.chat_info()}")


@pytest.fixture(scope="module")
async def proxy() -> AsyncIterator[SimpleNamespace]:
    """Hub app + stub sidecar; REST via ASGITransport, SSE via real uvicorn.

    ``HubState.start`` is deliberately NOT called — that would spawn the real
    render worker. The sidecar starts directly; ``state.stop()`` (which works
    without a prior ``start``) tears the sidecar + proxy client down.
    """
    root = Path(tempfile.mkdtemp(prefix="bm-chat-proxy-"))
    ws = create_workspace(root / "ws")
    manager = WorkerManager(ws.data_dir, "127.0.0.1:5999", script_path=ws.script_path)
    sidecar = SidecarManager(ws.path, command=[sys.executable, str(STUB)], health_interval_s=0.2)
    app = create_app(ws, manager, sidecar=sidecar)
    state = app.state.hub
    await sidecar.start()
    await wait_ready(sidecar)
    assert sidecar.base_url() is not None

    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(), name="sse-server")
    while not server.started:
        await asyncio.sleep(0.05)
    sse_port = int(server.servers[0].sockets[0].getsockname()[1])
    try:
        yield SimpleNamespace(
            app=app,
            state=state,
            sidecar=sidecar,
            ws=ws,
            sse_url=f"http://127.0.0.1:{sse_port}",
        )
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server_task, timeout=10)
        await state.stop()  # stops the sidecar + closes the shared proxy client


def _client(proxy: SimpleNamespace) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy.app), base_url="http://test"
    )


# --- REST mapping -------------------------------------------------------------


async def test_health_includes_chat_section(proxy: SimpleNamespace) -> None:
    async with _client(proxy) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["chat"]["state"] == "ready"
    assert body["chat"]["model"] == "stub/model"


async def test_status_includes_chat_section(proxy: SimpleNamespace) -> None:
    async with _client(proxy) as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["chat"]["state"] == "ready"


async def test_send_mapped_through(proxy: SimpleNamespace) -> None:
    async with _client(proxy) as client:
        resp = await client.post("/api/chat/send", json={"text": "hi"})
        assert resp.status_code == 202
        assert resp.json() == {"started": True}
        # The stub holds busy briefly → the 409 maps through untouched.
        busy = await client.post("/api/chat/send", json={"text": "again"})
        assert busy.status_code == 409
        assert busy.json() == {"error": "already processing a turn"}
        # 400 mapping (empty text) — the hub rewrites nothing.
        bad = await client.post("/api/chat/send", json={"text": ""})
        assert bad.status_code == 400


async def test_history_mapped_through(proxy: SimpleNamespace) -> None:
    async with _client(proxy) as client:
        resp = await client.get("/api/chat/history")
    assert resp.status_code == 200
    assert resp.json() == {"messages": [{"role": "user", "text": "stub history line"}]}


async def test_stop_and_new_mapped_through(proxy: SimpleNamespace) -> None:
    async with _client(proxy) as client:
        stop = await client.post("/api/chat/stop")
        assert stop.status_code == 202
        assert stop.json() == {"stopping": True}
        new = await client.post("/api/chat/new")
        assert new.status_code == 202
        assert new.json() == {"reset": True}


# --- SSE passthrough (real socket) ---------------------------------------------


async def _stub_connections(proxy: SimpleNamespace) -> int:
    async with httpx.AsyncClient() as direct:
        resp = await direct.get(f"{proxy.sidecar.base_url()}/health")
        return int(resp.json()["connections"])


async def test_sse_passthrough_and_disconnect_closes_upstream(proxy: SimpleNamespace) -> None:
    assert await _stub_connections(proxy) == 0
    client = httpx.AsyncClient()
    stream_cm = client.stream("GET", f"{proxy.sse_url}/api/chat/events")
    resp = await stream_cm.__aenter__()
    try:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache"
        first: dict | None = None
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                first = json.loads(line[len("data: ") :])
                break
        assert first == {"type": "text_delta", "delta": "hello from stub"}
        assert await _stub_connections(proxy) == 1  # upstream open while we read
    finally:
        # Client disconnect: the hub must close the upstream (trap T9).
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0
    while loop.time() < deadline:
        if await _stub_connections(proxy) == 0:
            break
        await asyncio.sleep(0.1)
    else:
        raise TimeoutError("stub SSE connection did not close after client disconnect")
    assert await _stub_connections(proxy) == 0


# --- chat lifecycle events on the hub stream -------------------------------------


async def test_chat_events_flow_through_hub_events(proxy: SimpleNamespace) -> None:
    """``/api/events`` carries the hello snapshot with chat state and a
    ``{"type": "chat"}`` transition when the sidecar dies."""
    hello: dict | None = None
    chat_events: list[dict] = []
    got_chat = asyncio.Event()

    async def read_stream() -> None:
        # Cancellation is the normal exit here (M3.2 _read_events pattern):
        # an SSE response never ends, so a clean stream exit would block in
        # read-to-end until the read timeout.
        nonlocal hello
        async with (
            httpx.AsyncClient() as client,
            client.stream("GET", f"{proxy.sse_url}/api/events") as resp,
        ):
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue  # heartbeat comment or blank
                event = json.loads(line[len("data: ") :])
                if event.get("type") == "hello":
                    assert "chat" in event
                    hello = event
                    pid = proxy.sidecar.sidecar_pid
                    assert pid is not None
                    os.kill(pid, signal.SIGKILL)  # trigger a chat transition
                elif event.get("type") == "chat":
                    chat_events.append(event)
                    if event["state"] in ("restarting", "ready"):
                        got_chat.set()
                        # Park FOREVER: the task must be cancelled while blocked
                        # in a read (a clean return would run the stream
                        # teardown's read-to-end and die on the read timeout).
                        await asyncio.Event().wait()

    reader = asyncio.create_task(read_stream(), name="chat-events-reader")
    try:
        await asyncio.wait_for(got_chat.wait(), timeout=SETTLE_S)
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await proxy.sidecar.stop()  # the restart task may still be spawning
    assert hello is not None
    assert chat_events, "expected a chat transition event after the kill"
    assert chat_events[-1]["state"] in ("restarting", "ready")


# --- sidecar down (must run after every test that needs a live sidecar) ----------


async def test_sidecar_down_serves_503(proxy: SimpleNamespace) -> None:
    # Runs last: shuts the stub down for good.
    await proxy.sidecar.stop()
    async with _client(proxy) as client:
        health = await client.get("/api/health")
        assert health.json()["chat"]["state"] == "down"
        resp = await client.post("/api/chat/send", json={"text": "hi"})
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"] == "chat sidecar unavailable"
        assert body["reason"]
