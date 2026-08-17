"""Tests for the workspace model: creation, dataset registration, manifest (M1)."""

from pathlib import Path

from better_mad.core.workspace import (
    MANIFEST_FILE,
    SCRIPT_NAME,
    SKILL_FILE,
    create_workspace,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_create_workspace_writes_skeleton(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    assert (ws.path / SKILL_FILE).is_file()
    assert (ws.path / SCRIPT_NAME).is_file()
    assert (ws.path / MANIFEST_FILE).is_file()
    assert ws.data_dir.is_dir()
    assert ws.datasets == {}
    skill = ws.skill_path.read_text()
    assert "better_mad.sdk" in skill
    assert "datasets.md" in skill


def test_create_workspace_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / SCRIPT_NAME).write_text("# user edits survive\n")
    ws = create_workspace(root)
    assert ws.script_path.read_text() == "# user edits survive\n"


def test_empty_manifest_says_so(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    assert "No datasets loaded yet" in ws.manifest_path.read_text()


def test_add_file_registers_dataset_and_snapshot(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ds = ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    assert ds.name == "sample_csv_nulls"
    assert set(ws.datasets) == {"sample_csv_nulls"}
    assert (ws.data_dir / "sample_csv_nulls.parquet").is_file()


def test_add_file_name_collision_gets_suffix(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    ds2 = ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    assert ds2.name == "sample_csv_nulls_2"
    assert (ws.data_dir / "sample_csv_nulls_2.parquet").is_file()


def test_manifest_contents_match_dataset(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    text = ws.manifest_path.read_text()
    ds = ws.datasets["sample_ws"]
    # dataset-level facts
    assert "## sample_ws" in text
    assert f"Rows: {len(ds.df):,}" in text
    # original hostile names and sanitized script names both present
    assert "TR.DOMFREQ" in text
    assert "`TR_DOMFREQ`" in text
    # per-column stats survive for a numeric column
    assert "float32" in text
    assert "NaN%" in text


def test_add_file_refreshes_manifest_each_time(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    assert "sample_csv_nulls" in ws.manifest_path.read_text()
    ws.add_file(FIXTURES / "sample_ws.txt")
    text = ws.manifest_path.read_text()
    assert "## sample_csv_nulls" in text
    assert "## sample_ws" in text
