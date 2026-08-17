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


def show(obj: object) -> None:
    """Register a figure as the preview output. Last call wins.

    Accepts anything picklable; intended for HoloViews ``Element``, ``Overlay``,
    ``Layout`` and ``NdLayout`` objects. The write is atomic, so a watching app
    never reads a half-written figure.
    """
    out = _output_path()
    tmp = out.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(obj, fh)
    os.replace(tmp, out)
