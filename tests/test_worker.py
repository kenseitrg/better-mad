"""Tests for the M3.1 render worker (handoff §7).

Scenarios: good run replaces roots; DynamicMaps stay dynamic (the attach
transport keeps range callbacks alive); error runs keep last-good roots;
a script without ``bm.show()`` errors without touching the document; a
watchdog timeout kills the worker process; the spawned-subprocess path works
end to end.

Most scenarios drive an **in-process** worker (fast: bokeh/holoviews import
once per pytest run; the handle exposes ``.state`` so tests can assert on the
kept plot). The timeout scenario needs a real subprocess (the watchdog
``os._exit``s its own process), and one subprocess test covers the full spawn
path (``-m better_mad.worker``, ready line, HTTP boundary). Every network wait
has an explicit timeout (trap T1).

Scenario 6 (busy 409) is NOT tested: runs execute synchronously on the IOLoop
thread (trap T8), so concurrent ``/run`` requests cannot be accepted while one
is executing; the 409 guard covers only the sub-millisecond window between exec
completion and the awaited document-lock swap, which is not deterministically
reachable over HTTP.
"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from bokeh.events import RangesUpdate

from better_mad.core.workspace import Workspace, create_workspace
from better_mad.worker import WorkerHandle, boot_worker, start_worker
from better_mad.worker.executor import HANG_EXIT_CODE

FIXTURES = Path(__file__).parent / "fixtures"
BOOT_TIMEOUT_S = 60.0  # in-process boot: thread startup + Server bind only

POINTS_SCRIPT = """
import holoviews as hv
import better_mad.sdk as bm
df = bm.data('sample_ws')
pts = hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT'], vdims=['TR_DOMFREQ'])
bm.show(pts.opts(color='TR_DOMFREQ', cmap='viridis', size=2))
"""

CURVE_SCRIPT = """
import holoviews as hv
import better_mad.sdk as bm
df = bm.data('sample_ws')
bm.show(hv.Curve(df, kdims=['XCORD_MIDPT'], vdims=['TR_DOMFREQ']))
"""

RASTERIZE_SCRIPT = """
import datashader as ds
import holoviews as hv
from holoviews.operation.datashader import rasterize
import better_mad.sdk as bm
df = bm.data('sample_ws')
pts = hv.Points(df, kdims=['XCORD_MIDPT', 'YCORD_MIDPT'], vdims=['TR_DOMFREQ'])
bm.show(rasterize(pts, aggregator=ds.mean('TR_DOMFREQ')).opts(cmap='fire'))
"""


def _workspace(tmp_path: Path) -> Workspace:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    return ws


def _boot_inproc(ws: Workspace) -> WorkerHandle:
    """boot_worker in a background thread; returns once the loop is serving."""
    box: dict = {}
    ready = threading.Event()

    def announce(handle: WorkerHandle) -> None:
        box["handle"] = handle
        ready.set()  # fires just before the IOLoop starts — boot_worker itself blocks

    def run() -> None:
        try:
            boot_worker(ws.data_dir, ready=announce)
        except Exception as exc:  # surface boot failures instead of hanging the test
            box["error"] = exc
        finally:
            ready.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(BOOT_TIMEOUT_S), "in-process worker boot timed out"
    if "error" in box:
        raise box["error"]
    return box["handle"]


@pytest.fixture()
def inproc(tmp_path: Path) -> Iterator[tuple[WorkerHandle, Workspace]]:
    """In-process worker on an ephemeral port; yields (handle, workspace)."""
    ws = _workspace(tmp_path)
    handle = _boot_inproc(ws)
    yield handle, ws
    handle.stop()


@pytest.fixture()
def spawned(tmp_path: Path) -> Iterator[tuple[WorkerHandle, Workspace]]:
    """Worker subprocess (``python -m better_mad.worker``); yields (handle, ws)."""
    ws = _workspace(tmp_path)
    handle = start_worker(ws.data_dir, stderr_log=tmp_path / "worker.stderr")
    yield handle, ws
    handle.stop()


def _doc_signature(payload: str) -> str:
    """Canonical snapshot signature for comparison.

    ``doc.to_json()`` regenerates model/buffer ids on every call (even with
    unchanged content — ndarray buffers are re-referenced per serialization),
    so byte equality is never a valid "document unchanged" test: drop all
    ``id`` keys and compare structure.
    """

    def strip(node: object) -> object:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "id"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(json.loads(payload)), sort_keys=True)


# --- scenario 1: good runs replace the document roots -----------------------

def test_good_run_populates_and_replaces_roots(inproc) -> None:
    handle, ws = inproc

    result = _post_run(handle, {"script": _write(ws, POINTS_SCRIPT)})
    assert result["status"] == "ok", result
    assert result["run_seconds"] > 0

    doc_a = handle.doc_json()
    sig_a = _doc_signature(doc_a)
    assert '"empty":true' not in doc_a.replace(" ", "")  # not the empty placeholder
    assert "Scatter" in sig_a  # hv.Points renders as a bokeh Scatter glyph

    _write(ws, CURVE_SCRIPT)
    result2 = _post_run(handle, {"script": str(ws.script_path)})
    assert result2["status"] == "ok", result2
    sig_b = _doc_signature(handle.doc_json())
    assert sig_b != sig_a  # roots replaced
    assert "Line" in sig_b and "Scatter" not in sig_b  # Curve, not a stale second root


def test_attached_figure_fits_container(inproc) -> None:
    """The worker forces stretch_both sizing and clears zero width/height —
    the plot must fill the embedding container, not render as a thin
    fixed-width strip (v1 fit_container_hook equivalent; QC 2026-11-03)."""
    handle, ws = inproc
    script = POINTS_SCRIPT.replace(
        "pts.opts(color='TR_DOMFREQ', cmap='viridis', size=2)",
        "pts.opts(color='TR_DOMFREQ', cmap='viridis', size=2, width=0, height=0)",
    )
    result = _post_run(handle, {"script": _write(ws, script)})
    assert result["status"] == "ok", result

    root = handle.state.session.document.roots[0]
    assert root.sizing_mode == "stretch_both"
    assert root.width != 0 and root.height != 0


# --- scenario 2: DynamicMaps stay dynamic -----------------------------------

def test_dynamic_map_stays_dynamic(inproc) -> None:
    handle, ws = inproc
    result = _post_run(handle, {"script": _write(ws, RASTERIZE_SCRIPT)})
    assert result["status"] == "ok", result

    sig = _doc_signature(handle.doc_json())
    assert "Image" in sig  # datashaded raster is in the document
    assert handle.state is not None and handle.state.last_plot is not None
    # kept plot reference: its range callbacks re-rasterize on zoom (trap T6)
    assert len(handle.state.last_plot.callbacks) > 0
    assert handle.state.ever_shown


def test_zoom_event_re_rasterizes(inproc) -> None:
    """A ``rangesupdate`` event (what WheelZoom/Pan tools send) re-aggregates.

    Regression for the hv/panel lock trap (QC 2026-11-03): without
    ``_patch_hv_callback_locking``, hv's deferred ``process_on_event`` is
    scheduled nolock, the first model mutation raises ``_pending_writes
    should be non-None``, and the raster never updates. The event is fired
    server-side with x AND y values — bokeh's python serializer drops
    ``RangesUpdate`` values, so the wire round-trip can't be tested here.
    """
    handle, ws = inproc
    result = _post_run(handle, {"script": _write(ws, RASTERIZE_SCRIPT)})
    assert result["status"] == "ok", result

    doc = handle.state.session.document
    fig = doc.roots[0]
    rend = [
        r for r in fig.renderers
        if type(r.glyph).__name__ == "Image" and "image" in r.data_source.data
    ]
    src = rend[0].data_source

    def raster_hash() -> str:
        return hashlib.sha256(src.data["image"][0].tobytes()).hexdigest()

    f0 = raster_hash()
    xr, yr = fig.x_range, fig.y_range
    event = RangesUpdate(
        fig,
        x0=xr.start + (xr.end - xr.start) * 0.4,
        x1=xr.end - (xr.end - xr.start) * 0.4,
        y0=yr.start + (yr.end - yr.start) * 0.4,
        y1=yr.end - (yr.end - yr.start) * 0.4,
    )
    # fire on the worker's IOLoop thread — same dispatch bokeh's websocket
    # patch handler uses (doc event callbacks -> hv on_event)
    handle.server.io_loop.add_callback(lambda: fig._trigger_event(event))

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if raster_hash() != f0:
            return  # re-aggregated + pushed under the session lock
    pytest.fail("raster did not re-render within 10 s of the rangesupdate event")


# --- scenario 3: error run keeps last good ----------------------------------

def test_error_run_keeps_last_good_roots(inproc) -> None:
    handle, ws = inproc
    _post_run(handle, {"script": _write(ws, POINTS_SCRIPT)})
    sig_good = _doc_signature(handle.doc_json())

    result = _post_run(handle, {"script": _write(ws, "raise ValueError('boom')\n")})
    assert result["status"] == "error"
    assert "ValueError: boom" in result["stderr_tail"]
    assert _doc_signature(handle.doc_json()) == sig_good  # last good intact

    health = handle.health()
    assert health["ok"] is True  # worker stays up after error runs (handoff §4.3)
    assert health["runs"] == 2
    assert health["last_status"] == "error"


# --- scenario 4: script without show() ---------------------------------------

def test_run_without_show_errors_and_document_stays_empty(inproc) -> None:
    handle, ws = inproc
    script = _write(ws, "import better_mad.sdk as bm\nprint(bm.list_data())\n")
    result = _post_run(handle, {"script": script})
    assert result["status"] == "error"
    assert result["stderr_tail"] == "script ran but never called bm.show()"
    assert "sample_ws" in result["stdout_tail"]  # the script did run
    assert json.loads(handle.doc_json()) == {"empty": True}


def test_missing_script_file_is_error_not_crash(inproc) -> None:
    handle, _ws = inproc
    result = _post_run(handle, {"script": "/does/not/exist/plot.py"})
    assert result["status"] == "error"
    assert "script not found" in result["stderr_tail"]
    assert json.loads(handle.doc_json()) == {"empty": True}


def test_invalid_run_request_is_400(inproc) -> None:
    handle, _ = inproc
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _post_raw(handle, b"not json at all")
    assert excinfo.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as excinfo2:
        _post_raw(handle, json.dumps({"script": "x.py", "timeout_s": 0}).encode())
    assert excinfo2.value.code == 400


# --- scenario 5: watchdog timeout kills the worker process -------------------

def test_timeout_kills_worker_process(spawned) -> None:
    handle, ws = spawned
    assert handle.health()["ok"] is True  # serving before the hang

    with pytest.raises((urllib.error.URLError, TimeoutError, ConnectionError)):
        _post_raw(
            handle,
            json.dumps({"script": str(_write(ws, "while True: pass\n")), "timeout_s": 2}).encode(),
            client_timeout_s=10,
        )
    assert handle.process is not None
    exit_code = handle.process.wait(timeout=5)
    assert exit_code == HANG_EXIT_CODE  # watchdog os._exit, not a crash


# --- spawned worker end-to-end (the path the M3.2 hub will use) --------------

def test_spawned_worker_end_to_end(spawned) -> None:
    handle, ws = spawned
    health = handle.health()
    assert health["ok"] is True
    assert health["session_id"] == handle.session_id
    assert health["last_status"] is None

    result = _post_run(handle, {"script": _write(ws, POINTS_SCRIPT)})
    assert result["status"] == "ok", result
    assert "Scatter" in _doc_signature(handle.doc_json())
    assert handle.doc_json() != '{"empty": true}'


def _write(ws: Workspace, source: str) -> Path:
    ws.script_path.write_text(source)
    return ws.script_path


def _post_run(handle: WorkerHandle, body: dict) -> dict:
    payload = {k: (str(v) if isinstance(v, Path) else v) for k, v in body.items()}
    raw = _post_raw(handle, json.dumps(payload).encode(), client_timeout_s=45)
    return json.loads(raw)


def _post_raw(handle: WorkerHandle, data: bytes, client_timeout_s: float = 45) -> bytes:
    req = urllib.request.Request(
        f"{handle.base_url}/run",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=client_timeout_s) as resp:
        return resp.read()
