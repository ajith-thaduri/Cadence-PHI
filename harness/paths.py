"""Repository paths."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ROOT / "configs"
DATA = ROOT / "data"
SYNTHETIC = DATA / "synthetic"
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
