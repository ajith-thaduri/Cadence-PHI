"""Normalizers: derived text views for spoken / ASR input.

Contract
  Normalizer.propose(text) -> list[Replacement]      rewrites proposed on the ORIGINAL text
  build_views(text, normalizer_ids) -> (views, traces)
      views[0] is always the identity view "original"; when any normalizer changes the text a
      second view "normalized" holds the combined rewrite plus an exact segment offset map.

Rules
  - DetectionRequest.text is never mutated; a view is a copy.
  - Every rewritten segment maps to an exact original segment (MappingQuality.exact_segment);
    a span that ends inside a rewritten segment snaps OUTWARD to the whole segment, so a
    redaction always covers at least the original characters that produced the hit.
  - Overlapping proposals: the earlier normalizer in NORMALIZERS order wins (documented order).
  - Each normalizer is versioned, independently toggleable and recorded in a NormalizationTrace.

Correspondence with the brief's list:
  spoken digits / spoken phone / spoken ID / spoken ZIP -> spoken_digits (digit-by-digit runs)
  spoken dates / spoken DOB                             -> spoken_dates (ordinals, spoken years)
  spoken address                                        -> spoken_address (two-group house numbers)
  spelled names                                         -> spelled_names
  spoken email                                          -> spoken_email
  ASR cleanup                                           -> asr_cleanup (fillers, stutter repeats)
  Context-dependent interpretation (which digits are a phone vs an MRN, "ends in ...", a spoken
  DOB after "date of birth") is evidence, so it lives in the contextual recognizers, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from harness.phi.contracts import MappingQuality, NormalizationTrace, OffsetSegment, TextView, sha256
from harness.phi.normalizers import spoken as S
from harness.phi.normalizers.spoken import Replacement


@dataclass(frozen=True)
class Normalizer:
    normalizer_id: str
    version: str
    fn: Callable[[str], list[Replacement]]
    mapping_quality: MappingQuality = MappingQuality.exact_segment

    def propose(self, text: str) -> list[Replacement]:
        return [r for r in self.fn(text) if r.end > r.start]


NORMALIZERS: dict[str, Normalizer] = {   # order = priority when proposals overlap
    "spoken_email": Normalizer("spoken_email", "2", S.spoken_email),
    "spelled_names": Normalizer("spelled_names", "1", S.spelled_names),
    "spoken_dates": Normalizer("spoken_dates", "1", S.spoken_dates),
    "spoken_address": Normalizer("spoken_address", "1", S.spoken_address),
    "spoken_digits": Normalizer("spoken_digits", "1", S.spoken_digits),
    "asr_cleanup": Normalizer("asr_cleanup", "1", S.asr_cleanup),
}

IDENTITY_VIEW = "original"


def identity_view(text: str) -> TextView:
    return TextView(view_id=IDENTITY_VIEW, transformation_ids=[], mapping_quality=MappingQuality.identity, text=text,
                    segments=[OffsetSegment(derived_start=0, derived_end=len(text), original_start=0, original_end=len(text))])


def build_views(text: str, normalizer_ids: list[str]) -> tuple[list[TextView], list[NormalizationTrace]]:
    views = [identity_view(text)]
    traces: list[NormalizationTrace] = []
    chosen: list[Replacement] = []
    for nid, norm in NORMALIZERS.items():
        if nid not in normalizer_ids:
            continue
        props = norm.propose(text)
        kept = [r for r in props if not any(r.start < o.end and o.start < r.end for o in chosen)]
        chosen += kept
        traces.append(NormalizationTrace(normalizer_id=nid, version=norm.version, input_sha256=sha256(text),
                                         output_sha256="", transformations=len(kept),
                                         mapping_quality=norm.mapping_quality))
    if not chosen:
        for t in traces:
            t.output_sha256 = t.input_sha256
        return views, traces
    chosen.sort(key=lambda r: r.start)
    parts, segs, pos, npos = [], [], 0, 0
    for r in chosen:
        if r.start > pos:
            chunk = text[pos:r.start]
            parts.append(chunk)
            segs.append(OffsetSegment(derived_start=npos, derived_end=npos + len(chunk), original_start=pos, original_end=r.start))
            npos += len(chunk)
        parts.append(r.value)
        segs.append(OffsetSegment(derived_start=npos, derived_end=npos + len(r.value), original_start=r.start,
                                  original_end=r.end, transformation_id=f"{r.normalizer}_v{NORMALIZERS[r.normalizer].version}"))
        npos += len(r.value)
        pos = r.end
    if pos < len(text):
        segs.append(OffsetSegment(derived_start=npos, derived_end=npos + len(text) - pos, original_start=pos, original_end=len(text)))
        parts.append(text[pos:])
    derived = "".join(parts)
    used = sorted({r.normalizer for r in chosen})
    quality = (MappingQuality.approximate if any(NORMALIZERS[u].mapping_quality == MappingQuality.approximate for u in used)
               else MappingQuality.exact_segment)
    views.append(TextView(view_id="normalized", transformation_ids=[f"{u}_v{NORMALIZERS[u].version}" for u in used],
                          mapping_quality=quality, text=derived, segments=segs))
    out_hash = sha256(derived)
    for t in traces:
        t.output_sha256 = out_hash
    return views, traces


def normalize_text(text: str, normalizer_ids: list[str]) -> TextView:
    """Convenience: the combined derived view (identity view when nothing changes)."""
    views, _ = build_views(text, normalizer_ids)
    return views[-1]
