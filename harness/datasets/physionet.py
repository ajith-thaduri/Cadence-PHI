"""Local-path adapter for the PhysioNet "Deidentified Medical Text" gold standard
(Neamatullah et al. 2008: id.text + id-phi.phrase / id.res formats).

Expected files in --data-path:
  id.text          records delimited by  START_OF_RECORD=<pid>||||<rid>||||  ...  ||||END_OF_RECORD
  id-phi.phrase    lines: "<pid> <rid> <start> <end> <TYPE?>"  (character offsets within the record)

PHI types in this file may be absent; untyped spans are labelled OTHER_UNIQUE_ID and only
LABEL-AGNOSTIC metrics are meaningful for this dataset (reported as such).
"""
from __future__ import annotations

import re
from pathlib import Path

from harness.datasets.base import Dataset, sha256_path
from harness.schema import GoldEntity, Sample

_REC = re.compile(r"START_OF_RECORD=(\d+)\|\|\|\|(\d+)\|\|\|\|\n?(.*?)\|\|\|\|END_OF_RECORD", re.S)
_TYPE_MAP = {"NAME": "NAME", "DATE": "DATE", "LOCATION": "ADDRESS", "PHONE": "PHONE",
             "AGE": None, "ID": "OTHER_UNIQUE_ID", "HCPNAME": "PROVIDER_NAME"}


def load_physionet_deid(path: Path) -> Dataset:
    text_file, phi_file = path / "id.text", path / "id-phi.phrase"
    if not text_file.exists() or not phi_file.exists():
        raise FileNotFoundError(f"expected {text_file} and {phi_file}")
    records = {(m.group(1), m.group(2)): m.group(3) for m in _REC.finditer(text_file.read_text(errors="replace"))}
    spans: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    for line in phi_file.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            spans.setdefault((parts[0], parts[1]), []).append(
                (int(parts[2]), int(parts[3]), parts[4] if len(parts) > 4 else "UNTYPED"))
    samples = []
    for key, text in records.items():
        ents = []
        for s, e, t in spans.get(key, []):
            canon = _TYPE_MAP.get(t.upper(), "OTHER_UNIQUE_ID")
            if canon and 0 <= s < e <= len(text):
                ents.append(GoldEntity(start=s, end=e, text=text[s:e], label=canon,
                                       difficulty="unknown", tags=[f"native:{t}"]))
        samples.append(Sample(id=f"{key[0]}_{key[1]}", text=text, entities=ents,
                              metadata={"subset": "PHYSIONET_DEID", "language": "en", "noise_level": "real",
                                        "source": "public", "dataset": "physionet_deid"}))
    return Dataset("physionet_deid", samples, "public", str(path), sha256_path(path), False,
                   ["Span types may be missing; use label-agnostic metrics only."])
