"""Synthetic Cadence benchmark loader."""
from __future__ import annotations

from pathlib import Path

from harness.datasets.base import Dataset, load_jsonl, sha256_path


def load_synthetic(name: str, path: Path) -> Dataset:
    samples = load_jsonl(path)
    bad = [s.id for s in samples if s.source != "synthetic"]
    if bad:
        raise ValueError(f"{path}: non-synthetic samples in synthetic dataset: {bad[:5]}")
    return Dataset(name=name, samples=samples, source="synthetic", path=str(path),
                   sha256=sha256_path(path), is_synthetic=True)
