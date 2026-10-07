"""Automatic CRITICAL / WARNING flags.

These are INTERNAL ENGINEERING BENCHMARK GATES on the supplied benchmark. They are not
claims about legal HIPAA compliance or de-identification certification.
"""
from __future__ import annotations

import operator
from typing import Any

from harness.taxonomy import HIGH_RISK

_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _get(metrics: dict[str, Any], dotted: str):
    cur: Any = metrics
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def evaluate_flags(metrics: dict[str, Any], gates: list[dict[str, Any]], *, spanish_recall,
                   truncation_detected: bool, coverage: dict[str, str]) -> list[dict[str, Any]]:
    flags = []
    hr_crit = False
    for g in gates:
        v = _get(metrics, g["metric"])
        if v is None:
            flags.append({"severity": "WARNING", "id": g["id"],
                          "message": f"{g['metric']} unavailable (no support in dataset)"})
            continue
        if _OPS[g["op"]](v, g["value"]):
            if g["id"] == "high_risk_recall_warning" and hr_crit:
                continue   # already CRITICAL
            if g["id"] == "high_risk_recall_critical":
                hr_crit = True
            flags.append({"severity": g["severity"], "id": g["id"],
                          "message": f"{g['metric']} = {v:.2%} {g['op']} {g['value']:.0%}"})
    if spanish_recall is None:
        flags.append({"severity": "WARNING", "id": "spanish_unavailable", "message": "Spanish recall unavailable"})
    if truncation_detected:
        flags.append({"severity": "WARNING", "id": "long_document_truncation",
                      "message": "long-document truncation detected when inputs exceed the model window without chunking"})
    uncovered = [c for c in HIGH_RISK if coverage.get(c) in (None, "none")]
    if uncovered:
        flags.append({"severity": "WARNING", "id": "unmapped_phi_categories",
                      "message": "high-risk categories with no native label: " + ", ".join(uncovered)})
    zero_shot = [c for c in HIGH_RISK if coverage.get(c) == "zero_shot"]
    if zero_shot:
        flags.append({"severity": "WARNING", "id": "zero_shot_labels",
                      "message": "labels requested zero-shot (not documented as trained): " + ", ".join(zero_shot)})
    return flags
