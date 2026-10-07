"""Dataset container + hashing."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.schema import Sample


@dataclass
class Dataset:
    name: str
    samples: list[Sample]
    source: str                       # synthetic | public
    path: str
    sha256: str
    is_synthetic: bool
    notes: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        by_subset: dict[str, int] = {}
        for s in self.samples:
            by_subset[s.subset] = by_subset.get(s.subset, 0) + 1
        return {"name": self.name, "source": self.source, "path": self.path, "sha256": self.sha256,
                "documents": len(self.samples), "entities": sum(len(s.entities) for s in self.samples),
                "by_subset": by_subset, "notes": self.notes}


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def load_jsonl(path: Path) -> list[Sample]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                s = Sample(**json.loads(line))
                s.validate_offsets()
                out.append(s)
    return out
