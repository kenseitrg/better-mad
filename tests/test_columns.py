"""Tests for column name sanitization (design.md §2.2)."""

from better_mad.core.columns import sanitize_columns, sanitize_name


def test_dot_becomes_underscore() -> None:
    assert sanitize_name("TR.DOMFREQ", 1) == "TR_DOMFREQ"


def test_leading_digit_gets_prefix() -> None:
    assert sanitize_name("3DT_SEC_ORD_CELCTR", 1) == "X3DT_SEC_ORD_CELCTR"


def test_whitespace_and_runs_of_specials() -> None:
    assert sanitize_name("  TR.RMS-AMP (v2) ", 1) == "TR_RMS_AMP_v2"


def test_underscore_preserved() -> None:
    assert sanitize_name("STACK_WORD", 1) == "STACK_WORD"


def test_empty_name_uses_index() -> None:
    assert sanitize_name("", 7) == "COL_7"
    assert sanitize_name("...", 3) == "COL_3"


def test_real_header() -> None:
    raw = [
        "CMP",
        "XCOR_MIDPT",
        "YCORD_MIDPT",
        "STACK_WORD",
        "ELEV_MIDPT",
        "3DT_SEC_ORD_CELCTR",
        "3DT_PRIM_ORD_CELCTR",
        "TR.DOMFREQ",
        "TR.RMSAMP",
    ]
    mapping = sanitize_columns(raw)
    assert list(mapping) == [
        "CMP",
        "XCOR_MIDPT",
        "YCORD_MIDPT",
        "STACK_WORD",
        "ELEV_MIDPT",
        "X3DT_SEC_ORD_CELCTR",
        "X3DT_PRIM_ORD_CELCTR",
        "TR_DOMFREQ",
        "TR_RMSAMP",
    ]
    assert mapping["TR_DOMFREQ"] == "TR.DOMFREQ"


def test_collision_suffixes() -> None:
    mapping = sanitize_columns(["A.B", "A_B", "A B", "A_B"])
    assert list(mapping) == ["A_B", "A_B_2", "A_B_3", "A_B_4"]
    assert mapping["A_B_3"] == "A B"


def test_case_is_preserved_no_collision() -> None:
    # Case-sensitive: "a_b" and "A_B" are distinct names.
    mapping = sanitize_columns(["A_B", "a b"])
    assert list(mapping) == ["A_B", "a_b"]


def test_collision_with_digit_start() -> None:
    # "1-X" and "1 X" both sanitize to "X1_X".
    mapping = sanitize_columns(["1-X", "1 X"])
    assert list(mapping) == ["X1_X", "X1_X_2"]


def test_collision_against_literal_suffixed_name() -> None:
    # "A_B_2" as a literal column occupies the first collision suffix slot.
    mapping = sanitize_columns(["A.B", "A_B_2", "A B"])
    assert list(mapping) == ["A_B", "A_B_2", "A_B_3"]
