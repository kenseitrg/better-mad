"""Column name handling.

Real seismic attribute headers contain characters that are invalid in expressions and
awkward in APIs (``TR.DOMFREQ``, ``3DT_SEC_ORD_CELCTR``). Every column therefore gets a
sanitized internal name used in expressions/APIs, while the original name is preserved
for display (design.md §2.2).

Rules (deterministic, collision-safe):
- strip surrounding whitespace,
- runs of non-``[A-Za-z0-9_]`` characters become a single ``_``,
- strip leading/trailing ``_``,
- names starting with a digit are prefixed with ``X``,
- empty names become ``COL_{index}`` (1-based),
- collisions get ``_2``, ``_3``, ... suffixes (first occurrence keeps the plain name).
"""

from __future__ import annotations

import re

_NON_IDENT = re.compile(r"[^A-Za-z0-9_]+")


def sanitize_name(name: str, index: int) -> str:
    """Sanitize a single raw column name.

    Args:
        name: original column name as it appears in the file header.
        index: 1-based position of the column in the header (used for empty names).
    """
    cleaned = _NON_IDENT.sub("_", str(name).strip()).strip("_")
    if not cleaned:
        return f"COL_{index}"
    if cleaned[0].isdigit():
        cleaned = f"X{cleaned}"
    return cleaned


def sanitize_columns(raw_names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Sanitize a full header, returning a mapping internal name -> original name.

    The mapping is collision-safe: if two raw names sanitize to the same internal name,
    later ones receive ``_2``, ``_3``, ... suffixes.
    """
    result: dict[str, str] = {}
    base_counts: dict[str, int] = {}
    taken: set[str] = set()
    for i, raw in enumerate(raw_names, start=1):
        base = sanitize_name(raw, i)
        n = base_counts.get(base, 0) + 1
        base_counts[base] = n
        candidate = base if n == 1 else f"{base}_{n}"
        while candidate in taken:
            n += 1
            base_counts[base] = n
            candidate = f"{base}_{n}"
        taken.add(candidate)
        result[candidate] = raw
    return result
