"""Contextual-ML-layer decision experiment (protocol: configs/decision_protocol.yaml).

Steps (each re-runnable; outputs under results/decision_<stamp>/):
  predict   one subprocess per candidate: predictions on every decision dataset at the
            candidate's FROZEN shield threshold (calibrated earlier on dev splits)
  evaluate  single models, then pairwise unions, then (only if the protocol allows) a
            triple and a cascade - all through the identical shield pipeline
"""
from __future__ import annotations

import json
import logging
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

import numpy as np

from harness.config import load_yaml
from harness.datasets import load_dataset
from harness.datasets.base import Dataset
from harness.evaluation.matching import core_chars, match_sample
from harness.evaluation.runner import _as_canonical, canonical_compatible, full_metrics
from harness.metrics.safety import doc_safety, safety_summary
from harness.schema import Prediction
from harness.shield.pipeline import ShieldComponents
from harness.shield.reconcile import reconcile

log = logging.getLogger(__name__)
PROTOCOL = load_yaml("configs/decision_protocol.yaml")
CANDIDATES: list[str] = PROTOCOL["candidates"]
PRIOR_RUN = "results/2026-10-01_192836"
DATASETS = {   # name -> (tier, threshold source pair)
    "cadence_asr": ("TIER 3 - Cadence-realistic, real ASR engine output", "synthetic_test"),
    "cadence_asr_uncorrected": ("TIER 3 - ASR, UNCORRECTED gold (sensitivity check for deviation D1)", "synthetic_test"),
    "cadence_text": ("TIER 3 - Cadence-realistic, clean authored text", "synthetic_test"),
    "meddocan_test": ("TIER 2 - real clinical text, synthetic PHI (Spanish)", "meddocan_test"),
    "synthetic_test": ("TIER 4 - template synthetic benchmark", "synthetic_test"),
}
HIGH_RISK = ["NAME", "PROVIDER_NAME", "FACILITY_NAME", "DOB", "ADDRESS", "PHONE", "EMAIL", "MEMBER_ID", "MRN",
             "ACCOUNT_ID", "NPI", "DATE"]


def frozen_threshold(model: str, pair: str) -> Optional[float]:
    r = json.loads(Path(f"{PRIOR_RUN}/metrics/{model}.json").read_text())
    return r["pairs"][pair]["calibration"]["best_dev_threshold_shield"]


# ---------------------------------------------------------------------------- predict
def predict_model(model: str, out: Path, device: str = "auto") -> None:
    from harness.adapters import build_adapter
    from harness.devices import detect_device

    a = build_adapter(model, detect_device(device))
    if not a.load():
        raise SystemExit(f"{model} failed to load: {a.load_error}")
    res: dict[str, Any] = {"model": model, "datasets": {}}
    for ds_name, (_, pair) in DATASETS.items():
        ds = load_dataset(ds_name)
        thr = frozen_threshold(model, pair)
        preds = {}
        for s in ds.samples:
            raw = a.infer_raw(s.text, None if a.filterable else thr)
            preds[s.id] = [p.model_dump() for p in a.decode(raw, s.text, thr)]
        res["datasets"][ds_name] = {"threshold": thr, "predictions": preds}
        log.info("[%s] %s: %d docs (thr=%s)", model, ds_name, len(ds.samples), thr)
    a.unload()
    (out / f"predictions__{model}.json").write_text(json.dumps(res))


# ---------------------------------------------------------------------------- evaluate
class Evaluator:
    def __init__(self, out: Path):
        self.out = out
        self.preds = {m: json.loads((out / f"predictions__{m}.json").read_text())["datasets"] for m in CANDIDATES}
        self.ds = {n: load_dataset(n) for n in DATASETS}
        self.shield = {n: ShieldComponents() for n in DATASETS}

    def model_spans(self, combo: tuple[str, ...], ds: str, sid: str, only_docs: Optional[set] = None) -> list[Prediction]:
        spans = []
        for i, m in enumerate(combo):
            if i > 0 and only_docs is not None and sid not in only_docs:
                continue   # cascade: secondary models only on triggered documents
            spans += [Prediction(**p) for p in self.preds[m][ds]["predictions"][sid]]
        return _as_canonical(spans)

    def stage_preds(self, combo: tuple[str, ...], ds: str, stage: str, only_docs=None) -> dict[str, list[Prediction]]:
        C = self.shield[ds]
        out = {}
        for s in self.ds[ds].samples:
            mp = self.model_spans(combo, ds, s.id, only_docs) if combo else []
            if stage == "MODEL_ONLY":
                out[s.id] = reconcile(s.text, mp, expand=False)
            elif stage == "DETERMINISTIC_ONLY":
                out[s.id] = _as_canonical(C.stage("DETERMINISTIC_ONLY", s, None))
            elif stage == "DETERMINISTIC_REGISTRY":
                out[s.id] = _as_canonical(C.stage("DETERMINISTIC_REGISTRY", s, None))
            else:
                out[s.id] = _as_canonical(C.stage(stage, s, mp))
        return out

    def score(self, ds: str, preds: dict[str, list[Prediction]], subset=None) -> dict[str, Any]:
        samples = [s for s in self.ds[ds].samples if subset is None or subset(s)]
        sms = [match_sample(s, preds[s.id], canonical_compatible) for s in samples]
        docs = [doc_safety(s, preds[s.id]) for s in samples]
        m = full_metrics(sms)
        saf = safety_summary(docs)
        return {"documents": len(samples), "entities": m["n_gold_entities"],
                "residual_mean": saf["RESIDUAL_PHI_AFTER_SHIELD"]["mean"], "residual_median": saf["RESIDUAL_PHI_AFTER_SHIELD"]["median"],
                "residual_p95": saf["RESIDUAL_PHI_AFTER_SHIELD"]["p95"], "residual_max": saf["RESIDUAL_PHI_AFTER_SHIELD"]["max"],
                "worst_document": saf["RESIDUAL_PHI_AFTER_SHIELD"]["worst_document"],
                "corpus_residual_char_rate": saf["RESIDUAL_PHI_AFTER_SHIELD"]["corpus_residual_char_rate"],
                "zero_residual_docs": saf["documents_with_phi"] - saf["documents_with_residual_phi"],
                "documents_with_phi": saf["documents_with_phi"],
                "sanitization": saf["complete_document_sanitization_rate"], "high_risk_recall": m["high_risk_recall"],
                "entity_recall": m["entity_recall"], "precision": m["detection"]["overlap"]["precision"],
                "spurious": m["false_positives"], "over_redaction": saf["over_redaction_char_rate"],
                "per_category": {c: {"support": v["support"], "recall": v["recall_full"], "fn": v["false_negatives_full"]}
                                 for c, v in m["per_category"].items() if v["support"]},
                "_sms": sms, "_docs": docs}


SLICES = {
    "all": None,
    "english": lambda s: s.language == "en",
    "spanish_or_mixed": lambda s: s.language in ("es", "mixed"),
    "calls": lambda s: s.metadata.get("doc_type") == "call",
}
STAGES_ALL = ["MODEL_ONLY", "DETERMINISTIC_ONLY", "DETERMINISTIC_REGISTRY", "MODEL_DETERMINISTIC",
              "MODEL_DETERMINISTIC_REGISTRY"]


def strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _rel(x, y):
    return (x - y) / x if x else 0.0


def materially_lower(x: float, y: float) -> bool:
    """Is residual y materially lower than residual x? (protocol M1: >=30% relative AND >=0.5 pp absolute)"""
    return _rel(x, y) >= 0.30 and (x - y) >= 0.005


def materiality(a: dict, ab: dict, slices: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """Apply the pre-registered materiality rule (configs/decision_protocol.yaml).
    `slices` maps slice name -> (residual mean with A, residual mean with A+B)."""
    rel = _rel
    res = {}
    res["M1_mean_residual"] = materially_lower(a["residual_mean"], ab["residual_mean"])
    res["M2_tail_residual"] = rel(a["residual_p95"], ab["residual_p95"]) >= 0.30 and (a["residual_p95"] - ab["residual_p95"]) >= 0.02
    m3 = []
    for c in HIGH_RISK:
        ca, cb = a["per_category"].get(c), ab["per_category"].get(c)
        if ca and cb and ca["support"] >= 10 and ca["recall"] < 0.95:
            recovered = (ca["fn"] - cb["fn"]) / ca["fn"] if ca["fn"] else 0
            if recovered >= 0.5 and cb["recall"] >= 0.95:
                m3.append(c)
    res["M3_critical_category"] = m3
    m4 = []
    for name, (x, y) in slices.items():
        if x and y and rel(x, y) >= 0.30 and (x - y) >= 0.01:
            m4.append(name)
    res["M4_slice"] = m4
    benefit = res["M1_mean_residual"] or res["M2_tail_residual"] or bool(m3) or bool(m4)
    guard_over = (ab["over_redaction"] - a["over_redaction"]) <= 0.005
    guard_fp = ab["spurious"] <= a["spurious"] * 1.25 + 1e-9 or ab["spurious"] - a["spurious"] <= 2
    res.update({"benefit": benefit, "guard_over_redaction": guard_over, "guard_false_positives": guard_fp,
                "relative_mean_reduction": rel(a["residual_mean"], ab["residual_mean"]),
                "absolute_mean_reduction": a["residual_mean"] - ab["residual_mean"],
                "relative_p95_reduction": rel(a["residual_p95"], ab["residual_p95"]),
                "passes_before_latency": benefit and guard_over and guard_fp})
    return res


def recovered_by(ev: Evaluator, ds: str, a: str, b: str) -> dict[str, Any]:
    """Entities left by A's full shield that B's addition fully covers (and what it costs)."""
    full_a = ev.stage_preds((a,), ds, "MODEL_DETERMINISTIC_REGISTRY")
    full_ab = ev.stage_preds((a, b), ds, "MODEL_DETERMINISTIC_REGISTRY")
    rows, chars_left, chars_rec = [], 0, 0
    for s in ev.ds[ds].samples:
        ga = match_sample(s, full_a[s.id], canonical_compatible).gold
        gab = match_sample(s, full_ab[s.id], canonical_compatible).gold
        for x, y in zip(ga, gab):
            if x.full:
                continue
            core = len(core_chars(s.text, x.entity))
            left = core - x.covered
            chars_left += left
            gain = y.covered - x.covered
            chars_rec += gain
            rows.append({"doc": s.id, "label": x.entity.label, "text": x.entity.text, "a_cov": round(x.coverage, 2),
                         "ab_cov": round(y.coverage, 2), "recovered_fully": y.full, "tags": x.entity.tags})
    n = len(rows)
    return {"a_misses": n, "recovered_fully": sum(r["recovered_fully"] for r in rows),
            "chars_left_by_a": chars_left, "chars_recovered": chars_rec,
            "share_of_residual_chars_recovered": chars_rec / chars_left if chars_left else 0.0,
            "by_label": _by(rows), "examples": rows[:60]}


def _by(rows):
    d: dict[str, dict] = {}
    for r in rows:
        x = d.setdefault(r["label"], {"misses": 0, "recovered": 0})
        x["misses"] += 1
        x["recovered"] += r["recovered_fully"]
    return d


def run_evaluation(out: Path) -> dict[str, Any]:
    ev = Evaluator(out)
    R: dict[str, Any] = {"protocol_sha256": (Path("configs/decision_protocol.sha256").read_text().split()[0]),
                         "datasets": {n: {"tier": t, **{"documents": len(ev.ds[n].samples),
                                          "entities": sum(len(s.entities) for s in ev.ds[n].samples)}} for n, (t, _) in DATASETS.items()},
                         "thresholds": {m: {n: ev.preds[m][n]["threshold"] for n in DATASETS} for m in CANDIDATES},
                         "single": {}, "baselines": {}, "pairs": {}, "triple": None, "cascade": None}
    # baselines + singles, all stages, all datasets, slices
    for n in DATASETS:
        R["baselines"][n] = {st: strip(ev.score(n, ev.stage_preds((), n, st))) for st in ("DETERMINISTIC_ONLY", "DETERMINISTIC_REGISTRY")}
        for m in CANDIDATES:
            R["single"].setdefault(m, {})[n] = {}
            for st in ("MODEL_ONLY", "MODEL_DETERMINISTIC", "MODEL_DETERMINISTIC_REGISTRY"):
                p = ev.stage_preds((m,), n, st)
                R["single"][m][n][st] = {sl: strip(ev.score(n, p, fn)) for sl, fn in SLICES.items()
                                         if fn is None or any(fn(s) for s in ev.ds[n].samples)}
    # primary model per protocol (decision dataset: cadence_asr, full shield)
    D, FULL = "cadence_asr", "MODEL_DETERMINISTIC_REGISTRY"
    full = {m: R["single"][m][D][FULL]["all"] for m in CANDIDATES}
    R["primary_selection"] = select_primary(R, full)
    A = R["primary_selection"]["primary"]
    # pairwise complementarity (A + each other candidate), plus all pairs for transparency
    for b in [m for m in CANDIDATES if m != A]:
        key = f"{A}+{b}"
        entry = {"datasets": {}}
        for n in DATASETS:
            entry["datasets"][n] = {}
            for st in ("MODEL_ONLY", "MODEL_DETERMINISTIC", FULL):
                p = ev.stage_preds((A, b), n, st)
                entry["datasets"][n][st] = {sl: strip(ev.score(n, p, fn)) for sl, fn in SLICES.items()
                                            if fn is None or any(fn(s) for s in ev.ds[n].samples)}
            entry["datasets"][n]["recovered"] = recovered_by(ev, n, A, b)
        a_full, ab_full = R["single"][A][D][FULL], entry["datasets"][D][FULL]
        slices = {sl: (a_full[sl]["residual_mean"], ab_full[sl]["residual_mean"]) for sl in ("calls", "spanish_or_mixed") if sl in a_full}
        entry["materiality"] = materiality(a_full["all"], ab_full["all"], slices)
        # replication: direction must not reverse on clean text or tier 2
        entry["replication"] = {n: entry["datasets"][n][FULL]["all"]["residual_mean"] <= R["single"][A][n][FULL]["all"]["residual_mean"] + 1e-9
                                for n in ("cadence_text", "meddocan_test")}
        R["pairs"][key] = entry
    log.info("pairs evaluated")
    passing = [k for k, v in R["pairs"].items() if v["materiality"]["passes_before_latency"] and all(v["replication"].values())]
    R["passing_pairs"] = passing
    if passing:     # protocol: triple and cascade only when a pair passes
        best = min(passing, key=lambda k: R["pairs"][k]["datasets"][D][FULL]["all"]["residual_mean"])
        R["cascade"] = evaluate_cascade(ev, A, best.split("+")[1], R)
        if len(CANDIDATES) == 3:
            p = ev.stage_preds(tuple(CANDIDATES), D, FULL)
            tri = strip(ev.score(D, p))
            R["triple"] = {"vs_pair": best, "full_shield": tri,
                           "materiality": materiality(R["pairs"][best]["datasets"][D][FULL]["all"], tri, {})}
    return R


def select_primary(R, full: dict[str, dict]) -> dict[str, Any]:
    """Primary-model rule from configs/decision_protocol.yaml.

    Deviation D2 (documented in the report): each tie-breaker decides only when it shows a
    MATERIAL difference (same materiality notion as the main rule); otherwise the next
    tie-breaker is applied. Without this, near-ceiling P95 noise (89.3% vs 90.2%) would
    override the primary metric. The literal (unguarded) outcome is also recorded.
    """
    from harness.reporting.analysis import evidence_tier

    ranked = sorted(full, key=lambda m: full[m]["residual_mean"])
    best = ranked[0]
    tied = [m for m in ranked[1:] if not materially_lower(full[m]["residual_mean"], full[best]["residual_mean"])
            ]
    pool = [best] + tied
    veto_ok = [m for m in pool if (full[m]["high_risk_recall"] or 0) >= 0.95]
    steps = [f"tied on mean residual (no material difference): {pool}"]
    if veto_ok:
        pool = veto_ok
        steps.append(f"veto (high-risk recall >= 95%): {pool}")
    else:
        steps.append("veto: no candidate reaches 95% high-risk recall - veto cannot discriminate")
    literal = sorted(pool, key=lambda m: full[m]["residual_p95"])[0]
    # TB1 P95 (material only)
    bp = min(full[m]["residual_p95"] for m in pool)
    keep = [m for m in pool if not (_rel(full[m]["residual_p95"], bp) >= 0.30 and full[m]["residual_p95"] - bp >= 0.02)]
    steps.append(f"TB1 P95 residual {{{', '.join(f'{m}: {full[m]['residual_p95']:.3f}' for m in pool)}}} -> {keep}")
    pool = keep
    # TB2 contradiction on other tiers
    if len(pool) > 1:
        out = set()
        for n in ("cadence_text", "meddocan_test"):
            v = {m: R["single"][m][n]["MODEL_DETERMINISTIC_REGISTRY"]["all"]["residual_mean"] for m in pool}
            for m in pool:
                if any(materially_lower(v[m], v[o]) for o in pool if o != m):
                    out.add(m)
                    steps.append(f"TB2 {m} materially worse on {n} ({v[m]:.4f} vs best {min(v.values()):.4f})")
        pool = [m for m in pool if m not in out] or pool
    # TB3 published real-clinical evidence tier
    if len(pool) > 1:
        tiers = {m: evidence_tier(m) for m in pool}
        bt = min(tiers.values())
        pool = [m for m in pool if tiers[m] == bt]
        steps.append(f"TB3 published evidence tiers {tiers} -> {pool}")
    # TB4 cost (CPU P95 latency)
    if len(pool) > 1:
        def cpu(m):
            p = Path(f"{PRIOR_RUN}/latency/{m}__cpu.json")
            return json.loads(p.read_text())["single_doc"]["p95_ms"] if p.exists() else 1e9
        pool = sorted(pool, key=cpu)[:1]
        steps.append(f"TB4 CPU P95 -> {pool}")
    primary = pool[0]
    contradicted = []
    for n in ("cadence_text", "meddocan_test"):
        vals = {m: R["single"][m][n]["MODEL_DETERMINISTIC_REGISTRY"]["all"]["residual_mean"] for m in full}
        for m, v in vals.items():
            if m != primary and materially_lower(vals[primary], v):
                contradicted.append({"dataset": n, "materially_better": m})
    return {"ranking_by_mean_residual": ranked, "tied_with_best": tied, "primary": primary, "steps": steps,
            "literal_unguarded_tiebreak_primary": literal, "contradictions": contradicted, "notes": steps,
            "after_veto": veto_ok or [best] + tied,
            "values": {m: {k: full[m][k] for k in ("residual_mean", "residual_p95", "high_risk_recall", "sanitization")} for m in full}}


def evaluate_cascade(ev: Evaluator, A: str, B: str, R) -> dict[str, Any]:
    """Run B only on documents meeting a simple trigger; compare to always-on pair."""
    D, FULL = "cadence_asr", "MODEL_DETERMINISTIC_REGISTRY"
    triggers = {
        "spoken_or_spanish": lambda s, ps: s.metadata.get("doc_type") in ("call", "dictation") or s.language != "en",
        "low_confidence_primary": lambda s, ps: any((p.score or 1.0) < 0.6 for p in ps),
    }
    a_full = R["single"][A][D][FULL]["all"]["residual_mean"]
    pair_full = R["pairs"][f"{A}+{B}"]["datasets"][D][FULL]["all"]["residual_mean"]
    out = {}
    for name, trig in triggers.items():
        docs = {s.id for s in ev.ds[D].samples
                if trig(s, [Prediction(**p) for p in ev.preds[A][D]["predictions"][s.id]])}
        sc = strip(ev.score(D, ev.stage_preds((A, B), D, FULL, only_docs=docs)))
        kept = (a_full - sc["residual_mean"]) / (a_full - pair_full) if a_full != pair_full else 1.0
        out[name] = {"documents_invoking_B": len(docs), "share_invoking_B": len(docs) / len(ev.ds[D].samples),
                     "residual_mean": sc["residual_mean"], "share_of_pair_benefit_kept": kept,
                     "adopt": kept >= 0.8 and len(docs) / len(ev.ds[D].samples) <= 0.5}
    return out
