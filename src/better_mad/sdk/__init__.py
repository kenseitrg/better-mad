"""Tiny SDK for agent-generated plot scripts (design.md §5).

Scripts produced by the agent (or the user) import this module:

    import holoviews as hv

    import better_mad.sdk as bm

    df = bm.data("file_a")
    bm.show(hv.Points(df, kdims=["XCORD_MIDPT", "YCORD_MIDPT"]))

The runner (``better_mad.core.runner``) provides two environment variables:

- ``BETTER_MAD_DATA_DIR``: directory of per-dataset parquet snapshots,
- ``BETTER_MAD_OUTPUT``: path where ``show()`` pickles the final figure.

This module must stay dependency-light and never import Panel; it runs inside
the script subprocess, not in the app.
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path

import pandas as pd

ENV_DATA_DIR = "BETTER_MAD_DATA_DIR"
ENV_OUTPUT = "BETTER_MAD_OUTPUT"


def _ensure_backend() -> None:
    """Load the bokeh plotting extension so ``.opts()`` works in any script.

    A bare ``import holoviews`` may claim a ``current_backend`` while no plotting
    module is actually loaded (``loaded_backends()`` empty), in which case applying
    options raises ``ValueError``. Loading bokeh here means scripts (and small
    models) never have to remember it.
    """
    try:
        import holoviews as hv
    except ImportError:
        return
    if "bokeh" not in hv.Store.loaded_backends():
        hv.extension("bokeh")


_ensure_backend()


def _data_dir() -> Path:
    raw = os.environ.get(ENV_DATA_DIR)
    if not raw:
        raise RuntimeError(
            f"{ENV_DATA_DIR} is not set — bm.data() only works inside a plot script "
            "run by better-mad."
        )
    return Path(raw)


def _output_path() -> Path:
    raw = os.environ.get(ENV_OUTPUT)
    if not raw:
        raise RuntimeError(
            f"{ENV_OUTPUT} is not set — bm.show() only works inside a plot script "
            "run by better-mad."
        )
    return Path(raw)


def list_data() -> list[str]:
    """Names of the datasets available to ``data()``, sorted."""
    return sorted(p.stem for p in _data_dir().glob("*.parquet"))


def data(name: str) -> pd.DataFrame:
    """Load a dataset by name (see ``datasets.md`` for names and columns).

    Returns a DataFrame with sanitized column names. Raises ``KeyError`` with
    the list of available datasets when the name is unknown.
    """
    path = _data_dir() / f"{name}.parquet"
    if not path.exists():
        raise KeyError(f"unknown dataset {name!r}; available: {list_data()}")
    return pd.read_parquet(path)


def _materialize(obj: object) -> object:
    """Replace DynamicMaps with their current frame so the figure pickles.

    ``rasterize()``/``datashade()`` return DynamicMaps holding unpicklable local
    closures (``Can't pickle local object 'Dynamic._dynamic_operation...'``).
    Materializing yields a static frame — pan/zoom still work, but the raster is
    not re-aggregated on zoom (accepted for v2; in-app re-rasterizing is future
    work). Recurses through containers so nested DynamicMaps are covered.
    """
    try:
        import holoviews as hv
        from holoviews.core.spaces import DynamicMap
    except ImportError:
        return obj
    if isinstance(obj, DynamicMap):
        return obj[()]
    if isinstance(obj, (hv.Layout, hv.NdLayout, hv.GridSpace, hv.NdOverlay)):
        return obj.clone({k: _materialize(v) for k, v in obj.items()})
    if isinstance(obj, hv.Overlay):
        return hv.Overlay([_materialize(el) for el in obj])
    return obj


def _capture_options(obj: object) -> list[dict[str, dict[str, object]] | None]:
    """Explicit per-element options, aligned with ``traverse()`` order.

    HoloViews stores ``.opts()`` in a global registry keyed by object id
    (``Store.custom_options``), so applied options **do not survive pickle**.
    They are captured here and re-applied by the runner after transport.
    """
    try:
        import holoviews as hv
    except ImportError:
        return []
    backend = hv.Store.current_backend or "bokeh"
    captured: list[dict[str, dict[str, object]] | None] = []

    def visit(item: object) -> None:
        entry: dict[str, dict[str, object]] = {}
        for group in ("plot", "style", "norm"):
            try:
                opts = hv.Store.lookup_options(backend, item, group, defaults=False)
            except Exception:
                opts = None
            if opts is not None and opts.kwargs:
                entry[group] = dict(opts.kwargs)
        captured.append(entry or None)

    obj.traverse(visit)  # type: ignore
    return captured


def show(obj: object) -> None:
    """Register a figure as the preview output. Last call wins.

    Accepts anything picklable; intended for HoloViews ``Element``, ``Overlay``,
    ``Layout`` and ``NdLayout`` objects. DynamicMaps (e.g. ``rasterize()`` output)
    are materialized to their current frame first, and explicitly applied options
    are captured alongside the figure (they don't survive pickle on their own).
    The write is atomic, so a watching app never reads a half-written figure.
    """
    figure = _materialize(obj)
    payload = {"figure": figure, "options": _capture_options(figure)}
    out = _output_path()
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(payload, fh)
    os.replace(tmp, out)
