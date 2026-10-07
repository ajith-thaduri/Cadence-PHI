"""Transparent native -> canonical label mapping and coverage analysis."""
from __future__ import annotations

from dataclasses import dataclass, field

from harness.config import load_yaml, model_entry
from harness.taxonomy import CANONICAL_LABELS, UNMAPPED

COVERAGE_SYMBOL = {"full": "Y", "partial": "P", "zero_shot": "Z", "none": "N"}


@dataclass
class LabelMapping:
    model_key: str
    provenance: str                                   # native_trained | zero_shot_requested
    native_to_canonical: dict[str, str]
    covers: dict[str, dict[str, str]]                 # native -> {canonical: full|partial}
    rationale: dict[str, str] = field(default_factory=dict)
    outside_label: str | None = None
    label_provenance: dict[str, str] = field(default_factory=dict)   # native -> trained | zero_shot

    @classmethod
    def load(cls, model_key: str) -> "LabelMapping":
        raw = load_yaml(model_entry(model_key)["label_mapping"])
        n2c, cov, why, prov = {}, {}, {}, {}
        default_prov = "zero_shot" if raw.get("label_provenance") == "zero_shot_requested" else "trained"
        for native, spec in (raw.get("native_labels") or {}).items():
            canon = spec.get("canonical", UNMAPPED)
            if canon != UNMAPPED and canon not in CANONICAL_LABELS:
                raise ValueError(f"{model_key}: {native} maps to non-canonical {canon!r}")
            for c, level in (spec.get("covers") or {}).items():
                if c not in CANONICAL_LABELS or level not in ("full", "partial"):
                    raise ValueError(f"{model_key}: bad covers entry {native}: {c}={level}")
            n2c[native] = canon
            cov[native] = dict(spec.get("covers") or {})
            why[native] = str(spec.get("rationale", "")).strip()
            prov[native] = spec.get("provenance", default_prov)
        return cls(model_key, raw.get("label_provenance", "native_trained"), n2c, cov, why,
                   raw.get("outside_label"), prov)

    @property
    def native_labels(self) -> list[str]:
        return list(self.native_to_canonical)

    def to_canonical(self, native: str) -> str:
        return self.native_to_canonical.get(native, UNMAPPED)

    def compatible(self, native: str, gold_label: str) -> bool:
        """Label-strict match: the native label is designed to cover the gold category."""
        return gold_label in self.covers.get(native, {})

    def coverage(self) -> dict[str, str]:
        """canonical -> full | partial | zero_shot | none (best level across native labels)."""
        rank = {"none": 0, "zero_shot": 1, "partial": 2, "full": 3}
        best = {c: "none" for c in CANONICAL_LABELS}
        for native, cov in self.covers.items():
            trained = self.label_provenance.get(native, "trained") == "trained"
            for c, level in cov.items():
                lv = level if trained else "zero_shot"
                if rank[lv] > rank[best[c]]:
                    best[c] = lv
        return best

    def unmapped_native_labels(self) -> list[str]:
        return [n for n, c in self.native_to_canonical.items() if c == UNMAPPED]
