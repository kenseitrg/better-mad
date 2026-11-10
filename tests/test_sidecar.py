"""SidecarManager tests (handoff M3.3 §11): spawn/handshake/restart/down
against a stub sidecar process (tests/fixtures/stub_sidecar.py — a plain
Python http.server, so CI needs no Node and no ~/.pi).

The node-gate unit tests monkeypatch version detection instead of requiring
an ancient Node to exist. Blocking manager internals run in threads; tests
drive everything through the async public API (W1 analog).
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import httpx
import pytest

from better_mad.hub.sidecar import SidecarManager, node_gate_reason

FIXTURES = Path(__file__).parent / "fixtures"
STUB = FIXTURES / "stub_sidecar.py"

SETTLE_S = 20.0  # generous: process spawn + up to one retry


def stub_command() -> list[str]:
    return [sys.executable, str(STUB)]


def make_manager(tmp_path: Path, **kwargs: object) -> SidecarManager:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    options: dict = {
        "command": stub_command(),
        "health_interval_s": 0.2,
        "boot_timeout_s": 15.0,
    }
    options.update(kwargs)
    return SidecarManager(ws, **options)  # type: ignore[arg-type]


async def _wait_state(manager: SidecarManager, state: str, *, timeout_s: float = SETTLE_S) -> dict:
    """Poll ``chat_info()`` until the state matches; returns the final info."""
    async def poll() -> dict:
        while True:
            info = manager.chat_info()
            if info["state"] == state:
                return info
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout_s)


# --- boot + handshake ---------------------------------------------------------


async def test_spawn_reaches_ready(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    events: list[dict] = []
    unsubscribe = manager.subscribe(events.append)
    try:
        await manager.start()
        info = await _wait_state(manager, "ready")
        assert info["port"] and info["port"] > 0
        assert info["model"] == "stub/model"
        assert info["restarts"] == 0
        assert manager.base_url() == f"http://127.0.0.1:{info['port']}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{manager.base_url()}/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert [e["state"] for e in events][:2] == ["starting", "ready"]
        assert all(e["type"] == "chat" for e in events)
    finally:
        unsubscribe()
        await manager.stop()
    assert manager.chat_info()["state"] == "down"
    assert manager.sidecar_pid is None  # reaped


async def test_stderr_log_written(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    await manager.start()
    try:
        await _wait_state(manager, "ready")
        assert (tmp_path / "ws" / ".data" / "sidecar.log").exists()
    finally:
        await manager.stop()


# --- restart-once-then-down -----------------------------------------------------


async def test_kill9_restarts_once_then_down(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)  # restart_limit=1 (default), window 60 s
    states: list[str] = []
    unsubscribe = manager.subscribe(lambda e: states.append(e["state"]))
    try:
        await manager.start()
        await _wait_state(manager, "ready")
        pid = manager.sidecar_pid
        assert pid is not None
        os.kill(pid, signal.SIGKILL)
        # First death: restarting → ready (restarts == 1). The stale "ready"
        # from before the kill persists until the next health tick, so wait
        # through "restarting" explicitly.
        await _wait_state(manager, "restarting")
        info = await _wait_state(manager, "ready")
        assert info["restarts"] == 1
        assert "restarting" in states
        # Second death within the window: straight to down (the crash guard
        # fires before any restarting state) + reason; supervision stops.
        pid2 = manager.sidecar_pid
        assert pid2 is not None and pid2 != pid
        os.kill(pid2, signal.SIGKILL)
        info = await _wait_state(manager, "down")
        assert info.get("reason")
        assert manager.base_url() is None
    finally:
        unsubscribe()
        await manager.stop()


async def test_boot_failure_captures_stderr_reason(tmp_path: Path) -> None:
    # A process that writes a distinctive line to stderr and exits 1 — the
    # manager must retry once (restarts == 1) then go down with that reason.
    failing = tmp_path / "failing_sidecar.py"
    failing.write_text(
        "import sys\nprint('boom: provider auth failed', file=sys.stderr)\nsys.exit(1)\n"
    )
    manager = make_manager(tmp_path, command=[sys.executable, str(failing)])
    try:
        await manager.start()
        info = await _wait_state(manager, "down")
        assert info["restarts"] == 1  # exactly one boot retry (§7.3)
        assert "boom: provider auth failed" in info["reason"]
    finally:
        await manager.stop()


async def test_command_not_found_downs_with_reason(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, command=["definitely-not-a-real-binary-xyz"])
    try:
        await manager.start()
        info = await _wait_state(manager, "down")
        assert info.get("reason")
    finally:
        await manager.stop()


async def test_missing_entry_gets_npm_install_hint(tmp_path: Path) -> None:
    # An entry inside a package dir that has a package.json but no
    # node_modules → the down-reason points at the fix (trap T12).
    pkg = tmp_path / "sidefake"
    (pkg / "src").mkdir(parents=True)
    (pkg / "package.json").write_text("{}")
    manager = make_manager(tmp_path, command=[sys.executable, pkg / "src" / "missing.ts"])
    try:
        await manager.start()
        info = await _wait_state(manager, "down")
        assert info.get("reason")
        assert "npm install" in info["reason"]
    finally:
        await manager.stop()


# --- node gate (R1) ---------------------------------------------------------


def test_node_gate_missing_node(monkeypatch: pytest.MonkeyPatch) -> None:
    import better_mad.hub.sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod.shutil, "which", lambda name: None)
    assert sidecar_mod.node_version() is None
    reason = node_gate_reason()
    assert reason is not None and "node not found" in reason


def test_node_gate_version_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    import better_mad.hub.sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "node_version", lambda: (20, 11, 0))
    reason = node_gate_reason()
    assert reason is not None and "22.18" in reason
    monkeypatch.setattr(sidecar_mod, "node_version", lambda: (22, 18, 0))
    assert node_gate_reason() is None  # at the floor: fine


async def test_default_command_gated_down_without_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import better_mad.hub.sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod.shutil, "which", lambda name: None)
    manager = SidecarManager(tmp_path / "ws")  # default command → gated
    try:
        await manager.start()
        info = await _wait_state(manager, "down")
        assert "node not found" in info["reason"]
        assert manager.base_url() is None
        assert manager.sidecar_pid is None  # never spawned on a failed gate
    finally:
        await manager.stop()


async def test_stop_is_idempotent(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    await manager.stop()  # never started: no raise
    await manager.start()
    await _wait_state(manager, "ready")
    await manager.stop()
    await manager.stop()  # double stop: no raise
    assert manager.chat_info()["state"] == "down"
