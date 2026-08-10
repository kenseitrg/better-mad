"""Tests for file loading (design.md §2.1, §2.3, §2.4)."""

from pathlib import Path

import numpy as np
import pytest

from better_mad.core.loading import (
    DEFAULT_SENTINELS,
    LoaderError,
    ParserSettings,
    detect_delimiter,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures"
WS_FIXTURE = FIXTURES / "sample_ws.txt"  # trimmed real file: whitespace, 1000 rows


class TestDelimiterDetection:
    def test_whitespace(self) -> None:
        assert detect_delimiter("CMP  XCORD  YCORD\n1  2  3\n") == "whitespace"

    def test_tab_wins_over_comma(self) -> None:
        assert detect_delimiter("a\tb,c\n") == "tab"

    def test_comma(self) -> None:
        assert detect_delimiter("a,b,c\n1,2,3\n") == "comma"

    def test_comments_skipped(self) -> None:
        assert detect_delimiter("# comment, with comma\na b c\n") == "whitespace"

    def test_empty_defaults_whitespace(self) -> None:
        assert detect_delimiter("") == "whitespace"


class TestWhitespaceFixture:
    def test_loads_real_format(self) -> None:
        df = parse_file(WS_FIXTURE)
        assert df.shape == (1000, 9)
        assert list(df.columns) == [
            "CMP",
            "XCORD_MIDPT",
            "YCORD_MIDPT",
            "STACK_WORD",
            "ELEV_MIDPT",
            "X3DT_SEC_ORD_CELCTR",
            "X3DT_PRIM_ORD_CELCTR",
            "TR_DOMFREQ",
            "TR_RMSAMP",
        ]

    def test_display_names_preserved(self) -> None:
        df = parse_file(WS_FIXTURE)
        display = df.attrs["display_names"]
        assert display["TR_DOMFREQ"] == "TR.DOMFREQ"
        assert display["X3DT_SEC_ORD_CELCTR"] == "3DT_SEC_ORD_CELCTR"

    def test_dtype_policy(self) -> None:
        df = parse_file(WS_FIXTURE)
        assert df["XCORD_MIDPT"].dtype == np.float64  # coordinate
        assert df["YCORD_MIDPT"].dtype == np.float64
        assert df["CMP"].dtype == np.float32
        assert df["TR_DOMFREQ"].dtype == np.float32

    def test_values(self) -> None:
        df = parse_file(WS_FIXTURE)
        assert df["CMP"].iloc[0] == pytest.approx(876149.0)
        assert df["TR_RMSAMP"].iloc[0] == pytest.approx(3.113, rel=1e-3)


class TestSentinels:
    def test_sentinels_become_nan(self, tmp_path: Path) -> None:
        p = tmp_path / "s.txt"
        p.write_text("a b\n1.0 -999.25\n-9999.0 2.0\n0.0 3.0\n")
        df = parse_file(p, ParserSettings(sentinels=(-999.25, -9999.0)))
        assert np.isnan(df["a"].iloc[1])
        assert np.isnan(df["b"].iloc[0])

    def test_zero_is_always_valid(self, tmp_path: Path) -> None:
        p = tmp_path / "z.txt"
        p.write_text("a b\n0.0 0\n1.0 2.0\n")
        df = parse_file(p, ParserSettings(sentinels=DEFAULT_SENTINELS))
        assert df["a"].iloc[0] == 0.0
        assert df["b"].iloc[0] == 0.0

    def test_custom_sentinel(self, tmp_path: Path) -> None:
        p = tmp_path / "c.txt"
        p.write_text("a\n1e30\n5.0\n")
        df = parse_file(p, ParserSettings(sentinels=(1.0e30,)))
        assert np.isnan(df["a"].iloc[0])
        assert df["a"].iloc[1] == 5.0

    def test_string_null_sentinel(self, tmp_path: Path) -> None:
        # Some software emits the literal string NULL; the column must stay numeric.
        p = tmp_path / "n.txt"
        p.write_text("a b\n1.0 NULL\nNULL 2.0\n3.0 4.0\n")
        df = parse_file(p)
        assert np.isnan(df["b"].iloc[0])
        assert np.isnan(df["a"].iloc[1])
        assert df["a"].iloc[2] == 3.0
        assert df["a"].dtype == np.float32

    def test_default_sentinels_include_null_token(self) -> None:
        assert "NULL" in DEFAULT_SENTINELS
        assert -9999.0 in DEFAULT_SENTINELS


class TestFormats:
    def test_csv(self, tmp_path: Path) -> None:
        p = tmp_path / "d.csv"
        p.write_text("CMP,A.B\n1,2.5\n2,3.5\n")
        df = parse_file(p)
        assert list(df.columns) == ["CMP", "A_B"]
        assert df.shape == (2, 2)

    def test_tab(self, tmp_path: Path) -> None:
        p = tmp_path / "d.tsv"
        p.write_text("a\tb\n1\t2\n")
        df = parse_file(p)
        assert df["b"].iloc[0] == 2.0

    def test_decimal_comma(self, tmp_path: Path) -> None:
        p = tmp_path / "d.txt"
        p.write_text("a b\n1,5 2,5\n")
        df = parse_file(p, ParserSettings(decimal=","))
        assert df["a"].iloc[0] == pytest.approx(1.5)
        assert df["b"].iloc[0] == pytest.approx(2.5)

    def test_decimal_comma_with_comma_delimiter_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "d.csv"
        p.write_text("a,b\n1,2\n")
        with pytest.raises(LoaderError, match="ambiguous"):
            parse_file(p, ParserSettings(delimiter="comma", decimal=","))

    def test_comments_and_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "c.txt"
        p.write_text("# header comment\n\na b\n1 2\n\n# mid comment\n3 4\n")
        df = parse_file(p)
        assert df.shape == (2, 2)

    def test_ragged_rows_tolerated(self, tmp_path: Path) -> None:
        # Short rows are padded with NaN; never crash the load (design.md §2.1).
        p = tmp_path / "r.txt"
        p.write_text("a b c\n1 2 3\n4 5\n6 7 8\n")
        df = parse_file(p)
        assert df.shape == (3, 3)
        assert np.isnan(df["c"].iloc[1])


class TestErrors:
    def test_missing_file(self) -> None:
        with pytest.raises(LoaderError, match="not found"):
            parse_file("/nonexistent/file.txt")

    def test_non_numeric_column(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.txt"
        p.write_text("a b\n1 foo\n2 bar\n")
        with pytest.raises(LoaderError, match="not numeric"):
            parse_file(p)

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.txt"
        p.write_text("a b\n")
        with pytest.raises(LoaderError, match="no data"):
            parse_file(p)
