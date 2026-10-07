"""Metric aggregation over matched samples.

Primary safety metrics are LABEL-AGNOSTIC: for redaction, a name tagged as the wrong
PHI type is still removed. Label-strict ("typed") metrics are reported separately.

Unless stated otherwise, "recall" below = full-coverage recall: the fraction of gold
entities whose every core character is covered by some prediction.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from harness.evaluation.matching import GoldMatch, SampleMatch
from harness.taxonomy import CANONICAL_LABELS, CATEGORY_GROUPS, HIGH_RISK


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def _f(p: float | None, r: float | None, beta: float = 1.0) -> float | None:
    if p is None or r is None:
        return None
    b2 = beta * beta
    return (1 + b2) * p * r / (b2 * p + r) if (b2 * p + r) else 0.0


def gold_block(gms: list[GoldMatch], min_partial: float = 0.5) -> dict[str, Any]:
    """Recall-side statistics for any group of gold entities."""
    n = len(gms)
    core = sum(g.core_size for g in gms)
    cov = sum(g.covered for g in gms)
    return {
        "support": n,
        "recall_full": _div(sum(g.full for g in gms), n),
        "recall_partial": _div(sum(g.partial(min_partial) for g in gms), n),
        "recall_overlap": _div(sum(g.overlap for g in gms), n),
        "recall_lenient": _div(sum(g.lenient for g in gms), n),
        "recall_exact": _div(sum(g.exact for g in gms), n),
        "recall_strict_overlap": _div(sum(g.strict_overlap for g in gms), n),
        "recall_strict_full": _div(sum(g.strict_covered == g.core_size for g in gms), n),
        "char_coverage": _div(cov, core),
        "residual_char_rate": _div(core - cov, core),
        "false_negatives_full": sum(not g.full for g in gms),
        "complete_misses": sum(not g.overlap for g in gms),
        "core_chars": core,
    }


def document_block(sms: list[SampleMatch]) -> dict[str, Any]:
    docs = [s for s in sms if s.gold]
    n = len(docs)
    sanitized = sum(all(g.full for g in s.gold) for s in docs)
    one_plus_missed = sum(any(not g.overlap for g in s.gold) for s in docs)
    critical = sum(any((not g.overlap) and g.entity.label in HIGH_RISK for g in s.gold) for s in docs)
    return {
        "documents_with_phi": n,
        "documents_without_phi": len(sms) - n,
        "complete_document_sanitization_rate": _div(sanitized, n),
        "any_residual_phi_rate": _div(n - sanitized, n),
        "one_plus_missed_entity_rate": _div(one_plus_missed, n),
        "critical_miss_rate": _div(critical, n),
        "sanitized_documents": sanitized,
    }


def compute_metrics(sms: list[SampleMatch], min_partial: float = 0.5) -> dict[str, Any]:
    golds = [g for s in sms for g in s.gold]
    preds = [p for s in sms for p in s.preds if not p.ignored]
    n_pred = len(preds)
    overall = gold_block(golds, min_partial)

    # ---- detection (label-agnostic) P/R/F1 ------------------------------------------
    tp_ov = sum(p.overlaps_gold for p in preds)
    tp_ex = sum(p.exact_gold for p in preds)
    det = {}
    for mode, rec, prec in [
        ("overlap", overall["recall_overlap"], _div(tp_ov, n_pred)),
        ("full_coverage", overall["recall_full"], _div(tp_ov, n_pred)),
        ("exact", overall["recall_exact"], _div(tp_ex, n_pred)),
    ]:
        det[mode] = {"precision": prec, "recall": rec, "f1": _f(prec, rec), "f2": _f(prec, rec, 2.0)}

    # ---- typed (label-strict) per category -------------------------------------------
    per_cat: dict[str, dict[str, Any]] = {}
    by_label: dict[str, list[GoldMatch]] = defaultdict(list)
    for g in golds:
        by_label[g.entity.label].append(g)
    preds_by_canon: dict[str, list] = defaultdict(list)
    for p in preds:
        preds_by_canon[p.prediction.canonical_label].append(p)
    for cat in CANONICAL_LABELS:
        gb = gold_block(by_label.get(cat, []), min_partial)
        cp = preds_by_canon.get(cat, [])
        prec = _div(sum(p.strict_correct for p in cp), len(cp))
        rec = gb["recall_strict_overlap"]
        gb.update({
            "typed_precision": prec, "typed_recall": rec, "typed_f1": _f(prec, rec),
            "predictions": len(cp),
            "false_positives_typed": sum(not p.strict_correct for p in cp),
            "false_positives_spurious": sum(not p.overlaps_gold for p in cp),
        })
        per_cat[cat] = gb

    present = [c for c in CANONICAL_LABELS if per_cat[c]["support"] > 0]
    macro = {}
    for k_out, k_in in [("precision", "typed_precision"), ("recall", "typed_recall"), ("f1", "typed_f1")]:
        vals = [per_cat[c][k_in] if per_cat[c][k_in] is not None else 0.0 for c in present]
        macro[k_out] = sum(vals) / len(vals) if vals else None
    macro_full = [per_cat[c]["recall_full"] for c in present]
    macro["recall_full_label_agnostic"] = sum(macro_full) / len(macro_full) if macro_full else None

    typed_micro_p = _div(sum(p.strict_correct for p in preds), n_pred)
    typed_micro_r = overall["recall_strict_overlap"]
    typed_exact_p = _div(sum(p.strict_exact for p in preds), n_pred)

    hr = gold_block([g for g in golds if g.entity.label in HIGH_RISK], min_partial)
    groups = {name: gold_block([g for g in golds if g.entity.label in cats], min_partial)
              for name, cats in CATEGORY_GROUPS.items()}

    outcomes = {
        "gold_exact_match": sum(g.outcome == "exact_match" for g in golds),
        "gold_partial_match": sum(g.outcome == "partial_match" for g in golds),
        "gold_miss": sum(g.outcome == "miss" for g in golds),
        "pred_exact_match": sum(p.outcome == "exact_match" for p in preds),
        "pred_partial_match": sum(p.outcome == "partial_match" for p in preds),
        "pred_spurious": sum(p.outcome == "spurious" for p in preds),
        "pred_ignored": sum(1 for s in sms for p in s.preds if p.ignored),
    }

    return {
        "n_documents": len(sms),
        "n_gold_entities": len(golds),
        "n_predictions": n_pred,
        "entity_recall": overall["recall_full"],
        "entity_recall_overlap": overall["recall_overlap"],
        "char_recall": overall["char_coverage"],
        "high_risk_recall": hr["recall_full"],
        "high_risk_false_negative_rate": None if hr["recall_full"] is None else 1 - hr["recall_full"],
        "high_risk_recall_overlap": hr["recall_overlap"],
        "high_risk": hr,
        "residual_phi_character_rate": overall["residual_char_rate"],
        "residual_phi_entity_rate": None if overall["recall_full"] is None else 1 - overall["recall_full"],
        "false_negatives": overall["false_negatives_full"],
        "false_positives": outcomes["pred_spurious"],
        **document_block(sms),
        "overall": overall,
        "detection": det,
        "typed_micro": {"precision": typed_micro_p, "recall": typed_micro_r,
                        "f1": _f(typed_micro_p, typed_micro_r),
                        "exact_precision": typed_exact_p,
                        "exact_recall": _div(sum(g.strict_exact for g in golds), len(golds))},
        "typed_macro": macro,
        "per_category": per_cat,
        "category_groups": groups,
        "category_recall": {c: per_cat[c]["recall_full"] for c in present},
        "outcomes": outcomes,
    }


def breakdown(sms: list[SampleMatch], key_fn, min_partial: float = 0.5) -> dict[str, dict[str, Any]]:
    """Recall-side metrics grouped by a sample-level key (subset, language, noise...)."""
    groups: dict[str, list[SampleMatch]] = defaultdict(list)
    for s in sms:
        for k in _as_iter(key_fn(s)):
            groups[k].append(s)
    out = {}
    for k, ss in sorted(groups.items()):
        gb = gold_block([g for s in ss for g in s.gold], min_partial)
        gb.update(document_block(ss))
        gb["false_positives"] = sum(p.outcome == "spurious" for s in ss for p in s.preds)
        gb["n_documents"] = len(ss)
        out[k] = gb
    return out


def entity_breakdown(sms: list[SampleMatch], key_fn, min_partial: float = 0.5) -> dict[str, dict[str, Any]]:
    """Recall-side metrics grouped by an entity-level key (difficulty, tags...)."""
    groups: dict[str, list[GoldMatch]] = defaultdict(list)
    for s in sms:
        for g in s.gold:
            for k in _as_iter(key_fn(s, g)):
                groups[k].append(g)
    return {k: gold_block(v, min_partial) for k, v in sorted(groups.items())}


def _as_iter(v) -> Iterable[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v]
    return [str(v)]
