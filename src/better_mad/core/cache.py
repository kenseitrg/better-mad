"""Parquet cache for parsed files (design.md §6).

After a successful parse, the dataframe (with sanitized column names) is written to
``$XDG_CACHE_HOME/better-mad/<key>.parquet`` together with the display-name mapping in
the parquet schema metadata. The key hashes absolute path + mtime + size + parser
settings, so any change to the file or its settings invalidates the entry.

The directory lives under the user cache dir (data directories are often read-only).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from better_mad.core.loading import ParserSettings

_DISPLAY_META_KEY = b"better_mad.display_names"


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "~/.cache")
    return Path(base).expanduser() / "better-mad"


def settings_fingerprint(settings: ParserSettings) -> str:
    """Stable string identity of parser settings (for cache keys)."""
    return json.dumps(
        {
            "delimiter": settings.delimiter,
            "decimal": settings.decimal,
            "sentinels": list(settings.sentinels),
            "comment": settings.comment,
            "float64_columns": (
                list(settings.float64_columns) if settings.float64_columns is not None else None
            ),
        },
        sort_keys=True,
    )


def cache_key(path: Path, settings: ParserSettings) -> str:
    stat = path.stat()
    payload = json.dumps(
        [str(path.resolve()), stat.st_mtime_ns, stat.st_size, settings_fingerprint(settings)],
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()


def cache_lookup(
    path: Path, settings: ParserSettings, cache_dir: Path | None = None
) -> pd.DataFrame | None:
    """Return the cached dataframe (with display names restored) or None on cache miss."""
    cache_dir = cache_dir or default_cache_dir()
    entry = cache_dir / f"{cache_key(path, settings)}.parquet"
    if not entry.exists():
        return None
    try:
        table = pq.read_table(entry)
    except Exception:  # corrupted cache entry -> miss
        return None
    meta = table.schema.metadata or {}
    if _DISPLAY_META_KEY not in meta:
        return None
    df = table.to_pandas()
    df.attrs["display_names"] = json.loads(meta[_DISPLAY_META_KEY].decode())
    return df


def cache_store(
    path: Path,
    settings: ParserSettings,
    df: pd.DataFrame,
    cache_dir: Path | None = None,
) -> None:
    """Write the dataframe and its display-name mapping to the cache. Best-effort."""
    cache_dir = cache_dir or default_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        entry = cache_dir / f"{cache_key(path, settings)}.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        display = json.dumps(df.attrs.get("display_names", {})).encode()
        table = table.replace_schema_metadata(
            {**(table.schema.metadata or {}), _DISPLAY_META_KEY: display}
        )
        pq.write_table(table, entry)
    except Exception:
        pass  # caching must never break loading
