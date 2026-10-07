"""Registry (known-identifier) recognizers.

A registry is scoped context for ONE request: the subject's own record plus versioned
provider / facility / staff directories, resolved by RegistryStore from the request's opaque
RegistryContext. It is attached to Presidio as an AD-HOC recognizer for that call only.
Registries derived from gold labels are refused (evaluation leakage).
"""
from __future__ import annotations

from typing import Any

from harness.phi.contracts import EvidenceSource, ScoreType
from harness.phi.recognizers.base import PHIRecognizer, RawHit
from harness.shield import registry as REG

KIND_ENTITIES = {"member": ["NAME"], "provider": ["PROVIDER_NAME"], "staff": ["CARE_TEAM_MEMBER"],
                 "facility": ["FACILITY_NAME"], "employer": ["EMPLOYER"], "contact": ["RELATIVE_NAME"],
                 "identifier": ["MEMBER_ID", "MRN", "PHONE", "NPI"],
                 "record": ["EMAIL", "ADDRESS", "CITY", "ZIP", "DOB"]}
KIND_DIRECTORY = {"provider": "provider_directory", "facility": "facility_directory", "staff": "staff_directory"}


class RegistryRecognizer(PHIRecognizer):
    source = EvidenceSource.registry

    def __init__(self, recognizer_id: str, kinds: list[str], registry: dict[str, Any], versions: dict[str, str] | None = None,
                 language: str = "en", version: str = "1", kind_entities: dict[str, list[str]] | None = None,
                 params: "REG.RegistryParams | None" = None) -> None:
        table = kind_entities or KIND_ENTITIES
        ents = sorted({e for k in kinds for e in table.get(k, [])})
        self.params = params
        super().__init__(recognizer_id, ents, language, version)
        if registry and registry.get("simulated_from_gold"):
            raise ValueError("refusing a registry derived from gold labels (evaluation leakage)")
        self.kinds = set(kinds)
        self.registry = registry or {}
        self.versions = versions or {}

    def hits(self, text: str) -> list[RawHit]:
        out = []
        for p in REG.match(text, self.registry, self.params):
            parts = p.native_label.split(":")          # registry:<kind>[:exact|fuzzy][:single_token]
            kind = parts[1]
            if kind not in self.kinds:
                continue
            if kind in ("identifier", "record"):
                st, match_type = ScoreType.registry_normalized, "normalized"
            elif len(parts) > 2 and parts[2] == "fuzzy":
                st, match_type = ScoreType.registry_fuzzy, "fuzzy"
            else:
                st, match_type = ScoreType.registry_exact, "exact"
            out.append(RawHit(p.canonical_label, p.start, p.end, float(p.score or 1.0), st, rule_id=":".join(parts[1:]),
                              metadata={"match_type": match_type, "registry_type": kind,
                                        "registry_version": self.versions.get(KIND_DIRECTORY.get(kind, "subject"), "unversioned"),
                                        "single_token": "single_token" in parts}))
        return out
