"""Serializable style models (M4, design §4.3/§4.4).

Plain dataclasses, UI-free: they feed both the render layer and (in M6) the
plot-config JSON. All defaults mirror the previous hard-coded look so the
refactor is behavior-preserving.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Colormap picker options (M4 §4.4). viridis first: it is the legacy default.
COLORMAPS = ["viridis", "magma", "inferno", "plasma", "cividis", "turbo", "RdBu_r"]

#: Scatter marker symbols (Bokeh names).
SYMBOLS = ["circle", "square", "triangle", "cross", "diamond", "asterisk"]


@dataclass
class LayerStyle:
    """Per-layer visual style (design §4.3 layer properties)."""

    color: str = "steelblue"  # used when no z column is selected
    cmap: str = "viridis"
    alpha: float = 0.6
    size: float = 2.0  # scatter marker size
    symbol: str = "circle"
    line_width: float = 1.0
    #: Percentile clip for the color scale, e.g. (2, 98) (design §4.4).
    #: None → full data range. Explicit min/max below always win over clipping.
    clip_percentiles: tuple[float, float] | None = (2.0, 98.0)
    clim_min: float | None = None  # explicit color-scale minimum override
    clim_max: float | None = None  # explicit color-scale maximum override
    log_color: bool = False


@dataclass
class PlotStyle:
    """Plot-level (all-layers) style; serializable subset of design §4.3."""

    title: str = ""  # empty → auto-generated from data/type


def default_layer_style() -> LayerStyle:
    return LayerStyle()
