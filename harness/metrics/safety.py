"""PHI-safety metrics computed on the REDACTED output.

RESIDUAL_PHI_AFTER_SHIELD: for each document, run the detector, apply redaction
(consistent tokenisation), then count gold core PHI characters that are NOT inside a
redacted region of the original text. Reported as a per-document distribution.

Also: over-redaction (non-PHI characters removed), false all-clear (detector redacted
nothing although gold PHI exists), verbatim leaks (gold entity text still present
verbatim in the redacted output).
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from harness.evaluation.matching import core_chars
from harness.schema import Prediction, Sample
from harness.shield.redact import redact


def doc_safety(s: Sample, spans: list[Prediction]) -> dict[str, Any]:
    red = redact(s.text, spans)
    mask = red.redacted_mask
    core_total = residual = 0
    leaked_entities = 0
    verbatim = 0
    for e in s.entities:
        core = core_chars(s.text, e)
        left = sum(1 for i in core if not mask[i])
        core_total += len(core)
        residual += left
        leaked_entities += left > 0
        surface = re.sub(r"\s+", " ", e.text.strip())
        if len(re.sub(r"\W", "", surface)) >= 4 and surface.lower() in red.text.lower():
            verbatim += 1
    phi_or_ignored = set()
    for e in s.entities:
        phi_or_ignored.update(range(e.start, e.end))
    for ig in s.ignore_spans:
        phi_or_ignored.update(range(ig.start, ig.end))
    non_phi = [i for i, c in enumerate(s.text) if not c.isspace() and i not in phi_or_ignored]
    over = sum(1 for i in non_phi if mask[i])
    return {
        "sample_id": s.id, "subset": s.subset, "language": s.language,
        "gold_entities": len(s.entities), "core_chars": core_total, "residual_chars": residual,
        "residual_fraction": (residual / core_total) if core_total else 0.0,
        "leaked_entities": leaked_entities, "verbatim_leaks": verbatim,
        "redacted_spans": len(red.tokens), "over_redacted_chars": over, "non_phi_chars": len(non_phi),
        "sanitized": residual == 0, "false_all_clear": bool(s.entities) and not red.tokens,
    }


def safety_summary(docs: list[dict[str, Any]]) -> dict[str, Any]:
    with_phi = [d for d in docs if d["gold_entities"]]
    fr = np.array([d["residual_fraction"] for d in with_phi]) if with_phi else np.array([0.0])
    worst = max(with_phi, key=lambda d: (d["residual_fraction"], d["residual_chars"]), default=None)
    total_core = sum(d["core_chars"] for d in docs)
    neg = [d for d in docs if not d["gold_entities"]]
    return {
        "RESIDUAL_PHI_AFTER_SHIELD": {
            "mean": float(fr.mean()), "median": float(np.median(fr)), "p95": float(np.percentile(fr, 95)),
            "max": float(fr.max()), "worst_document": worst["sample_id"] if worst else None,
            "worst_document_residual_chars": worst["residual_chars"] if worst else None,
            "corpus_residual_char_rate": (sum(d["residual_chars"] for d in docs) / total_core) if total_core else None,
        },
        "complete_document_sanitization_rate": (sum(d["sanitized"] for d in with_phi) / len(with_phi)) if with_phi else None,
        "documents_with_residual_phi": sum(not d["sanitized"] for d in with_phi),
        "documents_with_phi": len(with_phi),
        "false_all_clear_rate": (sum(d["false_all_clear"] for d in with_phi) / len(with_phi)) if with_phi else None,
        "verbatim_leaked_entities": sum(d["verbatim_leaks"] for d in docs),
        "over_redaction_char_rate": (sum(d["over_redacted_chars"] for d in docs) / max(1, sum(d["non_phi_chars"] for d in docs))),
        "negative_control_documents": len(neg),
        "negative_control_docs_with_any_redaction": sum(d["redacted_spans"] > 0 for d in neg),
        "negative_control_redacted_chars": sum(d["over_redacted_chars"] for d in neg),
    }
