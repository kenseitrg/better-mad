"""Command-line entry point for better-mad (v2 stub — app server lands in M2/M3)."""

from __future__ import annotations

import argparse

from better_mad import __version__

BANNER = f"""\
better-mad {__version__} — agent-driven seismic attribute visualization
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="better-mad",
        description="Agent-driven visualization of seismic attributes.",
    )
    parser.add_argument("files", nargs="*", help="attribute files to load on startup")
    parser.add_argument("--port", type=int, default=5006, help="port for the web server")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--version", action="version", version=f"better-mad {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(BANNER)
    if args.files:
        print("Requested files:", ", ".join(args.files))
    print("Web server arrives in M2. For now, the data core is importable:")
    print("  from better_mad.core.dataset import load_dataset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
