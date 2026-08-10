"""Command-line entry point for better-mad."""

from __future__ import annotations

import argparse

from better_mad import __version__

BANNER = f"""\
better-mad {__version__} — seismic attribute visualization
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="better-mad",
        description="Visualize seismic attributes from tabular text files.",
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
        print(f"files to load: {', '.join(args.files)}")
    # M2: start the Panel server on localhost:{args.port} here.
    print("web server not implemented yet (milestone M2)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
