"""Application state: the registry of loaded datasets.

Plain data, no widgets — usable headless (tests, future CLI rendering).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from better_mad.core.dataset import Dataset, load_dataset
from better_mad.core.loading import LoaderError


@dataclass
class LoadFailure:
    path: Path
    error: str


@dataclass
class AppState:
    """All datasets currently loaded in the app, keyed by dataset name."""

    datasets: dict[str, Dataset] = field(default_factory=dict)
    failures: list[LoadFailure] = field(default_factory=list)

    def load_files(self, paths: list[str | Path]) -> None:
        """Load files into the registry; failures are collected, never raised."""
        for p in paths:
            path = Path(p)
            try:
                ds = load_dataset(path)
            except LoaderError as exc:
                self.failures.append(LoadFailure(path=path, error=str(exc)))
                continue
            self.datasets[self._unique_name(ds)] = ds

    def _unique_name(self, ds: Dataset) -> str:
        name = ds.name
        i = 2
        while name in self.datasets:
            name = f"{ds.name}_{i}"
            i += 1
        return name

    def load_report(self) -> str:
        lines = [
            f"  {name}: {ds.n_rows} rows x {len(ds.columns)} cols "
            f"in {ds.load_time_s:.2f}s (cache={ds.from_cache})"
            for name, ds in self.datasets.items()
        ]
        lines += [f"  FAILED {f.path}: {f.error}" for f in self.failures]
        return "\n".join(lines) if lines else "  (no files)"

    def column_options(self, name: str) -> dict[str, str]:
        """{display name: internal name} for Select widgets, in file order."""
        ds = self.datasets[name]
        return {ds.display_names.get(c, c): c for c in ds.columns}
