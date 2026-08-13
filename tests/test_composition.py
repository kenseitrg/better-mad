"""Composition rules and style model (M4, design §4.2)."""

from better_mad.core.composition import compatible, compatible_types, family
from better_mad.core.styles import LayerStyle, PlotStyle


class TestComposition:
    def test_families(self) -> None:
        assert family("scatter") == "xy"
        assert family("line") == "xy"
        assert family("density2d") == "xy"
        assert family("histogram") == "1d"
        assert family("density1d") == "1d"
        assert family("polar") == "polar"

    def test_compatible_pairs(self) -> None:
        assert compatible("scatter", "scatter")
        assert compatible("scatter", "line")
        assert compatible("scatter", "density2d")
        assert compatible("histogram", "density1d")
        assert not compatible("scatter", "histogram")
        assert not compatible("scatter", "polar")
        assert not compatible("histogram", "line")

    def test_compatible_types_no_others_all_allowed(self) -> None:
        assert len(compatible_types("scatter", [])) == 6

    def test_compatible_types_restricted_by_others(self) -> None:
        assert compatible_types("scatter", ["line"]) == ["scatter", "density2d", "line"]
        assert compatible_types("histogram", ["density1d"]) == ["histogram", "density1d"]
        assert compatible_types("scatter", ["polar"]) == ["polar"]


class TestStyleModels:
    def test_defaults_match_legacy_look(self) -> None:
        s = LayerStyle()
        assert s.color == "steelblue"
        assert s.cmap == "viridis"
        assert s.alpha == 0.6
        assert s.size == 2.0
        assert s.clip_percentiles == (2.0, 98.0)
        assert PlotStyle().title == ""
