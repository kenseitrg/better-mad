"""Tests for the workspace model: creation, dataset registration, manifest (M1);
uploads / re-parse / close / head (M3.5)."""

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from better_mad.core.loading import LoaderError, ParserSettings
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


# --- M3.5: uploads, re-parse, close, head (handoff §9) -------------------------


def test_add_upload_persists_raw_copy_and_points_source_at_it(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    upload = tmp_path / "incoming" / "sample_ws.txt"  # simulates the hub's temp file
    upload.parent.mkdir()
    shutil.copyfile(FIXTURES / "sample_ws.txt", upload)
    ds = ws.add_upload(upload, "sample_ws.txt")
    assert ds.name == "sample_ws"
    copy = ws.sources_dir / "sample_ws.txt"
    assert copy.is_file()
    assert copy.read_bytes() == (FIXTURES / "sample_ws.txt").read_bytes()
    assert ds.path == copy
    sidecar = json.loads((ws.data_dir / "sample_ws.json").read_text())
    assert sidecar["source"] == str(copy.resolve())
    # reopened workspace rehydrates from the persisted copy
    ws2 = create_workspace(tmp_path / "ws")
    assert ws2.datasets["sample_ws"].path == copy


def test_add_upload_loader_error_registers_nothing_and_copies_nothing(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    upload = tmp_path / "junk.txt"
    upload.write_text("")  # no data rows → LoaderError
    with pytest.raises(LoaderError):
        ws.add_upload(upload, "junk.txt")
    assert ws.datasets == {}
    assert not ws.sources_dir.exists() or not any(ws.sources_dir.iterdir())
    assert "junk" not in ws.manifest_path.read_text()


def test_add_upload_dedup_names_the_copy_after_final_name(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    upload = tmp_path / "up.csv"
    shutil.copyfile(FIXTURES / "sample_csv_nulls.csv", upload)
    ws.add_upload(upload, "up.csv")
    ds2 = ws.add_upload(upload, "up.csv")
    assert ds2.name == "up_2"
    assert (ws.sources_dir / "up_2.csv").is_file()  # T6: final name, not raw name


def test_add_upload_suffix_preserved_for_extensions(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    upload = tmp_path / "data.csv"
    shutil.copyfile(FIXTURES / "sample_csv_nulls.csv", upload)
    ds = ws.add_upload(upload, "data.csv")
    assert (ws.sources_dir / "data.csv").is_file()
    assert ds.path.suffix == ".csv"


def test_reparse_new_settings_change_data_and_sidecar_in_place(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ds0 = ws.add_file(FIXTURES / "sample_csv_nulls.csv")  # defaults: -9999.0 is a sentinel
    assert ds0.df["TR_HILBSTATIC"].isna().any()
    before_manifest = ws.manifest_path.read_text()

    ds = ws.reparse("sample_csv_nulls", ParserSettings(sentinels=()))
    assert ds.name == "sample_csv_nulls"  # name preserved even as settings change
    assert not ds.df["TR_HILBSTATIC"].isna().any()  # NaN% visibly changed
    assert ds.settings == ParserSettings(sentinels=())
    sidecar = json.loads((ws.data_dir / "sample_csv_nulls.json").read_text())
    assert sidecar["settings"]["sentinels"] == []
    assert sidecar["source"] == str((FIXTURES / "sample_csv_nulls.csv").resolve())
    assert ws.manifest_path.read_text() != before_manifest  # manifest regenerated
    # the parquet snapshot itself was replaced (atomic overwrite)
    reread = pd.read_parquet(ws.data_dir / "sample_csv_nulls.parquet")
    assert not reread["TR_HILBSTATIC"].isna().any()


def test_reparse_loader_error_leaves_dataset_untouched(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ds0 = ws.add_file(FIXTURES / "sample_csv_nulls.csv")
    df_before = ds0.df.copy()
    with pytest.raises(LoaderError):  # ambiguous combination, guarded by the loader
        ws.reparse("sample_csv_nulls", ParserSettings(delimiter="comma", decimal=","))
    assert ws.datasets["sample_csv_nulls"].settings == ds0.settings
    pd.testing.assert_frame_equal(ws.datasets["sample_csv_nulls"].df, df_before)


def test_reparse_unknown_name_raises_keyerror(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    with pytest.raises(KeyError):
        ws.reparse("ghost", ParserSettings())


def test_remove_upload_deletes_copy_but_cli_source_survives(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    upload = tmp_path / "up.txt"
    shutil.copyfile(FIXTURES / "sample_ws.txt", upload)
    ws.add_upload(upload, "up.txt")
    cli_file = tmp_path / "cli.txt"
    shutil.copyfile(FIXTURES / "sample_ws.txt", cli_file)
    ws.add_file(cli_file)

    ws.remove("up")  # upload: parquet + sidecar + raw copy all go
    assert not (ws.data_dir / "up.parquet").exists()
    assert not (ws.data_dir / "up.json").exists()
    assert not (ws.sources_dir / "up.txt").exists()

    ws.remove("cli")  # T4: a user's CLI file is never deleted
    assert not (ws.data_dir / "cli.parquet").exists()
    assert cli_file.is_file()
    assert ws.datasets == {}
    assert "No datasets loaded yet" in ws.manifest_path.read_text()


def test_remove_unknown_name_raises_keyerror(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    with pytest.raises(KeyError):
        ws.remove("ghost")


def test_dataset_head_returns_raw_source_lines(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    ws.add_file(FIXTURES / "sample_ws.txt")
    head = ws.dataset_head("sample_ws", lines=3)
    assert head.splitlines()[0].strip().startswith("CMP")
    assert len(head.splitlines()) == 3
    assert ws.dataset_head("sample_ws") == _head_of(FIXTURES / "sample_ws.txt", 20)


def test_dataset_head_missing_source_raises_filenotfound(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    temp_file = tmp_path / "gone.txt"
    shutil.copyfile(FIXTURES / "sample_ws.txt", temp_file)
    ws.add_file(temp_file)
    temp_file.unlink()
    with pytest.raises(FileNotFoundError):
        ws.dataset_head("gone")
    with pytest.raises(KeyError):
        ws.dataset_head("ghost")


def test_rehydrate_ignores_sources_subdirectory(tmp_path: Path) -> None:
    ws = create_workspace(tmp_path / "ws")
    upload = tmp_path / "up.txt"
    shutil.copyfile(FIXTURES / "sample_ws.txt", upload)
    ws.add_upload(upload, "up.txt")
    ws2 = create_workspace(tmp_path / "ws")  # globs .data/*.parquet — sources/ invisible
    assert set(ws2.datasets) == {"up"}
    assert ws2.datasets["up"].path == ws.sources_dir / "up.txt"


def _head_of(path: Path, n: int) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return "".join(f.readline() for _ in range(n))
