"""Threshold sweep + dev-set threshold selection.

The selected value is called "best threshold on dev" - never "optimal". It is chosen on
the dev split only and then applied unchanged to test.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


def sweep(thresholds: list[Optional[float]], evaluate: Callable[[Optional[float]], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for t in thresholds:
        m = evaluate(t)
        d = m["detection"]["full_coverage"]
        rows.append({
            "threshold": t,
            "precision": d["precision"], "recall_full": d["recall"], "f1": d["f1"], "f2": d["f2"],
            "recall_overlap": m["detection"]["overlap"]["recall"],
            "high_risk_recall": m["high_risk_recall"],
            "complete_document_sanitization_rate": m["complete_document_sanitization_rate"],
            "residual_phi_character_rate": m["residual_phi_character_rate"],
            "false_positives": m["false_positives"], "false_negatives": m["false_negatives"],
            "n_predictions": m["n_predictions"],
        })
    return rows


def select_threshold(rows: list[dict[str, Any]], criterion: str = "f2") -> Optional[dict[str, Any]]:
    """Max F2 (recall-weighted). Ties -> lower threshold (favours recall)."""
    cands = [r for r in rows if r["threshold"] is not None and r[criterion] is not None]
    if not cands:
        return None
    return sorted(cands, key=lambda r: (-round(r[criterion], 6), r["threshold"]))[0]
