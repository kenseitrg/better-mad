"""Smoke tests for the better-mad package (M0)."""

from better_mad import __version__
from better_mad.cli import build_parser, main


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


def test_main_runs(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "better-mad" in out
