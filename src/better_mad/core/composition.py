"""Layer composition rules (design §4.2), UI-free.

Which plot types may coexist in one plot:
- xy family: scatter + scatter, scatter + line, scatter + 2D density → OK
- 1D family: histogram + density1d → OK (shared normalization)
- polar: single-layer only in v1
"""

from __future__ import annotations

PLOT_TYPE_LABELS: dict[str, str] = {
    "scatter": "Scatter",
    "histogram": "Histogram",
    "density1d": "Density (1D)",
    "density2d": "Density (2D)",
    "line": "Line graph",
    "polar": "Polar scatter",
}

_FAMILIES: dict[str, str] = {
    "scatter": "xy",
    "line": "xy",
    "density2d": "xy",
    "histogram": "1d",
    "density1d": "1d",
    "polar": "polar",
}


def family(plot_type: str) -> str:
    """Composition family of a plot type."""
    return _FAMILIES[plot_type]


def compatible(type_a: str, type_b: str) -> bool:
    """Whether two layer types may coexist in one plot."""
    return family(type_a) == family(type_b)


def compatible_types(current: str, others: list[str]) -> list[str]:
    """All plot types a layer may take, given the types of the *other* layers.

    With no other layers any type is allowed (the layer defines the family);
    otherwise only same-family types. Used to disable invalid options in the UI
    (UX §5) instead of erroring after the fact.
    """
    del current  # the layer's own type is what is being (re)chosen
    if not others:
        return list(PLOT_TYPE_LABELS)
    fam = family(others[0])
    return [t for t in PLOT_TYPE_LABELS if family(t) == fam]
