"""Manual smoke test for the M3.1 render worker (not a pytest).

Creates a demo workspace from a test fixture, spawns a real worker subprocess,
runs a datashaded plot, and writes an HTML viewer that embeds the shared
session (server_session — same mechanism the SPA will use in M3.4).

Usage:
    uv run python scripts/manual_worker_demo.py [--keep]

Then open the printed viewer.html in a browser: pan/zoom should RE-RASTERIZE
(the raster gets sharper as you zoom in — that's the live DynamicMap, not a
static image). Ctrl+C stops the worker.
"""

from __future__ import annotations

import argparse
import json
import socket
import tempfile
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bokeh.core.types import ID
from bokeh.embed import server_session

from better_mad.core.workspace import create_workspace
from better_mad.worker.lifecycle import start_worker

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO / "data" / "14_01_post_stack_attr_after_scac_all"


def _plot_script(dataset: str) -> str:
    return f'''
import holoviews as hv
from holoviews.operation.datashader import rasterize
import datashader as ds
import better_mad.sdk as bm

df = bm.data("{dataset}")
pts = hv.Points(df, ["XCORD_MIDPT", "YCORD_MIDPT"], vdims=["TR_DOMFREQ"])
rasterized = rasterize(pts, aggregator=ds.mean("TR_DOMFREQ"), cnorm="eq_hist")
print(f"rows: {{len(df)}}")
bm.show(rasterized.opts(cmap="fire", colorbar=True, tools=["hover"]))
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA,
                        help="tabular file to load (default: the 121 MB post-stack sample)")
    parser.add_argument("--keep", action="store_true", help="keep temp workspace on exit")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="bm-worker-demo-")
    ws = create_workspace(Path(tmp))
    print(f"loading {args.data.name} (first parse ~13 s, cached after)…")
    ds = ws.add_file(str(args.data))
    print(f"workspace: {tmp}")
    print(f"loaded {ds.name}: {ds.df.shape[0]} rows x {ds.df.shape[1]} cols")

    ws.path.joinpath("plot.py").write_text(_plot_script(ds.name))

    # Static server for the viewer page: the embed must be served from a real
    # HTTP origin — file:// pages send `Origin: null`, which bokeh's websocket
    # allowlist always rejects. The worker gets this origin allowlisted.
    static_port = _free_port()
    static = ThreadingHTTPServer(
        ("127.0.0.1", static_port),
        partial(SimpleHTTPRequestHandler, directory=tmp),
    )
    threading.Thread(target=static.serve_forever, daemon=True).start()

    handle = start_worker(
        ws.path / ".data",
        allow_websocket_origin=[f"127.0.0.1:{static_port}"],
        stderr_log=Path(tmp) / "worker.log",
    )
    try:
        print(f"worker: {handle.base_url} (session {handle.session_id!r}, pid via /health)")
        print(f"health: {json.dumps(handle.health())}")

        t0 = time.perf_counter()
        result = handle.run(ws.path / "plot.py")
        print(f"run 1: {json.dumps(result, indent=None)} in {time.perf_counter() - t0:.2f}s")
        assert result["status"] == "ok", result

        doc = handle.doc_json()
        assert '"empty"' not in doc[:64], doc[:200]
        has_image = "Image" in doc
        print(f"doc.json: {len(doc)} chars | datashader Image glyph present: {has_image}")

        tag = server_session(
            model=None,
            url=f"{handle.base_url}/bm",
            session_id=ID(handle.session_id),
        )
        viewer = Path(tmp) / "viewer.html"
        viewer.write_text(
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>bm worker demo</title><style>"
            "body{margin:0;background:#1a1a1a;height:100vh}"
            "div{height:100vh !important}"
            "</style></head><body><div>"
            + tag
            + "</div></body></html>"
        )
        viewer_url = f"http://127.0.0.1:{static_port}/viewer.html"
        print(f"\nviewer: {viewer_url}")
        print("open it in a browser — zoom in: the raster must RE-RASTERIZE (get sharper)")
        if not args.no_browser:
            webbrowser.open(viewer_url)

        print("\nalso try, in another terminal:")
        print(f"  curl -s {handle.base_url}/health")
        print(f"  curl -s -X POST {handle.base_url}/run "
              f"-H 'content-type: application/json' -d '{{\"script\": \"{ws.path}/plot.py\"}}'")
        print("\nCtrl+C to stop")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        handle.stop()
        static.shutdown()
        print("worker stopped")
        if not args.keep:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"kept: {tmp}")


if __name__ == "__main__":
    main()
