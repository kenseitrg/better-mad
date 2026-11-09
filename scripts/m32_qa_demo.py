"""Manual QA launcher for M3.2 — the hub backend (not a pytest).

Boots the real hub (same wiring as the ``better-mad`` CLI) against the real
sample data, plus one extra route the CLI doesn't have:

    GET /demo  — a viewer page embedding the worker's live shared session via
                 ``bokeh.embed.server_session`` (exactly what the M3.4 SPA will
                 do with ``/api/plot/embed``). The tag is regenerated per
                 request, so RELOAD the page after a worker restart to pick up
                 the new port/epoch.

Everything else is pure M3.2 surface: REST under ``/api``, SSE on
``/api/events``, auto-run watcher on ``plot.py``, worker supervision.

Usage:
    uv run python scripts/m32_qa_demo.py [--port 5006] [--workspace DIR]
                                         [--data FILE] [--no-browser]

QA cheat sheet (hub = http://127.0.0.1:<port>):
    curl -s hub/api/health | jq        # worker state/epoch/restarts
    curl -s hub/api/files | jq         # datasets + column stats
    curl -N hub/api/events             # SSE: hello / plot_changed / run_result / worker
    curl -s hub/api/plot/embed | jq    # worker URL + session + epoch
    curl -s -X POST hub/api/run        # manual run
    curl -s -X POST hub/api/autorun -H 'content-type: application/json' -d '{"on": false}'
    curl -s -X PUT hub/api/plot.py -H 'content-type: application/json' \
         --data-binary '{"content": "<new script>"}'
    kill -9 $(pgrep -f 'better_mad[.]worker')   # worker crash → restart + last-good replay
    # import a second file with parser settings:
    curl -s -X POST hub/api/files/import -F file=@tests/fixtures/sample_csv_nulls.csv \
         -F 'settings={"sentinels": ["-9999"]}' | jq
"""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO / "data" / "14_01_post_stack_attr_after_scac_all"
DEFAULT_WORKSPACE = Path.home() / ".cache" / "better-mad" / "workspaces" / "qa-m32"

DEMO_SCRIPT = '''\
"""Demo plot: datashaded mean-attribute map (M3.2 QA).

Edit me (or PUT /api/plot.py) — the watcher auto-runs me ~0.8 s after I stop
changing. Break me on purpose to see error results + last-good retention.
"""
import holoviews as hv
from holoviews.operation.datashader import rasterize
import datashader as ds

import better_mad.sdk as bm

df = bm.data("{dataset}")
print(f"rows: {{len(df)}}")
pts = hv.Points(df, ["XCORD_MIDPT", "YCORD_MIDPT"], vdims=["TR_DOMFREQ"])
rasterized = rasterize(pts, aggregator=ds.mean("TR_DOMFREQ"), cnorm="eq_hist")
bm.show(rasterized.opts(cmap="fire", colorbar=True, tools=["hover"]))
'''

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>better-mad M3.2 QA</title><style>
html,body{{margin:0;height:100%;background:#1a1a1a;color:#ddd;font-family:monospace}}
#plot{{height:100vh !important}}
/* R2b: autoload.js replaces the script tag with a div.bk-root wrapper — it is
   height:auto, and stretch_both measures the PARENT, so it collapses to a
   toolbar-height strip unless every div in the chain is forced to full height
   (the M3.1-verified pattern; the SPA must do the same in M3.4). */
#plot div{{height:100vh !important}}
#badge{{position:fixed;top:8px;right:12px;z-index:99;background:#000a;padding:6px 10px;
border:1px solid #444;border-radius:6px;font-size:12px;line-height:1.6}}
a{{color:#7ab0ff}}
</style></head><body><div id="plot">{embed}</div>
<div id="badge">epoch {epoch} · session {sid}<br>
<a href="/api/status" target="_blank">/api/status</a> ·
<a href="/api/health" target="_blank">/api/health</a><br>
reload me after a worker restart</div></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="M3.2 hub QA demo")
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    from better_mad.core.workspace import STARTER_SCRIPT, create_workspace
    from better_mad.hub.app import create_app
    from better_mad.worker.manager import WorkerManager

    print(f"workspace: {args.workspace}")
    ws = create_workspace(args.workspace)
    if not any(ds.path.name == str(args.data) for ds in ws.datasets.values()):
        print(f"loading {args.data.name} (cold ~13 s, cached ~0.1 s)…")
        ds = ws.add_file(str(args.data))
        print(f"loaded {ds.name}: {ds.df.shape[0]:,} rows x {ds.df.shape[1]} cols")
    dataset_names = [name for name, ds in ws.datasets.items()
                     if ds.path.name == str(args.data)]
    dataset = dataset_names[0] if dataset_names else next(iter(ws.datasets))

    if ws.script_path.read_text() == STARTER_SCRIPT:
        ws.script_path.write_text(DEMO_SCRIPT.format(dataset=dataset))
        print(f"seeded demo plot.py (dataset {dataset!r})")

    manager = WorkerManager(
        ws.data_dir, f"127.0.0.1:{args.port}", script_path=ws.script_path
    )
    app = create_app(ws, manager)

    # --- the one demo-only addition: a live-session viewer at /demo ----------

    from bokeh.core.types import ID
    from bokeh.embed import server_session
    from fastapi.responses import HTMLResponse

    @app.get("/demo", include_in_schema=False)
    async def demo() -> HTMLResponse:
        info = manager.embed_info()
        if info["server_url"] is None:
            return HTMLResponse(
                "<body style='background:#1a1a1a;color:#ddd;font-family:monospace'>"
                "worker not up (state in /api/health) — reload me.</body>",
                status_code=503,
            )
        tag = server_session(
            model=None, url=info["server_url"], session_id=ID(info["session_id"])
        )
        return HTMLResponse(
            _PAGE.format(embed=tag, epoch=info["epoch"], sid=info["session_id"])
        )

    if not args.no_browser:
        threading.Thread(
            target=_open_browser_when_ready, args=(args.port,), daemon=True
        ).start()

    import uvicorn

    print(f"\nhub:    http://127.0.0.1:{args.port}/")
    print(f"viewer: http://127.0.0.1:{args.port}/demo")
    print(f"script: {ws.script_path}  (edits auto-run)")
    print(f"events: curl -N http://127.0.0.1:{args.port}/api/events")
    print("\nCtrl+C to stop")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")  # blocks
    return 0


def _open_browser_when_ready(port: int, timeout_s: float = 90.0) -> None:
    import json
    import urllib.request

    url = f"http://127.0.0.1:{port}/demo"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                if r.status == 200 and json.loads(r.read())["worker"]["state"] == "ready":
                    webbrowser.open(url)
                    return
        except Exception:  # hub/worker not up yet
            time.sleep(0.5)
    print(f"hub or worker not ready within {timeout_s:g}s — open {url} manually")


if __name__ == "__main__":
    raise SystemExit(main())
