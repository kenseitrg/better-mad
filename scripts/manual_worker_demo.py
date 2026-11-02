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
import tempfile
import time
import webbrowser
from pathlib import Path

from bokeh.embed import server_session

from better_mad.core.workspace import create_workspace
from better_mad.worker.lifecycle import start_worker

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "sample_csv_nulls.csv"

PLOT = '''
import holoviews as hv
from holoviews.operation.datashader import rasterize
import datashader as ds
import better_mad.sdk as bm

df = bm.data("sample_csv_nulls")
pts = hv.Points(df, ["XCORD_MIDPT", "YCORD_MIDPT"], vdims=["TR_HILBSTATIC"])
rasterized = rasterize(pts, aggregator=ds.mean("TR_HILBSTATIC"), cnorm="eq_hist")
print(f"rows: {len(df)}")
bm.show(rasterized.opts(cmap="fire", colorbar=True, width=0, height=0, tools=["hover"]))
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep temp workspace on exit")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="bm-worker-demo-")
    ws = create_workspace(Path(tmp))
    ds = ws.add_file(str(FIXTURE))
    print(f"workspace: {tmp}")
    print(f"loaded {ds.name}: {ds.df.shape[0]} rows x {ds.df.shape[1]} cols")

    ws.path.joinpath("plot.py").write_text(PLOT)

    handle = start_worker(ws.path / ".data", stderr_log=Path(tmp) / "worker.log")
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
            model=None, url=f"{handle.base_url}/bm", session_id=handle.session_id
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
        print(f"\nviewer: {viewer}")
        print("open it in a browser — zoom in: the raster must RE-RASTERIZE (get sharper)")
        if not args.no_browser:
            webbrowser.open(viewer.as_uri())

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
        print("worker stopped")
        if not args.keep:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"kept: {tmp}")


if __name__ == "__main__":
    main()
