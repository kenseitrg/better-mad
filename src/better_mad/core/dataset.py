"""Dataset model: a loaded file plus its metadata (design.md §2, UX.md §2).

The core data unit of the app. Loading composes :mod:`better_mad.core.loading` and
:mod:`better_mad.core.cache`; nothing here touches the UI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from better_mad.core.cache import cache_lookup, cache_store
from better_mad.core.loading import LoaderError, ParserSettings, parse_file


@dataclass
class Dataset:
    """A loaded attribute file.

    Attributes:
        name: human-facing dataset name (file stem by default).
        path: source file.
        df: dataframe with sanitized column names; ``df.attrs["display_names"]`` maps
            internal -> original names.
        settings: parser settings used for the load (part of session state).
        load_time_s: wall-clock seconds for the load.
        from_cache: whether the parquet cache served this load.
        computed_columns: name -> expression for M5 computed columns.
    """

    name: str
    path: Path
    df: pd.DataFrame
    settings: ParserSettings
    load_time_s: float
    from_cache: bool
    computed_columns: dict[str, str] = field(default_factory=dict)

    @property
    def display_names(self) -> dict[str, str]:
        return dict(self.df.attrs.get("display_names", {}))

    @property
    def n_rows(self) -> int:
        return len(self.df)

    @property
    def columns(self) -> list[str]:
        return list(self.df.columns)


def load_dataset(
    path: str | Path,
    settings: ParserSettings | None = None,
    *,
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> Dataset:
    """Load an attribute file into a :class:`Dataset`, using the parquet cache if possible."""
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"file not found: {path}")
    settings = settings or ParserSettings()
    t0 = time.perf_counter()

    df: pd.DataFrame | None = None
    if use_cache:
        df = cache_lookup(path, settings, cache_dir)
    from_cache = df is not None

    if df is None:
        df = parse_file(path, settings)
        if use_cache:
            cache_store(path, settings, df, cache_dir)

    return Dataset(
        name=path.stem,
        path=path,
        df=df,
        settings=settings,
        load_time_s=time.perf_counter() - t0,
        from_cache=from_cache,
    )
