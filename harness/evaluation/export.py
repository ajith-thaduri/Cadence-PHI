"""False-negative / false-positive records."""
from __future__ import annotations

import re
from typing import Any

from harness.evaluation.matching import SampleMatch
from harness.privacy import TextPolicy

_SENT_END = re.compile(r"[.!?\n]")


def context_window(text: str, start: int, end: int, max_side: int = 120) -> str:
    """Surrounding sentence (bounded), with the span marked by >>> <<<."""
    left = max(0, start - max_side)
    for m in _SENT_END.finditer(text, left, start):
        left = m.end()
    right = min(len(text), end + max_side)
    m = _SENT_END.search(text, end, right)
    if m:
        right = m.end()
    return (text[left:start] + ">>>" + text[start:end] + "<<<" + text[end:right]).strip()


def false_negatives(sm: SampleMatch, model_key: str, policy: TextPolicy, *, dataset: str = "",
                    stage: str = "MODEL_ONLY", chunk_bounds=None, compatible=None) -> list[dict[str, Any]]:
    from harness.evaluation.failures import categorise

    out = []
    s = sm.sample
    for g in sm.gold:
        if g.full:
            continue
        e = g.entity
        best = max(g.overlapping, key=lambda p: p.score or 0.0) if g.overlapping else None
        cats, primary = categorise(g, s, chunk_bounds, stage, compatible)
        out.append({
            "model": model_key,
            "stage": stage,
            "dataset": dataset,
            "document_id": s.id,
            "sample_id": s.id,
            "entity_type": e.label,
            "gold_span": [e.start, e.end],
            "coverage": round(g.coverage, 4),
            "primary_failure": primary,
            "failure_categories": cats,
            "gold": policy.text(e.text),
            "label": e.label,
            "start": e.start, "end": e.end,
            "outcome": g.outcome,                       # miss | partial_match (exact can't be FN)
            "char_coverage": round(g.coverage, 4),
            "prediction": policy.text(best.text) if best else None,
            "prediction_span": [best.start, best.end] if best else None,
            "native_label": best.native_label if best else None,
            "canonical_label": best.canonical_label if best else None,
            "confidence": best.score if best else None,
            "context": policy.text(context_window(s.text, e.start, e.end)),
            "subset": s.subset,
            "language": s.language,
            "noise_level": s.metadata.get("noise_level"),
            "difficulty": e.difficulty,
            "entity_tags": e.tags,
            "source": s.source,
        })
    return out


def false_positives(sm: SampleMatch, model_key: str, policy: TextPolicy) -> list[dict[str, Any]]:
    out = []
    s = sm.sample
    for p in sm.preds:
        if p.outcome != "spurious":
            continue
        q = p.prediction
        out.append({
            "model": model_key, "sample_id": s.id, "prediction": policy.text(q.text),
            "start": q.start, "end": q.end, "native_label": q.native_label,
            "canonical_label": q.canonical_label, "confidence": q.score,
            "context": policy.text(context_window(s.text, q.start, q.end)),
            "subset": s.subset, "language": s.language, "noise_level": s.metadata.get("noise_level"),
        })
    return out
