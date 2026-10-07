"""Why was PHI missed? Programmatic failure categorisation of every false negative.

Categories (all that apply are recorded; `primary_failure` uses the precedence below):
  chunk_boundary_failure    entity straddles a chunk boundary of the inference windows
  long_document_failure     entity in a LONG_DOCUMENTS sample
  registry_matching_failure registry stage ran, entity value is in the registry, still missed
  asr_failure               ASR-style entity (spoken numbers, ASR noise, accent variation)
  spanish_failure           Spanish or code-switched document
  identifier_format_failure structured identifier/date in a non-canonical format
  context_failure           contextual mention (first/last name only, relative, nickname...)
  wrong_entity_boundary     detected, but only part of the span
  wrong_entity_type         overlapping predictions all carry an incompatible type
  model_missed_entity       nothing overlapped the entity
"""
from __future__ import annotations

from typing import Optional

from harness.evaluation.matching import GoldMatch
from harness.schema import Sample
from harness.shield.registry import build_terms, norm

PRECEDENCE = ["chunk_boundary_failure", "long_document_failure", "registry_matching_failure", "asr_failure",
              "identifier_format_failure", "context_failure", "spanish_failure", "wrong_entity_boundary",
              "wrong_entity_type", "model_missed_entity"]
STRUCTURED = {"PHONE", "EMAIL", "URL", "MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "OTHER_UNIQUE_ID", "ZIP", "DATE", "DOB"}
FORMAT_TAGS = {"natural_format", "stylized_format", "spoken_number", "unformatted_number", "malformed_date",
               "partial_date", "relative_date", "structured", "insurance_id", "bare_weekday"}
CONTEXT_TAGS = {"contextual", "first_name_only", "last_name_only", "nickname", "relative", "partial_name",
                "provider_facility_ambiguity", "intersection", "partial_address", "out_of_registry"}
ASR_TAGS = {"asr", "asr_noise", "spoken_number", "accent_variation"}


def _in_registry(s: Sample, text: str) -> bool:
    reg = s.metadata.get("registry")
    if not reg:
        return False
    t = norm(text)
    toks = set(t.split())
    for seq, _, _, _ in build_terms(reg):
        if " ".join(seq) == t or (len(seq) == 1 and seq[0] in toks):
            return True
    digits = "".join(c for c in text if c.isdigit())
    return bool(digits) and digits in str(reg)


def categorise(g: GoldMatch, s: Sample, chunk_bounds: Optional[list[int]] = None, stage: str = "MODEL_ONLY",
               compatible=None) -> tuple[list[str], str]:
    e = g.entity
    tags = set(e.tags)
    cats: list[str] = []
    if chunk_bounds and any(e.start < b < e.end for b in chunk_bounds):
        cats.append("chunk_boundary_failure")
    if s.subset == "LONG_DOCUMENTS":
        cats.append("long_document_failure")
    if "REGISTRY" in stage and _in_registry(s, e.text):
        cats.append("registry_matching_failure")
    if tags & ASR_TAGS or s.metadata.get("noise_level") in ("medium", "high"):
        cats.append("asr_failure")
    if s.language in ("es", "mixed"):
        cats.append("spanish_failure")
    if e.label in STRUCTURED:
        cats.append("identifier_format_failure")
    if tags & CONTEXT_TAGS:
        cats.append("context_failure")
    if g.overlap:
        cats.append("wrong_entity_boundary")
        if compatible and g.overlapping and not any(compatible(p.native_label, e.label) for p in g.overlapping):
            cats.append("wrong_entity_type")
    else:
        cats.append("model_missed_entity")
    primary = next(c for c in PRECEDENCE if c in cats)
    return cats, primary
