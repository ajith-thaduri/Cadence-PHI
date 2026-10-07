"""Deterministic rule families (harness/shield/rules.py) as recognizers."""
from __future__ import annotations

import re

from harness.phi.contracts import EvidenceSource, ScoreType
from harness.phi.recognizers.base import PHIRecognizer, RawHit
from harness.shield import rules as R

FAMILIES = {"phones": R.phones, "spoken_numbers": R.spoken_numbers, "emails_urls": R.emails_urls,
            "keyword_ids": R.keyword_ids, "zips": R.zips, "dates": R.dates, "addresses": R.addresses,
            "titled_names": R.titled_names}
# entities each family can emit (entity responsibility matrix)
FAMILY_ENTITIES = {"phones": ["PHONE"], "spoken_numbers": ["PHONE", "MEMBER_ID", "MRN", "OTHER_UNIQUE_ID", "ZIP"],
                   "emails_urls": ["EMAIL", "URL"],
                   "keyword_ids": ["MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "OTHER_UNIQUE_ID", "PHONE"],
                   "zips": ["ZIP"], "dates": ["DATE", "DOB"], "addresses": ["ADDRESS", "CITY", "STATE", "ZIP"],
                   "titled_names": ["NAME", "PROVIDER_NAME", "RELATIVE_NAME", "CARE_TEAM_MEMBER"]}


def npi_luhn_valid(digits: str) -> bool:
    d = re.sub(r"\D", "", digits)
    if len(d) != 10:
        return False
    total = 0
    for i, c in enumerate(reversed("80840" + d)):
        x = int(c)
        if i % 2 == 1:
            x = x * 2 - 9 if x * 2 > 9 else x * 2
        total += x
    return total % 10 == 0


class RuleFamilyRecognizer(PHIRecognizer):
    source = EvidenceSource.rule

    def __init__(self, recognizer_id: str, family: str, language: str = "en", version: str = "1",
                 emits: list[str] | None = None) -> None:
        if family not in FAMILIES:
            raise ValueError(f"unknown rule family {family!r}; known: {sorted(FAMILIES)}")
        super().__init__(recognizer_id, emits or FAMILY_ENTITIES[family], language, version)
        self.fn = FAMILIES[family]

    def hits(self, text: str) -> list[RawHit]:
        out = []
        allowed = set(self.supported_entities)
        for p in self.fn(text):
            if p.canonical_label not in allowed:
                continue
            st = ScoreType.checksum_valid if p.canonical_label == "NPI" and npi_luhn_valid(p.text) else ScoreType.regex_match
            out.append(RawHit(p.canonical_label, p.start, p.end, float(p.score if p.score is not None else 1.0), st,
                              rule_id=p.native_label.split(":", 1)[-1]))
        return out
