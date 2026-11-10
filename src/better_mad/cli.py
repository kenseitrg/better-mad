"""Command-line entry point for better-mad (handoff M3.2 §7).

``better-mad [files...]`` starts the hub: create/load workspace → load CLI
files (errors print and continue — a bad file never crashes the hub) →
:class:`WorkerManager` + :class:`SidecarManager` + FastAPI app → uvicorn
(blocks) → browser opens once ``/api/health`` is green. The hub owns the
worker spawn, so the port must be known before boot: the CLI port (or an
allocated free one for ``--port 0``) becomes both the uvicorn bind port and
the worker's websocket origin. A chat boot failure never blocks the hub —
the SidecarManager degrades to ``down`` and ``/api/chat/*`` serves 503
(design §9.1).

The superseded Panel app layer (``better_mad.app``) is no longer imported
(handoff W10); it stays in the tree until the cleanup step removes it.
"""

from __future__ import annotations

import argparse
import datetime
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

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
    parser.add_argument("--port", type=int, default=5006, help="port for the hub web server")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--workspace",
        help="workspace directory (default: timestamped under ~/.cache/better-mad/workspaces)",
    )
    parser.add_argument(
        "--model",
        type=_model_spec,
        default=None,
        help='chat model override as "provider/model" (default: the pi config default)',
    )
    parser.add_argument("--version", action="version", version=f"better-mad {__version__}")
    return parser


def _model_spec(value: str) -> str:
    """Validate the --model SHAPE only ("provider/model"); real resolution is
    Node-side, against the user's ~/.pi/agent catalogs (handoff §8)."""
    provider, sep, model = value.partition("/")
    if not sep or not provider or not model:
        raise argparse.ArgumentTypeError('--model must look like "provider/model"')
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(BANNER)

    from better_mad.core.workspace import create_workspace
    from better_mad.hub.app import create_app
    from better_mad.hub.sidecar import SidecarManager
    from better_mad.worker.manager import WorkerManager

    ws = create_workspace(Path(args.workspace) if args.workspace else _default_workspace_dir())
    print(f"workspace: {ws.path}")
    for f in args.files:
        try:
            ds = ws.add_file(f)
            print(f"loaded {ds.name}: {ds.df.shape[0]:,} rows x {ds.df.shape[1]} cols")
        except Exception as exc:  # degrade: one bad file must not kill the hub
            print(f"error loading {f}: {exc} — continuing")

    port = args.port if args.port != 0 else allocate_free_port()
    # The worker must allowlist the hub origin BEFORE it spawns (trap W3: a
    # non-empty allowlist replaces bokeh's default self-origin entries).
    manager = WorkerManager(
        ws.data_dir,
        f"127.0.0.1:{port}",
        script_path=ws.script_path,
    )
    sidecar = SidecarManager(ws.path, model=args.model)
    app = create_app(ws, manager, sidecar=sidecar)

    if not args.no_browser:
        threading.Thread(target=_open_browser_when_ready, args=(port,), daemon=True).start()

    import uvicorn

    print(f"hub: http://127.0.0.1:{port}/")
    # SSE streams never end on their own — without a graceful-shutdown timeout
    # an open /api/events client (a SPA tab) blocks Ctrl+C forever (found in
    # M3.2 manual QA cleanup). After the timeout uvicorn force-closes sockets;
    # the lifespan still stops the worker cleanly.
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        timeout_graceful_shutdown=5,
    )  # blocks
    return 0


def allocate_free_port() -> int:
    """Take a free TCP port from the kernel and release it for uvicorn.

    The bind happens later, so a racer could steal the port in between —
    accepted for a localhost dev tool (documented in :mod:`WorkerManager`).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _open_browser_when_ready(port: int, timeout_s: float = 60.0) -> None:
    """Poll ``/api/health`` from a side thread; open the browser when green."""
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:  # hub not up yet
            time.sleep(0.5)
    print(f"hub not healthy within {timeout_s:g}s — open {url} manually")


def _default_workspace_dir() -> Path:
    root = Path.home() / ".cache" / "better-mad" / "workspaces"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / stamp


if __name__ == "__main__":
    raise SystemExit(main())
