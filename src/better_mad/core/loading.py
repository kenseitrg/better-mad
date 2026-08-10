"""File loading: parser settings, delimiter detection, pandas-based parsing.

Implements design.md §2.1 (format), §2.3 (sentinels), §2.4 (dtypes).
The loader is pure (no caching, no UI); caching and the dataset model compose it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

from better_mad.core.columns import sanitize_columns

Delimiter = Literal["auto", "whitespace", "comma", "tab"]

#: Industry-common null sentinels used as defaults (design.md §2.3). 0 is never a sentinel.
#: Numeric sentinels plus string tokens (e.g. "NULL") some software emits.
DEFAULT_SENTINELS: tuple[float | str, ...] = (
    -999.25,
    -999.0,
    -9999.0,
    -99999.0,
    1.0e30,
    "NULL",
)

#: Column names matching this pattern are kept as float64 (coordinates); everything else
#: becomes float32 (design.md §2.4). Example matches: XCORD_MIDPT, YCORD_MIDPT.
_COORD_RE = re.compile(r"(?i)(^|_)(x|y)[a-z_]*(cord|coord)")

_SNIFF_LINES = 20


class LoaderError(Exception):
    """Raised when a file cannot be parsed; message is user-presentable."""


@dataclass(frozen=True)
class ParserSettings:
    """Per-file parsing configuration (serializable, part of session state)."""

    delimiter: Delimiter = "auto"
    decimal: str = "."
    sentinels: tuple[float | str, ...] = field(default_factory=lambda: DEFAULT_SENTINELS)
    comment: str = "#"
    #: Sanitized column names to force to float64; None = auto-detect coordinate names.
    float64_columns: tuple[str, ...] | None = None


def detect_delimiter(sample: str) -> Delimiter:
    """Guess the delimiter from the first lines of a file.

    Rules: tabs win if present in the header; commas win if the header has commas but no
    tabs; otherwise whitespace. Only the first non-comment line is decisive.
    """
    for line in sample.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in line:
            return "tab"
        if "," in line:
            return "comma"
        return "whitespace"
    return "whitespace"


def _head(path: Path, n_lines: int = _SNIFF_LINES) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return "".join(f.readline() for _ in range(n_lines))


def _sep_for(delimiter: Delimiter, sample: str) -> tuple[str, Literal["c", "python"]]:
    """Return (sep, engine) for pandas.read_csv."""
    resolved = delimiter if delimiter != "auto" else detect_delimiter(sample)
    if resolved == "whitespace":
        return r"\s+", "python"
    if resolved == "tab":
        return "\t", "c"
    return ",", "c"


def _sentinel_strings(sentinels: tuple[float | str, ...]) -> list[str]:
    """String variants of sentinel values, since pandas matches na_values literally."""
    variants: set[str] = set()
    for s in sentinels:
        if isinstance(s, str):
            variants.add(s)
            continue
        for v in (str(s), f"{s:g}", repr(s)):
            variants.add(v)
            variants.add(v.replace("e+", "e").replace("E+", "E"))
    return sorted(variants)


def parse_file(path: str | Path, settings: ParserSettings | None = None) -> pd.DataFrame:
    """Parse a tabular attribute file into a DataFrame with sanitized column names.

    All columns must be numeric; non-numeric columns raise :class:`LoaderError`.
    Attribute columns are float32, coordinate-like columns float64. Sentinels become NaN.
    Short rows are padded with NaN rather than failing the load.
    """
    path = Path(path)
    settings = settings or ParserSettings()
    if not path.exists():
        raise LoaderError(f"file not found: {path}")

    if settings.decimal == "," and settings.delimiter == "comma":
        raise LoaderError("decimal ',' is ambiguous with comma delimiter")

    sample = _head(path)
    sep, engine = _sep_for(settings.delimiter, sample)

    try:
        df = pd.read_csv(
            path,
            sep=sep,
            engine=engine,
            header=0,
            decimal=settings.decimal,
            comment=settings.comment,
            na_values=_sentinel_strings(settings.sentinels),
            on_bad_lines="skip",  # rows with too many fields
            skip_blank_lines=True,
        )
    except Exception as exc:  # pandas raises assorted parser errors
        raise LoaderError(f"failed to parse {path.name}: {exc}") from exc

    if df.empty:
        raise LoaderError(f"{path.name}: no data rows found")

    # Sanitize header names; keep originals for display via df.attrs.
    raw_names = [str(c) for c in df.columns]
    display = sanitize_columns(raw_names)
    df = df.rename(columns=dict(zip(raw_names, display, strict=True)))
    df.attrs["display_names"] = display

    # Enforce numeric columns and apply the dtype policy.
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() < df[col].notna().sum():
                raise LoaderError(f"{path.name}: column '{display[col]}' is not numeric")
            df[col] = converted

    f64 = (
        set(settings.float64_columns)
        if settings.float64_columns is not None
        else {c for c in df.columns if _COORD_RE.search(c)}
    )
    for col in df.columns:
        df[col] = df[col].astype("float64" if col in f64 else "float32")

    return df
