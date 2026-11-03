"""Process entry point: ``python -m better_mad.worker``.

Boots the render worker in this process and announces readiness on stdout as
``WORKER_READY {json}`` (ephemeral port + session id) for the spawning process
— tests today, the M3.2 hub's spawn/health-poll/restart loop.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from better_mad.worker.lifecycle import READY_PREFIX
from better_mad.worker.server import DEFAULT_SESSION_ID, WorkerHandle, boot_worker


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="better-mad render worker")
    parser.add_argument("--data-dir", required=True, type=Path, help="workspace .data directory")
    parser.add_argument("--port", type=int, default=0, help="port (0 = ephemeral)")
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID, help="shared session id")
    parser.add_argument(
        "--allow-websocket-origin",
        action="append",
        default=None,
        help="extra websocket origin allowed to embed the live document "
        "(host:port or URL); repeatable — the hub passes its own origin",
    )
    args = parser.parse_args(argv)

    def announce(handle: WorkerHandle) -> None:
        ready = {"port": handle.port, "session_id": handle.session_id, "pid": os.getpid()}
        print(READY_PREFIX + json.dumps(ready), flush=True)

    boot_worker(
        args.data_dir,
        port=args.port,
        session_id=args.session_id,
        extra_websocket_origins=args.allow_websocket_origin,
        ready=announce,
    )


if __name__ == "__main__":
    main()
