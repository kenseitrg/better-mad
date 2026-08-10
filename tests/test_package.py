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


def test_main_delegates_to_serve_app(monkeypatch) -> None:
    """main() parses args and hands them to serve_app."""
    import better_mad.app.server as server_mod
    from better_mad import cli

    calls: dict[str, object] = {}

    def fake_serve(files: list[str], port: int, show: bool) -> None:
        calls.update(files=files, port=port, show=show)

    monkeypatch.setattr(server_mod, "serve_app", fake_serve)
    assert cli.main(["a.txt", "--port", "8050", "--no-browser"]) == 0
    assert calls == {"files": ["a.txt"], "port": 8050, "show": False}
