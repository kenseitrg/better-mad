"""Tiny SDK for agent-generated plot scripts (design.md §5).

Scripts produced by the agent (or the user) import this module:

    import holoviews as hv

    import better_mad.sdk as bm

    df = bm.data("file_a")
    bm.show(hv.Points(df, kdims=["XCORD_MIDPT", "YCORD_MIDPT"]))

The runner (``better_mad.core.runner``) provides two environment variables:

- ``BETTER_MAD_DATA_DIR``: directory of per-dataset parquet snapshots,
- ``BETTER_MAD_OUTPUT``: path where ``show()`` pickles the final figure.

``show()`` has two transports (design.md §5):

- **Attach mode** (live render worker, M3.1+): the worker installs a callable
  in ``_ATTACH`` before executing a script; ``show()`` hands the object over
  eagerly and ``BETTER_MAD_OUTPUT`` is never demanded. The worker keeps the
  object dynamic (``rasterize()`` output re-rasterizes on zoom).
- **Pickle mode** (legacy subprocess runner): without ``_ATTACH``, the figure
  is materialized and pickled to ``$BETTER_MAD_OUTPUT``.

This module must stay dependency-light and never import Panel; it runs inside
the script process (subprocess, or the worker's exec namespace).
"""

from __future__ import annotations

import os
import pickle
from collections.abc import Callable
from pathlib import Path

import pandas as pd

ENV_DATA_DIR = "BETTER_MAD_DATA_DIR"
ENV_OUTPUT = "BETTER_MAD_OUTPUT"

# Set by the live render worker (better_mad.worker) around each script exec;
# when present, show() attaches instead of pickling (design.md §5). Workers
# must restore the previous value (normally None) after the run.
_ATTACH: Callable[[object], None] | None = None


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


def _holds_callable(value: object) -> bool:
    """True if ``value`` is a callable or a container holding one (recursively).

    Opts like ``hooks`` arrive as lists of functions, so the check must look
    inside plain containers, not just at the top-level value.
    """
    if callable(value):
        return True
    if isinstance(value, dict):
        return any(_holds_callable(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_holds_callable(v) for v in value)
    return False


def _capture_options(obj: object) -> list[dict[str, dict[str, object]] | None]:
    """Explicit per-element options, aligned with ``traverse()`` order.

    HoloViews stores ``.opts()`` in a global registry keyed by object id
    (``Store.custom_options``), so applied options **do not survive pickle**.
    They are captured here and re-applied by the runner after transport.

    Callable-valued opts (e.g. ``hooks``) are dropped — they cannot cross the
    process boundary when defined in the script: such a function pickles by
    reference in this process, but the app-side load fails to resolve it, and
    one such kwarg would sink the whole payload. Such opts simply don't
    transport.
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
                kept = {k: v for k, v in opts.kwargs.items() if not _holds_callable(v)}
                if kept:
                    entry[group] = kept
        captured.append(entry or None)

    obj.traverse(visit)  # type: ignore
    return captured


def show(obj: object) -> None:
    """Register a figure as the preview output. Last call wins.

    **Attach mode** (render worker installed ``_ATTACH``): the object is handed
    to the worker as-is — rendering happens there, eager and in-process, so
    DynamicMaps stay dynamic and invalid opts raise at run time.

    **Pickle mode** (legacy subprocess runner): accepts anything picklable;
    intended for HoloViews ``Element``, ``Overlay``, ``Layout`` and ``NdLayout``
    objects. DynamicMaps (e.g. ``rasterize()`` output) are materialized to
    their current frame first, and explicitly applied options are captured
    alongside the figure (they don't survive pickle on their own); callable-
    valued opts such as ``hooks`` are dropped at capture (see
    ``_capture_options``). The write is atomic, so a watching app never reads a
    half-written figure.
    """
    if _ATTACH is not None:
        _ATTACH(obj)
        return
    figure = _materialize(obj)
    payload = {"figure": figure, "options": _capture_options(figure)}
    out = _output_path()
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(payload, fh)
    os.replace(tmp, out)
