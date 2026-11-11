"""Smoke tests for the better-mad package (M0)."""

from better_mad import __version__
from better_mad.cli import build_parser


def test_version_string() -> None:
    assert isinstance(__version__, str)
    assert __version__.count(".") == 2


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.files == []
    assert args.port == 5006
    assert args.no_browser is False


def test_parser_files_and_port() -> None:
    args = build_parser().parse_args(["a.txt", "b.txt", "--port", "8050"])
    assert args.files == ["a.txt", "b.txt"]
    assert args.port == 8050


def test_parser_dev_origin_repeatable() -> None:
    """M3.4 §7.2: --dev-origin is repeatable, default empty (prod unchanged)."""
    args = build_parser().parse_args(
        [
            "--dev-origin",
            "http://localhost:5173",
            "--dev-origin",
            "http://localhost:5174",
        ]
    )
    assert args.dev_origin == ["http://localhost:5173", "http://localhost:5174"]
    assert build_parser().parse_args([]).dev_origin == []


def test_main_boots_hub_without_serving(monkeypatch, tmp_path) -> None:
    """CLI rewiring (M3.2): main() builds the hub app (workspace + worker
    manager) and hands it to uvicorn; with ``uvicorn.run`` stubbed out, no
    socket binds and no worker spawns — pure plumbing smoke test."""
    from typing import cast

    import uvicorn
    from fastapi import FastAPI

    import better_mad.cli as cli

    calls: dict[str, object] = {}

    def fake_run(
        app: object,
        *,
        host: str,
        port: int,
        log_level: str,
        timeout_graceful_shutdown: int | None = None,
    ) -> None:
        calls.update(
            app=app,
            host=host,
            port=port,
            log_level=log_level,
            tgs=timeout_graceful_shutdown,
        )

    monkeypatch.setattr(uvicorn, "run", fake_run)
    ws_dir = tmp_path / "ws"
    code = cli.main(
        [
            "a.txt",
            "--port",
            "8050",
            "--no-browser",
            "--workspace",
            str(ws_dir),
            "--dev-origin",
            "http://localhost:5173",  # M3.4 §7.2: forwarded to the manager
        ]
    )
    assert code == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8050
    # SSE clients must not be able to stall shutdown forever (M3.2 QA finding)
    assert calls["tgs"] == 5
    app = cast(FastAPI, calls["app"])
    hub = app.state.hub
    assert hub.ws.path == ws_dir
    assert hub.auto_run is True
    assert hub.manager.epoch == 1  # lifespan never ran: worker not spawned
    assert hub.manager._extra_ws_origins == ("http://localhost:5173",)
