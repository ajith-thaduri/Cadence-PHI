"""Span matching between gold entities and predictions.

Definitions (all character-offset based):

core characters of a gold entity
    alphanumeric characters inside the span, excluding a leading title token
    (Dr., Dra., Mrs., ...) and, for ID categories only, literal affix words such as
    "MRN", "MBR", "TEST", "NPI" that label the identifier rather than identify anyone.

coverage
    |core chars covered by ANY prediction| / |core chars|  (label-agnostic)

Per gold entity we report several match modes:
    exact    a prediction has exactly the same [start, end)
    lenient  a prediction's boundaries equal gold's after normalising edges
             (whitespace, punctuation, leading titles, trailing possessive)
    overlap  some prediction overlaps the span by >= 1 character
    partial  coverage >= partial_min_gold_coverage (default 0.5)
    full     coverage == 1.0  -> the entity would be completely redacted

Outcome category (for the overlap reconciliation report):
    exact_match | partial_match (overlaps but not exact) | miss
Predictions overlapping no gold span (and no ignore span) are spurious.

"strict" variants only consider predictions whose native label is designed to cover
the gold category according to the model's label mapping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from harness.schema import GoldEntity, Prediction, Sample
from harness.taxonomy import ID_CATEGORIES

DEFAULT_TITLES = {"dr", "dra", "doctor", "doctora", "mr", "mrs", "ms", "miss", "sr", "sra",
                  "srta", "nurse", "prof"}
ID_AFFIXES = {"mrn", "mbr", "test", "npi", "acct", "id", "member", "no", "num"}
_TOKEN = re.compile(r"[A-Za-z0-9À-ɏ]+")


def core_chars(text: str, ent: GoldEntity, titles: set[str] = DEFAULT_TITLES) -> set[int]:
    span = text[ent.start:ent.end]
    out: set[int] = set()
    first = True
    for m in _TOKEN.finditer(span):
        tok = m.group().lower()
        if first and tok in titles:
            first = False
            continue
        first = False
        if ent.label in ID_CATEGORIES and tok in ID_AFFIXES and not any(c.isdigit() for c in tok):
            continue
        out.update(range(ent.start + m.start(), ent.start + m.end()))
    if not out:   # degenerate span: fall back to all non-space chars
        out = {i for i in range(ent.start, ent.end) if not text[i].isspace()}
    return out


def _normalise(text: str, s: int, e: int, titles: set[str]) -> tuple[int, int]:
    while s < e and (text[s].isspace() or not text[s].isalnum()):
        s += 1
    while e > s and (text[e - 1].isspace() or not text[e - 1].isalnum()):
        e -= 1
    m = re.match(r"([A-Za-z]+)\.?\s+", text[s:e])
    if m and m.group(1).lower() in titles:
        s += m.end()
    if text[s:e].lower().endswith(("'s", "’s")):
        e -= 2
    return s, e


@dataclass
class GoldMatch:
    entity: GoldEntity
    core_size: int
    covered: int
    exact: bool
    lenient: bool
    overlap: bool
    strict_exact: bool
    strict_overlap: bool
    strict_covered: int
    overlapping: list[Prediction] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.covered / self.core_size if self.core_size else 1.0

    @property
    def strict_coverage(self) -> float:
        return self.strict_covered / self.core_size if self.core_size else 1.0

    @property
    def full(self) -> bool:
        return self.covered == self.core_size

    @property
    def outcome(self) -> str:
        if self.exact:
            return "exact_match"
        return "partial_match" if self.overlap else "miss"

    def partial(self, min_cov: float) -> bool:
        return self.coverage >= min_cov


@dataclass
class PredMatch:
    prediction: Prediction
    overlaps_gold: bool
    exact_gold: bool
    strict_correct: bool       # overlaps a gold entity whose label it is designed to cover
    strict_exact: bool
    ignored: bool              # overlaps only an ignore span -> neither TP nor FP
    gold_labels: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.ignored:
            return "ignored"
        if self.exact_gold:
            return "exact_match"
        return "partial_match" if self.overlaps_gold else "spurious"


@dataclass
class SampleMatch:
    sample: Sample
    gold: list[GoldMatch]
    preds: list[PredMatch]


def _ov(a_s: int, a_e: int, b_s: int, b_e: int) -> bool:
    return a_s < b_e and b_s < a_e


def match_sample(sample: Sample, preds: list[Prediction],
                 compatible: Callable[[str, str], bool],
                 titles: set[str] = DEFAULT_TITLES,
                 visible_char_end: Optional[int] = None) -> SampleMatch:
    text = sample.text
    covered_all: set[int] = set()
    for p in preds:
        covered_all.update(range(p.start, p.end))

    gms: list[GoldMatch] = []
    for g in sample.entities:
        core = core_chars(text, g, titles)
        ovl = [p for p in preds if _ov(p.start, p.end, g.start, g.end)]
        strict = [p for p in ovl if compatible(p.native_label, g.label)]
        strict_cov: set[int] = set()
        for p in strict:
            strict_cov.update(range(p.start, p.end))
        gn = _normalise(text, g.start, g.end, titles)
        gms.append(GoldMatch(
            entity=g, core_size=len(core), covered=len(core & covered_all),
            exact=any(p.start == g.start and p.end == g.end for p in ovl),
            lenient=any(_normalise(text, p.start, p.end, titles) == gn for p in ovl),
            overlap=bool(ovl),
            strict_exact=any(p.start == g.start and p.end == g.end for p in strict),
            strict_overlap=bool(strict), strict_covered=len(core & strict_cov),
            overlapping=ovl,
        ))

    pms: list[PredMatch] = []
    for p in preds:
        ov_gold = [g for g in sample.entities if _ov(p.start, p.end, g.start, g.end)]
        ov_ign = any(_ov(p.start, p.end, i.start, i.end) for i in sample.ignore_spans)
        pms.append(PredMatch(
            prediction=p, overlaps_gold=bool(ov_gold),
            exact_gold=any(g.start == p.start and g.end == p.end for g in ov_gold),
            strict_correct=any(compatible(p.native_label, g.label) for g in ov_gold),
            strict_exact=any(g.start == p.start and g.end == p.end and compatible(p.native_label, g.label)
                             for g in ov_gold),
            ignored=(not ov_gold) and ov_ign,
            gold_labels=[g.label for g in ov_gold],
        ))
    return SampleMatch(sample=sample, gold=gms, preds=pms)
