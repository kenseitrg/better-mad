"""Smoke tests on fixtures cut from real processing-software exports.

Fixtures:
- sample_ws.txt: whitespace fixed-width, 3D post-stack (original sample data)
- sample_csv_nulls.csv: CSV with -9999 sentinels, valid zeros, negative values
- sample_2d_lines.txt: 2D-like data with categorical LINE_ID column
"""

from pathlib import Path

import numpy as np

from better_mad.core.loading import parse_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_csv_with_sentinels_zeros_negatives() -> None:
    df = parse_file(FIXTURES / "sample_csv_nulls.csv")
    assert df.shape == (751, 6)
    assert list(df.columns) == [
        "CMP",
        "XCORD_MIDPT",
        "YCORD_MIDPT",
        "TR_XCOR",
        "TR_HILBSTATIC",
        "TR_HILBPHASE",
    ]
    # -9999 sentinels become NaN, never remain as values
    assert df["TR_HILBSTATIC"].isna().sum() > 0
    assert not (df["TR_HILBSTATIC"] == -9999.0).any()
    # zeros are valid data
    assert (df["TR_HILBSTATIC"] == 0.0).sum() > 0
    # negative values survive
    assert df["TR_HILBPHASE"].min() < 0


def test_2d_lines_categorical_column() -> None:
    df = parse_file(FIXTURES / "sample_2d_lines.txt")
    assert df.shape == (1000, 6)
    lines = sorted(df["LINE_ID"].unique())
    assert lines == [1445.0, 1446.0]  # slice spans the line boundary at row 985
    assert df["LINE_ID"].dtype == np.float32


def test_display_names_roundtrip_on_all_fixtures() -> None:
    for name in ("sample_ws.txt", "sample_csv_nulls.csv", "sample_2d_lines.txt"):
        df = parse_file(FIXTURES / name)
        display = df.attrs["display_names"]
        assert len(display) == df.shape[1]
        assert all(isinstance(v, str) for v in display.values())
