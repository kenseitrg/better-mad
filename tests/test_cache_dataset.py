"""Tests for the parquet cache and dataset model (design.md §2.4)."""

import os
from pathlib import Path

import numpy as np

from better_mad.core.cache import cache_key, cache_lookup, cache_store
from better_mad.core.dataset import load_dataset
from better_mad.core.loading import ParserSettings

FIXTURES = Path(__file__).parent / "fixtures"
WS_FIXTURE = FIXTURES / "sample_ws.txt"


def _write(tmp_path: Path, name: str = "d.txt") -> Path:
    p = tmp_path / name
    p.write_text("a b\n1.0 2.0\n3.0 4.0\n")
    return p


class TestCachePrimitives:
    def test_roundtrip(self, tmp_path: Path) -> None:
        p = _write(tmp_path)
        s = ParserSettings()
        ds = load_dataset(p, use_cache=False)
        cache_store(p, s, ds.df, cache_dir=tmp_path / "cache")
        got = cache_lookup(p, s, cache_dir=tmp_path / "cache")
        assert got is not None
        assert list(got.columns) == ["a", "b"]
        assert got.attrs["display_names"] == {"a": "a", "b": "b"}

    def test_key_changes_with_settings(self, tmp_path: Path) -> None:
        p = _write(tmp_path)
        k1 = cache_key(p, ParserSettings())
        k2 = cache_key(p, ParserSettings(sentinels=(-999.25,)))
        assert k1 != k2

    def test_key_changes_with_mtime(self, tmp_path: Path) -> None:
        p = _write(tmp_path)
        s = ParserSettings()
        k1 = cache_key(p, s)
        os.utime(p, ns=(0, 1_000_000_000))
        k2 = cache_key(p, s)
        assert k1 != k2

    def test_corrupt_cache_is_a_miss(self, tmp_path: Path) -> None:
        p = _write(tmp_path)
        s = ParserSettings()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / f"{cache_key(p, s)}.parquet").write_text("not parquet")
        assert cache_lookup(p, s, cache_dir=cache_dir) is None


class TestLoadDataset:
    def test_first_load_populates_cache(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        ds1 = load_dataset(WS_FIXTURE, use_cache=True, cache_dir=cache)
        assert ds1.from_cache is False
        assert ds1.n_rows == 1000
        assert any(f.suffix == ".parquet" for f in cache.iterdir())

        ds2 = load_dataset(WS_FIXTURE, use_cache=True, cache_dir=cache)
        assert ds2.from_cache is True
        assert ds2.n_rows == 1000
        assert ds2.display_names["TR_DOMFREQ"] == "TR.DOMFREQ"
        # dtypes survive the cache roundtrip
        assert ds2.df["TR_DOMFREQ"].dtype == np.float32
        assert ds2.df["XCORD_MIDPT"].dtype == np.float64

    def test_settings_change_invalidates(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        load_dataset(WS_FIXTURE, use_cache=True, cache_dir=cache)
        ds = load_dataset(
            WS_FIXTURE,
            ParserSettings(sentinels=()),
            use_cache=True,
            cache_dir=cache,
        )
        assert ds.from_cache is False

    def test_no_cache_mode(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        ds = load_dataset(WS_FIXTURE, use_cache=False, cache_dir=cache)
        assert ds.from_cache is False
        assert not cache.exists()

    def test_dataset_metadata(self) -> None:
        ds = load_dataset(WS_FIXTURE, use_cache=False)
        assert ds.name == "sample_ws"
        assert ds.path == WS_FIXTURE
        assert ds.load_time_s > 0
        assert ds.columns[0] == "CMP"
        assert ds.computed_columns == {}
