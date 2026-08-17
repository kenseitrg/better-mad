"""Server assembly for the M2 minimal app: workspace + preview pane (design.md §3).

The full three-pane shell lands in M3; this serves the header + preview only.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import panel as pn

from better_mad.app.preview import make_view
from better_mad.core.workspace import Workspace, create_workspace


def serve_app(
    files: list[str],
    port: int = 5006,
    show: bool = True,
    workspace: str | Path | None = None,
) -> Workspace:
    """Create the workspace, load CLI files, serve the preview app.

    Returns the workspace (after the server exits — mainly useful in tests).
    """
    ws = create_workspace(Path(workspace) if workspace else _default_workspace_dir())
    for f in files:
        ds = ws.add_file(f)
        print(f"loaded {ds.name}: {ds.df.shape[0]:,} rows x {ds.df.shape[1]} cols")
    print(f"workspace: {ws.path}")
    pn.serve(
        lambda: make_view(ws),
        port=port,
        show=show,
        title="better-mad",
        threaded=True,
    )
    return ws


def _default_workspace_dir() -> Path:
    root = Path.home() / ".cache" / "better-mad" / "workspaces"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / stamp
