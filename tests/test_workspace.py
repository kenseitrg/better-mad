"""Tests for the workspace model: creation, dataset registration, manifest (M1)."""

import json
from pathlib import Path

from better_mad.core.loading import ParserSettings
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


def test_add_file_writes_sidecar_with_source_and_settings(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    settings = ParserSettings(
        delimiter="whitespace", sentinels=(-999.0, "NULL"), float64_columns=("XCORD_MIDPT",)
    )
    ws.add_file(FIXTURES / "sample_ws.txt", settings=settings)
    sidecar = json.loads((ws.data_dir / "sample_ws.json").read_text())
    assert sidecar["source"] == str((FIXTURES / "sample_ws.txt").resolve())
    raw = sidecar["settings"]
    assert raw["delimiter"] == "whitespace"
    assert raw["decimal"] == "."
    assert tuple(raw["sentinels"]) == (-999.0, "NULL")
    assert raw["comment"] == "#"
    assert tuple(raw["float64_columns"]) == ("XCORD_MIDPT",)
    # default settings (float64_columns None) round-trip as null, not a list
    ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    raw = json.loads((ws.data_dir / "sample_csv_nulls.json").read_text())["settings"]
    assert raw["float64_columns"] is None


def test_reopen_rehydrates_datasets_and_manifest(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ws = create_workspace(root)
    ws.add_file(FIXTURES / "sample_ws.txt")
    ws2 = create_workspace(root)
    assert set(ws2.datasets) == {"sample_ws"}
    ds = ws2.datasets["sample_ws"]
    assert ds.path == (FIXTURES / "sample_ws.txt").resolve()
    assert ds.settings == ParserSettings()
    assert ds.from_cache is True
    assert ds.load_time_s >= 0
    # the manifest is regenerated from the rehydrated datasets, not wiped
    text = ws2.manifest_path.read_text()
    assert "## sample_ws" in text
    assert "TR.DOMFREQ" in text
    assert "`TR_DOMFREQ`" in text
    assert "float32" in text


def test_reopen_preserves_display_names(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    ws2 = create_workspace(tmp_path / "ws")
    assert ws2.datasets["sample_ws"].display_names["TR_DOMFREQ"] == "TR.DOMFREQ"


def test_reopen_then_readd_gets_suffix(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ws = create_workspace(root)
    ws.add_file(FIXTURES / "sample_ws.txt")
    ws2 = create_workspace(root)
    ds2 = ws2.add_file(FIXTURES / "sample_ws.txt")
    assert ds2.name == "sample_ws_2"
    assert set(ws2.datasets) == {"sample_ws", "sample_ws_2"}
    assert (ws2.data_dir / "sample_ws_2.parquet").is_file()
    text = ws2.manifest_path.read_text()
    assert "## sample_ws\n" in text
    assert "## sample_ws_2" in text


def test_reopen_skips_corrupt_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ws = create_workspace(root)
    ws.add_file(FIXTURES / "sample_ws.txt")
    (ws.data_dir / "sample_ws.parquet").write_bytes(b"garbage, not a parquet file")
    ws2 = create_workspace(root)  # must not raise
    assert "sample_ws" not in ws2.datasets
    assert "No datasets loaded yet" in ws2.manifest_path.read_text()


def test_reopen_without_sidecar_falls_back(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ws = create_workspace(root)
    ws.add_file(FIXTURES / "sample_ws.txt")
    (ws.data_dir / "sample_ws.json").unlink()
    ws2 = create_workspace(root)
    ds = ws2.datasets["sample_ws"]
    assert ds.path == ws2.data_dir / "sample_ws.parquet"
    assert ds.settings == ParserSettings()


def test_reopen_with_broken_sidecar_falls_back(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ws = create_workspace(root)
    ws.add_file(FIXTURES / "sample_ws.txt")
    (ws.data_dir / "sample_ws.json").write_text("{not json")
    ws2 = create_workspace(root)
    ds = ws2.datasets["sample_ws"]  # dataset kept, sidecar ignored
    assert ds.path == ws2.data_dir / "sample_ws.parquet"
    assert ds.settings == ParserSettings()


def test_reopen_ignores_orphan_sidecar(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    ws = create_workspace(root)
    ws.add_file(FIXTURES / "sample_ws.txt")
    (ws.data_dir / "ghost.json").write_text('{"source": "/gone", "settings": {}}')
    ws2 = create_workspace(root)
    assert set(ws2.datasets) == {"sample_ws"}
