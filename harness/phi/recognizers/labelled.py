"""Labelled-value recognizer: a field label determines the TYPE of the identifier that directly follows it.

    "DEA registration TR5521408"       -> LICENSE_CERT_ID      "IBAN BE80 6186 ..."    -> ACCOUNT_ID
    "bank account #0041223344"         -> ACCOUNT_ID           "beneficiary number X"   -> MEMBER_ID
    "IMEI 913450844021025"             -> DEVICE_ID            "VIN YV1RS61R51..."      -> VEHICLE_ID

Fully declarative (configs/phi/recognizers/labelled.yaml): per entity a list of label regexes, a `bridge` (what may
sit between label and value: "is", "was", ":", "#", "no.", ...), a `value` token regex and a minimum digit count.
The label itself is never part of the span. Typical role: generic identifier rules and the model find the value
but label it OTHER_UNIQUE_ID; this evidence covers the same span with the specific type, and the policy's type
refinement (OTHER_UNIQUE_ID -> specific) applies. It also finds values the other recognizers miss.
"""
from __future__ import annotations

import re
from typing import Any

from harness.phi.contracts import EvidenceSource, ScoreType
from harness.phi.recognizers.base import PHIRecognizer, RawHit

_YEAR = re.compile(r"(?:19|20)\d{2}")


class LabelledValueRecognizer(PHIRecognizer):
    source = EvidenceSource.rule

    def __init__(self, recognizer_id: str, spec: dict[str, Any], language: str = "en") -> None:
        labels: dict[str, list[str]] = spec["labels"]
        super().__init__(recognizer_id, sorted(labels), language, str(spec.get("version", "1")))
        self.rules = [(ent, re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(lbls) + r")(?![A-Za-z0-9])", re.I))
                      for ent, lbls in labels.items()]
        self.bridge = re.compile(spec["bridge"], re.I)
        self.value = re.compile(spec["value"])
        self.cont = re.compile(spec["continuation"]) if spec.get("continuation") else None
        self.min_digits = int(spec.get("min_digits", 3))
        self.min_length = int(spec.get("min_length", 5))
        self.score = float(spec.get("score", 0.9))

    def _value_at(self, text: str, pos: int) -> tuple[int, int] | None:
        b = self.bridge.match(text, pos)
        start = b.end() if b else pos
        v = self.value.match(text, start)
        if not v:
            return None
        s, e = v.span()
        tok = text[s:e]
        if self.cont is not None and sum(c.isdigit() for c in tok) < self.min_digits:
            c = self.cont.match(text, e)                       # "NV 4KL-221", "KR 4421"
            if c and re.fullmatch(r"[A-Za-z]{1,3}", tok):
                e = c.end()
                tok = text[s:e]
        digits = sum(ch.isdigit() for ch in tok)
        if digits < self.min_digits or sum(ch.isalnum() for ch in tok) < self.min_length or _YEAR.fullmatch(tok):
            return None
        return s, e

    def hits(self, text: str) -> list[RawHit]:
        out: list[RawHit] = []
        seen: set[tuple[int, int, str]] = set()
        for ent, rx in self.rules:
            for m in rx.finditer(text):
                span = self._value_at(text, m.end())
                if span is None or (*span, ent) in seen:
                    continue
                seen.add((*span, ent))
                out.append(RawHit(ent, span[0], span[1], self.score, ScoreType.regex_match,
                                  rule_id=f"{self.recognizer_id}:{ent.lower()}", metadata={"label": m.group(0).lower()}))
        return out
