"""Failure attribution: for every gold entity, exactly ONE outcome and the stage that failed.

Decision tree (first matching rule wins; docs/evaluation_methodology.md):

  ASR_destroyed            entity listed as lost by ASR (no span in the transcript)             stage: asr
  success                  detection covers all core chars AND the redacted output exposes none  stage: -
      -> outcome detected_by_{rule|registry|ml|multiple_sources}; sources + sole_catch recorded
  redaction_failure        detection covers all core chars but the OUTPUT still exposes some     stage: redaction
  partial_span             a final detection overlaps the entity but leaves core chars uncovered stage: recognition_boundary
  reconciliation_failure   accepted evidence overlaps it but no final detection does (expect 0)  stage: reconciliation
  policy_rejected          evidence exists but was rejected by policy (entity disabled / allow-listed) stage: policy
  normalization_failure    evidence exists only on a derived (normalised) view and was rejected
                           because its mapping back to the original text was unsafe             stage: normalization
  below_threshold          a live recognizer proposed it below its threshold                    stage: threshold
  disabled_only            only recognizers / models / normalizers switched off in this config saw it stage: configuration
  no_candidate             nothing proposed it                                                  stage: recognition

`policy_rejected` is ranked before `below_threshold` because it is the explicit configuration intent;
`disabled_only` after `below_threshold` because a live near miss is the operative failure.
"""
from __future__ import annotations

from typing import Any, Optional

from harness.phi.contracts import DetectionEvidence, RejectionReason

STAGE = {"ASR_destroyed": "asr", "redaction_failure": "redaction", "partial_span": "recognition_boundary",
         "reconciliation_failure": "reconciliation", "policy_rejected": "policy", "normalization_failure": "normalization",
         "below_threshold": "threshold", "disabled_only": "configuration", "no_candidate": "recognition"}
DISABLED = {RejectionReason.recognizer_disabled, RejectionReason.model_disabled, RejectionReason.normalizer_disabled,
            RejectionReason.shadow_mode, "shadow_mode",
            "recognizer_disabled", "model_disabled", "normalizer_disabled"}
POLICY = {RejectionReason.entity_disabled, RejectionReason.allow_listed, "entity_disabled", "allow_listed"}
APPROX = {RejectionReason.approximate_mapping, "approximate_mapping"}
BELOW = {RejectionReason.below_threshold, "below_threshold"}


def source_group(recognizer_id: str) -> str:
    """Fine-grained evidence source group used in attribution and ablation."""
    if recognizer_id.startswith("model_"):
        return "MODEL:" + recognizer_id.removeprefix("model_")
    if recognizer_id.startswith("registry_"):
        return "REGISTRY"
    if recognizer_id.startswith("conv_"):
        return "CONVERSATIONAL"
    if recognizer_id.startswith("presidio_"):
        return "PRESIDIO"
    return "DETERMINISTIC"


def coarse(group: str) -> str:
    return "ml" if group.startswith("MODEL:") else "registry" if group == "REGISTRY" else "rule"


def _val(x: Any) -> str:
    return str(getattr(x, "value", x))


def classify(start: int, end: int, core_left_det: int, core_left_out: int, overlapping_detection: bool,
             near: list[DetectionEvidence]) -> dict[str, Any]:
    """near = annotated evidence overlapping the entity. Returns outcome, stage, sources, sole_catch, best_rejected."""
    acc = [e for e in near if e.accepted_as_candidate]
    groups = sorted({source_group(e.recognizer_id) for e in acc})
    info: Optional[dict[str, Any]] = None
    if core_left_det == 0 and core_left_out == 0:
        kinds = {coarse(g) for g in groups}
        oc = "detected_by_multiple_sources" if len(kinds) > 1 else (f"detected_by_{kinds.pop()}" if kinds else "detected_by_rule")
        return {"outcome": oc, "stage": None, "sources": groups, "sole_catch": groups[0] if len(groups) == 1 else None,
                "best_rejected": None}
    if core_left_det == 0:
        return {"outcome": "redaction_failure", "stage": STAGE["redaction_failure"], "sources": groups, "sole_catch": None,
                "best_rejected": None}
    if overlapping_detection:
        return {"outcome": "partial_span", "stage": STAGE["partial_span"], "sources": groups, "sole_catch": None,
                "best_rejected": None}
    if acc:
        return {"outcome": "reconciliation_failure", "stage": STAGE["reconciliation_failure"], "sources": groups,
                "sole_catch": None, "best_rejected": None}

    def best(es):
        b = max(es, key=lambda e: e.raw_score)
        return {"recognizer": b.recognizer_id, "group": source_group(b.recognizer_id), "raw_score": round(b.raw_score, 4),
                "score_type": _val(b.score_type), "threshold": b.threshold_used, "threshold_source": b.threshold_source,
                "reason": _val(b.rejection_reason), "view": b.text_view_id}
    for name, kinds in (("policy_rejected", POLICY), ("normalization_failure", APPROX), ("below_threshold", BELOW),
                        ("disabled_only", DISABLED)):
        es = [e for e in near if e.rejection_reason in kinds or _val(e.rejection_reason) in {_val(k) for k in kinds}]
        if es:
            info = best(es)
            return {"outcome": name, "stage": STAGE[name], "sources": [], "sole_catch": None, "best_rejected": info}
    return {"outcome": "no_candidate", "stage": STAGE["no_candidate"], "sources": [], "sole_catch": None, "best_rejected": None}
