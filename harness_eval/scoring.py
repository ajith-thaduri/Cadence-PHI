"""Evaluation scorer (SCORE mode): DetectionResponse + RedactionResponse + GoldDocument -> metrics.

The production engine never sees gold. Evaluation runs the SAME engine:
  prepare()  verify the dataset manifest (hash + role) -> requests + gold + scoped registry store ->
             engine.collect(include_disabled=True) once per request (superset of evidence). Model
             inference is cached by (model, revision, floor, text sha256).
  score()    applies ONE configuration through the engine's policy stage, REDACTS with the engine's
             redaction module, and judges detection and redaction independently against gold.

Three views of the same predictions (configs/eval/metrics.yaml):
  leak      character- and token-level residual PHI measured on the REDACTED OUTPUT
  entity    relaxed (any overlap) / full-span / strict (exact span) recall, precision, per entity
  document  fully sanitized documents (no direct-tier residual) and any-PHI sanitized
Small-sample honesty: cluster-bootstrap CIs over documents, Wilson intervals per entity, the rule of
three for zero-miss categories, and an insufficient-evidence flag below the minimum support.
Every gold entity gets one outcome + failed stage (harness_eval/attribution.py).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from harness.config import load_yaml
from harness.evaluation.runner import canonical_compatible
from harness.paths import ROOT
from harness.phi.catalog import Catalog
from harness.phi.config import HarnessConfig
from harness.phi.contracts import (DecisionValue, Detection, DetectionEvidence, DetectionRequest, EntityOutcome,
                                   EvaluationRecord, GoldDocument, Operator, RedactionRequest)
from harness.phi.engine import Collected, PHIEngine
from harness.phi.policy import annotate, decide
from harness.phi.recognizers.base import RawHit
from harness.phi.redaction import redact as do_redact
from harness_eval import stats
from harness_eval.attribution import classify, source_group
from harness_eval.datasets import load
from harness_eval.redaction_check import check as redaction_check

log = logging.getLogger(__name__)
CACHE = ROOT / "results" / "phi" / "model_cache"
GRID = [round(float(x), 2) for x in np.arange(0.05, 1.0, 0.05)]
_TOKEN = re.compile(r"[A-Za-z0-9À-ɏ]+")


def metrics_config() -> dict[str, Any]:
    p = ROOT / "configs" / "eval" / "metrics.yaml"
    return (load_yaml(str(p)) or {}) if p.exists() else {}


def high_risk(cfg: HarnessConfig) -> list[str]:
    return Catalog(cfg).high_risk()


@dataclass
class Bundle:
    name: str
    data_source: str
    requests: list[DetectionRequest]
    golds: dict[str, GoldDocument]
    collected: dict[str, Collected]
    engine: PHIEngine
    manifest: Optional[dict[str, Any]] = None


def _val(x: Any) -> str:
    return str(getattr(x, "value", x))


# ---------------------------------------------------------------------------- model cache
def _cache_path(ds: str, model: str, cfg: HarnessConfig) -> Path:
    from harness.config import model_entry
    h = hashlib.sha256()
    for p in sorted((ROOT / "harness" / "adapters").glob("*.py")):
        h.update(p.read_bytes())
    h.update(json.dumps([model, model_entry(model).get("revision"), cfg.raw["models"][model].get("candidate_floor", 0.1)]).encode())
    return CACHE / f"{ds}__{model}__{h.hexdigest()[:12]}.json"


def _load_cache(path: Path) -> dict[str, list[RawHit]]:
    from harness.phi.contracts import ScoreType
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {k: [RawHit(h["entity_type"], h["start"], h["end"], h["raw_score"], ScoreType(h["score_type"]), h.get("rule_id"),
                       h.get("metadata") or {}) for h in v] for k, v in raw.items()}


def _save_cache(path: Path, cache: dict[str, list[RawHit]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)   # local only: offsets + scores, keyed by text hash
    path.write_text(json.dumps({k: [{"entity_type": h.entity_type, "start": h.start, "end": h.end, "raw_score": h.raw_score,
                                     "score_type": _val(h.score_type), "rule_id": h.rule_id, "metadata": h.metadata}
                                    for h in v] for k, v in cache.items()}))


def prepare(ds_name: str, cfg: HarnessConfig, device: str = "cpu", refresh: bool = False,
            verify_manifest: bool = True) -> Bundle:
    """Verify the dataset against its signed manifest, then collect superset evidence. Only documents in
    the configured languages are evaluated (this phase: English); exclusions are logged, never silent."""
    man = None
    if verify_manifest:
        from harness_eval import manifests
        try:
            man = manifests.verify(ds_name)
        except manifests.ManifestError as exc:
            raise SystemExit(f"dataset refused: {exc}") from None
    total = len(load(ds_name)[0])
    reqs, golds, store, ds = load(ds_name, languages=cfg.supported_languages)
    if len(reqs) != total:
        log.info("[%s] %d of %d documents in scope (languages=%s); %d out-of-scope documents excluded",
                 ds_name, len(reqs), total, cfg.supported_languages, total - len(reqs))
    caches = {m: ({} if refresh else _load_cache(_cache_path(ds_name, m, cfg))) for m in cfg.raw["models"]}
    sizes = {m: len(c) for m, c in caches.items()}
    # evaluation engines do not serve traffic: the startup self-test is exercised by the T5/T6 suites instead
    eng = PHIEngine(cfg, device=device, registry_store=store, load_all_models=True, model_caches=caches, lazy_models=True,
                    self_test=False)
    col = {}
    for i, r in enumerate(reqs):
        col[r.request_id] = eng.collect(r, include_disabled=True)
        if (i + 1) % 25 == 0:
            log.info("[%s] collected %d/%d", ds_name, i + 1, len(reqs))
    for m, c in caches.items():
        if len(c) != sizes[m]:
            _save_cache(_cache_path(ds_name, m, cfg), c)
    return Bundle(ds_name, ds.source if hasattr(ds, "source") else "unknown", reqs, {g.request_id: g for g in golds}, col,
                  eng, man)


# ---------------------------------------------------------------------------- helpers
def _ov(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def core_chars(text: str, start: int, end: int, cat: Catalog, entity: str) -> set[int]:
    """Core characters of a gold span: alphanumeric tokens minus a leading title (catalog `strip_titles`)
    and ID affix words without digits (catalog `strip_affixes`). Same definition as the legacy scorer."""
    info = cat.get(entity)
    titles = set(info.titles) if info else set()
    affixes = set(info.affixes) if info else set()
    out: set[int] = set()
    first = True
    for m in _TOKEN.finditer(text[start:end]):
        tok = m.group().lower()
        if first and tok in titles:
            first = False
            continue
        first = False
        if tok in affixes and not any(c.isdigit() for c in tok):
            continue
        out.update(range(start + m.start(), start + m.end()))
    if not out:
        out = {i for i in range(start, end) if not text[i].isspace()}
    return out


def _norm_span(text: str, s: int, e: int, titles: set[str]) -> tuple[int, int]:
    while s < e and not text[s].isalnum():
        s += 1
    while e > s and not text[e - 1].isalnum():
        e -= 1
    m = re.match(r"([A-Za-z]+)\.?\s+", text[s:e])
    if m and m.group(1).lower() in titles:
        s += m.end()
    return s, e


def run_policy(b: Bundle, req: DetectionRequest, cfg: HarnessConfig) -> tuple[list[DetectionEvidence], list[Detection]]:
    """The engine's own policy stage on pre-collected evidence (identical to engine.analyze)."""
    col = b.collected[req.request_id]
    ann = annotate(col.evidence, cfg, req, cfg.normalizers_for(req.normalization_profile))
    dets, _ = decide(req.request_id, req.text, ann, cfg, req.policy_profile)
    return ann, dets


def _length_bucket(n: int, edges: list[int]) -> str:
    for e in edges:
        if n <= e:
            return f"<={e}"
    return f">{edges[-1]}"


# ---------------------------------------------------------------------------- scoring
def score(b: Bundle, cfg: HarnessConfig, keep: bool = True, redact: bool = True) -> dict[str, Any]:
    """keep=False skips per-document records and CIs (fast path for sweeps / ablations).
    redact=False judges detection coverage only (used inside calibration sweeps)."""
    cat = Catalog(cfg)
    mcfg = metrics_config()
    st_cfg = mcfg.get("statistics") or {}
    edges = mcfg.get("length_buckets") or [500, 1500, 4000]
    HR = set(cat.high_risk())
    direct = set(cat.by_tier("direct"))
    E = defaultdict(lambda: defaultdict(float))
    S = defaultdict(lambda: defaultdict(list))
    records, docs, fps, resid_rows, outcomes_all = [], [], [], [], []
    red_checks: list[dict[str, Any]] = []
    for req in b.requests:
        gold = b.golds[req.request_id]
        text = req.text
        ann, dets = run_policy(b, req, cfg)
        pol = cfg.policy(req.policy_profile)
        red = [d for d in dets if d.decision == DecisionValue.REDACT or
               (d.decision == DecisionValue.REVIEW and pol.get("redact_review", True))]
        det_cov: set[int] = set()
        for d in red:
            det_cov.update(range(d.original_start, d.original_end))
        ents = [g for g in gold.entities if not g.ignore_region]
        ign = [g for g in gold.entities if g.ignore_region]
        mnr = [tuple(x) for x in gold.metadata.get("must_not_redact", [])]
        if redact:
            rr, vault = do_redact(RedactionRequest(request_id=req.request_id, original_text=text, detections=dets,
                                                   operator=Operator.TOKENIZE, redact_review=pol.get("redact_review", True)))
            rc = redaction_check(text, rr, [(g.annotation_id, g.start, g.end) for g in ents], mnr, vault)
            out_cov = rc.covered
            red_checks.append(rc.as_dict())
        else:
            rc, out_cov = None, det_cov
        outs, core_total, resid_total, direct_resid = [], 0, 0, 0
        tok_total = tok_leak = 0
        for g in ents:
            core = core_chars(text, g.start, g.end, cat, g.entity_type)
            left_det = sum(1 for i in core if i not in det_cov)
            left_out = sum(1 for i in core if i not in out_cov)
            ov = [d for d in red if _ov(d.original_start, d.original_end, g.start, g.end)]
            near = [e for e in ann if _ov(e.original_start, e.original_end, g.start, g.end)]
            c = classify(g.start, g.end, left_det, left_out, bool(ov), near)
            titles = set(cat[g.entity_type].titles) if g.entity_type in cat else set()
            strict = any(_norm_span(text, d.original_start, d.original_end, titles) == _norm_span(text, g.start, g.end, titles)
                         for d in ov)
            for m in _TOKEN.finditer(text[g.start:g.end]):
                rng = range(g.start + m.start(), g.start + m.end())
                if any(i in core for i in rng):
                    tok_total += 1
                    tok_leak += any(i not in out_cov for i in rng if i in core)
            e = E[g.entity_type]
            e["support"] += 1
            e["core_chars"] += len(core)
            e["residual_chars"] += left_out
            e["tp_gold"] += bool(ov)
            e["full"] += left_out == 0
            e["strict"] += strict
            e["type_correct"] += any(canonical_compatible(d.entity_type, g.entity_type) for d in ov)
            if ov:
                S[g.entity_type]["tp"].append(max(d.decision_score for d in ov))
            elif c["best_rejected"] and c["best_rejected"].get("reason") == "below_threshold":
                S[g.entity_type]["fn"].append(c["best_rejected"]["raw_score"])
            core_total += len(core)
            resid_total += left_out
            if g.entity_type in direct:
                direct_resid += left_out
            det_status = "detected_full" if left_det == 0 else ("detected_partial" if ov else "not_detected")
            red_status = ("redacted_ok" if left_out == 0 and c["outcome"] != "redaction_failure" else "redaction_failure") \
                if left_det == 0 else "n/a"
            o = EntityOutcome(annotation_id=g.annotation_id, entity_type=g.entity_type, start=g.start, end=g.end,
                              outcome=c["outcome"], core_chars=len(core), residual_chars=left_out, best_rejected=c["best_rejected"],
                              failed_stage=c["stage"], detection_status=det_status, redaction_status=red_status,
                              sources=c["sources"], sole_catch=c["sole_catch"], subtype=g.subtype)
            outs.append(o)
            if left_out or c["outcome"] == "redaction_failure":
                resid_rows.append({"dataset": b.name, "request_id": req.request_id, "doc": gold.metadata.get("doc_id"),
                                   "entity": g.entity_type, "subtype": g.subtype, "outcome": c["outcome"], "stage": c["stage"],
                                   "core_chars": len(core), "residual_chars": left_out, "start": g.start, "end": g.end,
                                   "language": gold.metadata.get("language"), "doc_type": gold.metadata.get("doc_type"),
                                   "modality": _val(req.modality), "asr_altered": "asr_altered" in g.tags,
                                   **{f"best_rejected_{k}": v for k, v in (c["best_rejected"] or {}).items()}})
        if rc is not None:
            missed_surfaces = {re.sub(r"[\W_]+", "", text[o.start:o.end].lower()) for o in outs if o.residual_chars}
            unexplained = [g.annotation_id for g in ents if g.annotation_id in (rc.verbatim_leaks + rc.normalized_leaks)
                           and re.sub(r"[\W_]+", "", text[g.start:g.end].lower()) not in missed_surfaces]
            red_checks[-1]["surface_leak_unexplained"] = len(unexplained)   # surface visible via a non-gold mention
        for lost in gold.asr_lost:
            outs.append(EntityOutcome(annotation_id=f"{gold.metadata.get('doc_id')}#asr_{lost.get('kind', 'lost')}",
                                      entity_type=lost["entity_type"], start=None, end=None, outcome="ASR_destroyed",
                                      core_chars=0, residual_chars=0, failed_stage="asr", detection_status="asr_destroyed"))
        nfp = 0
        for d in red:
            hit = any(_ov(d.original_start, d.original_end, g.start, g.end) for g in ents)
            e = E[d.entity_type]
            e["pred"] += 1
            if hit:
                e["tp_pred"] += 1
            elif not any(_ov(d.original_start, d.original_end, g.start, g.end) for g in ign):
                e["fp"] += 1
                nfp += 1
                S[d.entity_type]["fp"].append(d.decision_score)
                fps.append({"dataset": b.name, "request_id": req.request_id, "doc": gold.metadata.get("doc_id"),
                            "entity": d.entity_type, "start": d.original_start, "end": d.original_end,
                            "decision_score": d.decision_score, "confidence_class": _val(d.confidence_class),
                            "recognizers": "+".join(d.contributing_recognizers),
                            "source_groups": "+".join(sorted({source_group(r) for r in d.contributing_recognizers})),
                            "reason": d.decision_reason})
        phi_or_ign = set()
        for g in gold.entities:
            phi_or_ign.update(range(g.start, g.end))
        non_phi = [i for i, ch in enumerate(text) if not ch.isspace() and i not in phi_or_ign]
        over = sum(1 for i in non_phi if i in out_cov)
        frac = resid_total / core_total if core_total else 0.0
        if keep:
            records.append(EvaluationRecord(
                request_id=req.request_id, dataset=b.name, response_ref=req.request_id, core_chars=core_total,
                residual_chars=resid_total, residual_fraction=frac,
                tp_entities=sum(1 for o in outs if o.outcome.startswith("detected") or o.outcome == "partial_span"),
                fn_entities=sum(1 for o in outs if not (o.outcome.startswith("detected") or o.outcome in ("partial_span", "ASR_destroyed"))),
                fp_detections=nfp, over_redacted_chars=over, sanitized=resid_total == 0, outcomes=outs))
        outcomes_all += outs
        docs.append({"dataset": b.name, "request_id": req.request_id, "doc": gold.metadata.get("doc_id"),
                     "language": gold.metadata.get("language"), "doc_type": gold.metadata.get("doc_type"),
                     "modality": _val(req.modality), "length_bucket": _length_bucket(len(text), edges),
                     "normalization_profile": req.normalization_profile, "gold_entities": len(ents),
                     "full_entities": sum(1 for o in outs if o.residual_chars == 0 and o.outcome != "ASR_destroyed"
                                          and o.outcome != "redaction_failure"),
                     "hr_entities": sum(1 for g in ents if g.entity_type in HR),
                     "hr_full": sum(1 for o in outs if o.entity_type in HR and o.start is not None and o.residual_chars == 0
                                    and o.outcome != "redaction_failure"),
                     "core_chars": core_total, "residual_chars": resid_total, "residual_fraction": frac,
                     "direct_residual_chars": direct_resid, "tokens": tok_total, "tokens_leaked": tok_leak,
                     "redacted_spans": len(red), "fp_spans": nfp, "over_redacted_chars": over, "non_phi_chars": len(non_phi),
                     "sanitized": (resid_total == 0) if ents else None, "direct_sanitized": (direct_resid == 0) if ents else None,
                     "false_all_clear": bool(ents) and not red, "asr_lost_entities": len(gold.asr_lost),
                     **({"redaction_" + k: v for k, v in red_checks[-1].items()} if redact and red_checks else {})})
    summary = _summary(docs, E, HR, st_cfg, with_ci=keep)
    if redact:
        summary["redaction_checks"] = {k: sum(int(r[k] or 0) for r in red_checks if r.get(k) is not None)
                                       for k in ("verbatim_leaks", "normalized_leaks", "output_slice_errors",
                                                 "must_not_redact_violations", "derived_tokens", "surface_leak_unexplained")}
        rt = [r["roundtrip_ok"] for r in red_checks if r.get("roundtrip_ok") is not None]
        summary["redaction_checks"]["roundtrip_failures"] = sum(1 for x in rt if not x)
    summary["strata"] = _strata(docs) if keep else {}
    return {"dataset": b.name, "data_source": b.data_source, "documents": len(b.requests), "summary": summary,
            "entity_rows": _entity_rows(b.name, E, S, cfg, cat, st_cfg), "document_rows": docs, "fp_rows": fps,
            "residual_rows": resid_rows, "records": records, "outcomes": outcomes_all, "manifest": b.manifest}


def _ratio(docs, num, den):
    n = sum(d[num] for d in docs)
    m = sum(d[den] for d in docs)
    return n / m if m else None


def _summary(docs: list[dict], E, HR: set[str], st_cfg: dict, with_ci: bool) -> dict[str, Any]:
    wp = [d for d in docs if d["gold_entities"]]
    fr = np.array([d["residual_fraction"] for d in wp]) if wp else np.array([0.0])
    core = sum(d["core_chars"] for d in docs)
    tp, fp = sum(e["tp_pred"] for e in E.values()), sum(e["fp"] for e in E.values())
    allsup = sum(e["support"] for e in E.values())
    out = {"RESIDUAL_PHI_AFTER_SHIELD": {"mean": float(fr.mean()), "median": float(np.median(fr)),
                                          "p95": float(np.percentile(fr, 95)), "max": float(fr.max()),
                                          "corpus_residual_char_rate": sum(d["residual_chars"] for d in docs) / core if core else None},
           "token_leak_rate": _ratio(docs, "tokens_leaked", "tokens"),
           "complete_document_sanitization_rate": sum(1 for d in wp if d["sanitized"]) / len(wp) if wp else None,
           "direct_identifier_document_sanitization_rate": sum(1 for d in wp if d["direct_sanitized"]) / len(wp) if wp else None,
           "false_all_clear_rate": sum(d["false_all_clear"] for d in wp) / len(wp) if wp else None,
           "over_redaction_char_rate": sum(d["over_redacted_chars"] for d in docs) / max(1, sum(d["non_phi_chars"] for d in docs)),
           "high_risk_full_recall": _ratio(docs, "hr_full", "hr_entities"),
           "entity_full_recall": _ratio(docs, "full_entities", "gold_entities"),
           "entity_any_overlap_recall": sum(e["tp_gold"] for e in E.values()) / allsup if allsup else None,
           "entity_strict_recall": sum(e["strict"] for e in E.values()) / allsup if allsup else None,
           "span_precision": tp / (tp + fp) if tp + fp else None, "spurious_spans": int(fp),
           "asr_lost_entities": sum(d["asr_lost_entities"] for d in docs), "high_risk_entities": sorted(HR)}
    if with_ci and wp:
        reps, seed = int(st_cfg.get("bootstrap_replicates", 1000)), int(st_cfg.get("seed", 13))
        ci = lambda f: stats.cluster_bootstrap(wp, f, reps, seed)
        out["ci95"] = {
            "residual_mean": ci(lambda ds: float(np.mean([d["residual_fraction"] for d in ds]))),
            "corpus_residual_char_rate": ci(lambda ds: _ratio(ds, "residual_chars", "core_chars")),
            "entity_full_recall": ci(lambda ds: _ratio(ds, "full_entities", "gold_entities")),
            "high_risk_full_recall": ci(lambda ds: _ratio(ds, "hr_full", "hr_entities")),
            "token_leak_rate": ci(lambda ds: _ratio(ds, "tokens_leaked", "tokens")),
            "direct_identifier_document_sanitization_rate": ci(lambda ds: float(np.mean([bool(d["direct_sanitized"]) for d in ds]))),
            "method": f"cluster bootstrap over documents, {reps} replicates, percentile 95%"}
    return out


def _strata(docs: list[dict]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("modality", "doc_type", "language", "length_bucket"):
        groups = defaultdict(list)
        for d in docs:
            if d["gold_entities"]:
                groups[str(d[key])].append(d)
        out[key] = {k: {"documents": len(v), "residual_mean": float(np.mean([d["residual_fraction"] for d in v])),
                        "entity_full_recall": _ratio(v, "full_entities", "gold_entities"),
                        "high_risk_full_recall": _ratio(v, "hr_full", "hr_entities")} for k, v in sorted(groups.items())}
    return out


def _entity_rows(ds: str, E, S, cfg: HarnessConfig, cat: Catalog, st_cfg: dict) -> list[dict[str, Any]]:
    rows = []
    min_sup = int(st_cfg.get("min_support_default", 300))
    for lab in sorted(E, key=lambda l: cat.ids.index(l) if l in cat else 99):
        e, sc = E[lab], S[lab]
        sup = int(e["support"])
        prec = e["tp_pred"] / (e["tp_pred"] + e["fp"]) if (e["tp_pred"] + e["fp"]) else None
        rec = e["tp_gold"] / sup if sup else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
        full = e["full"] / sup if sup else None
        lo, hi = stats.wilson(e["full"], sup) if sup else (None, None)
        misses = sup - int(e["full"])
        thr = {m: cfg.threshold_for(lab, f"model_{m}", m, "model_confidence") for m in cfg.enabled_models}
        info = cat.get(lab)
        rows.append({"dataset": ds, "entity": lab, "enabled": info.enabled if info else None,
                     "risk_tier": info.risk_tier if info else None, "gate_group": info.gate_group if info else None,
                     "support": sup, "tp": int(e["tp_gold"]), "fn": int(sup - e["tp_gold"]), "fp": int(e["fp"]),
                     "predicted_spans": int(e["pred"]), "precision": prec, "recall": rec, "f1": f1,
                     "full_span_recall": full, "full_span_recall_ci_low": lo, "full_span_recall_ci_high": hi,
                     "miss_rate_upper_95_rule_of_three": stats.rule_of_three(sup) if sup and misses == 0 else None,
                     "strict_recall": e["strict"] / sup if sup else None,
                     "type_correct_recall": e["type_correct"] / sup if sup else None,
                     "char_recall": 1 - e["residual_chars"] / e["core_chars"] if e["core_chars"] else None,
                     "residual_chars": int(e["residual_chars"]), "core_chars": int(e["core_chars"]),
                     "insufficient_evidence": sup < min_sup,
                     "mean_score_tp": float(np.mean(sc["tp"])) if sc["tp"] else None,
                     "mean_score_fp": float(np.mean(sc["fp"])) if sc["fp"] else None,
                     "mean_best_rejected_score_fn": float(np.mean(sc["fn"])) if sc["fn"] else None,
                     "model_thresholds": json.dumps({m: t for m, (t, _) in thr.items()}),
                     "model_threshold_sources": json.dumps({m: s for m, (_, s) in thr.items()})})
    return rows


def breakdown(res: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    rows = res["residual_rows"]
    total = sum(r["residual_chars"] for r in rows)
    for key in ("entity", "outcome", "stage", "language", "doc_type", "modality", "asr_altered"):
        agg = defaultdict(int)
        for r in rows:
            agg[str(r.get(key))] += r["residual_chars"]
        out[key] = {k: {"residual_chars": v, "share": v / total if total else 0.0} for k, v in sorted(agg.items(), key=lambda kv: -kv[1])}
    oc = defaultdict(lambda: defaultdict(int))
    src = defaultdict(lambda: defaultdict(int))
    for o in res["outcomes"]:
        oc[o.entity_type][o.outcome] += 1
        oc["*ALL*"][o.outcome] += 1
        for g in o.sources:
            src[o.entity_type][g] += 1
            src["*ALL*"][g] += 1
        if o.sole_catch:
            src[o.entity_type][f"sole:{o.sole_catch}"] += 1
            src["*ALL*"][f"sole:{o.sole_catch}"] += 1
    out["attribution"] = {k: dict(v) for k, v in oc.items()}
    out["sources"] = {k: dict(v) for k, v in src.items()}
    return out


# ---------------------------------------------------------------------------- threshold curves
def threshold_curves(b: Bundle, cfg: HarnessConfig, models: Optional[list[str]] = None,
                     grid: list[float] = GRID, entities: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """Per model x entity: move ONE threshold (thresholds.model_entity) with everything else fixed."""
    models = models or cfg.enabled_models
    rows = []
    for m in models:
        rid = f"model_{m}"
        ev_ents = {e.entity_type for c in b.collected.values() for e in c.evidence if e.recognizer_id == rid}
        gold_ents = {g.entity_type for gd in b.golds.values() for g in gd.entities if not g.ignore_region}
        for ent in sorted((ev_ents & gold_ents) if entities is None else set(entities)):
            for t in grid:
                c = cfg.copy()
                c.set_value(f"thresholds.model_entity.{m}.{ent}", t)
                res = score(b, c, keep=False, redact=False)
                er = next((r for r in res["entity_rows"] if r["entity"] == ent), None)
                rows.append({"dataset": b.name, "model": m, "entity": ent, "threshold": t,
                             "support": er["support"] if er else 0, "recall": er["recall"] if er else None,
                             "full_span_recall": er["full_span_recall"] if er else None,
                             "precision": er["precision"] if er else None, "fp": er["fp"] if er else 0,
                             "fn": er["fn"] if er else 0, "residual_chars": er["residual_chars"] if er else 0,
                             "core_chars": er["core_chars"] if er else 0,
                             "doc_residual_mean": res["summary"]["RESIDUAL_PHI_AFTER_SHIELD"]["mean"],
                             "spurious_total": res["summary"]["spurious_spans"]})
    return rows


# ---------------------------------------------------------------------------- outputs
def show_text(b: Bundle, cfg: HarnessConfig) -> bool:
    mode = str(cfg.raw.get("output", {}).get("show_text", "auto")).lower()
    if mode in ("true", "always"):
        return True
    if mode in ("false", "never"):
        return False
    phi_class = (b.manifest or {}).get("phi_class")
    if phi_class:
        return phi_class == "fabricated"
    return b.data_source in ("synthetic", "realistic")      # fabricated PHI only; real/public text is masked


def csv_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r)) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        if not keys:
            return
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})


def thresholds_table(cfg: HarnessConfig, results: list[dict]) -> list[dict]:
    rows = []
    for r in results:
        for er in r["entity_rows"]:
            for m in cfg.enabled_models:
                t, src = cfg.threshold_for(er["entity"], f"model_{m}", m, "model_confidence")
                rows.append({"dataset": r["dataset"], "model": m, "entity": er["entity"], "threshold": t, "threshold_source": src,
                             "support": er["support"], "recall": er["recall"], "full_span_recall": er["full_span_recall"],
                             "precision": er["precision"], "residual_chars": er["residual_chars"], "fp": er["fp"], "fn": er["fn"]})
    return rows


def write_outputs(out: Path, bundles: list[Bundle], results: list[dict], cfg: HarnessConfig,
                  curves: Optional[list[dict]] = None, meta: Optional[dict] = None, command: str = "evaluate") -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"config": cfg.describe(), "config_path": cfg.path, **(meta or {}), "datasets": {}}
    for b, r in zip(bundles, results):
        summary["datasets"][r["dataset"]] = {"documents": r["documents"], "data_source": r["data_source"],
                                             "manifest": r.get("manifest"), "text_in_outputs": show_text(b, cfg),
                                             **r["summary"], "breakdown": breakdown(r)}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    csv_rows(out / "entity_metrics.csv", [x for r in results for x in r["entity_rows"]])
    csv_rows(out / "document_metrics.csv", [x for r in results for x in r["document_rows"]])
    csv_rows(out / "false_positives.csv", [x for r in results for x in r["fp_rows"]])
    csv_rows(out / "false_negatives.csv", [x for r in results for x in r["residual_rows"] if x["outcome"] != "partial_span"])
    csv_rows(out / "residual_phi.csv", [x for r in results for x in r["residual_rows"]])
    csv_rows(out / "thresholds.csv", thresholds_table(cfg, results))
    csv_rows(out / "attribution.csv", [{"dataset": r["dataset"], "entity": e, **v}
                                       for r in results for e, v in breakdown(r)["attribution"].items()])
    csv_rows(out / "source_attribution.csv", [{"dataset": r["dataset"], "entity": e, **v}
                                              for r in results for e, v in breakdown(r)["sources"].items()])
    if curves is not None:
        csv_rows(out / "threshold_curves.csv", curves)
    with open(out / "predictions.jsonl", "w", encoding="utf-8") as fp, \
         open(out / "detections.jsonl", "w", encoding="utf-8") as fd, \
         open(out / "evidence.jsonl", "w", encoding="utf-8") as fe, \
         open(out / "evaluation_records.jsonl", "w", encoding="utf-8") as fr:
        for b, r in zip(bundles, results):
            st = show_text(b, cfg)
            for req in b.requests:
                q = req.model_copy(update={"options": req.options.model_copy(update={"include_candidates": True, "include_text": st})})
                resp = b.engine.analyze(q, cfg, b.collected[req.request_id])
                fp.write(resp.model_dump_json() + "\n")
                for d in resp.detections:
                    fd.write(json.dumps({"dataset": b.name, "request_id": req.request_id, **d.model_dump(mode="json")},
                                        ensure_ascii=False) + "\n")
                for e in resp.evidence:
                    fe.write(json.dumps({"dataset": b.name, "request_id": req.request_id, **e.model_dump(mode="json")},
                                        ensure_ascii=False) + "\n")
            for rec in r["records"]:
                fr.write(rec.model_dump_json() + "\n")
    from harness_eval import ledger
    from harness_eval.report import write_reports
    write_reports(out, summary, results, curves)
    run_id = ledger.record(command, cfg, [r.get("manifest") or {"id": r["dataset"], "role": "unregistered"} for r in results],
                           {r["dataset"]: {"residual_mean": round(r["summary"]["RESIDUAL_PHI_AFTER_SHIELD"]["mean"], 4),
                                           "high_risk_full_recall": r["summary"]["high_risk_full_recall"],
                                           "span_precision": r["summary"]["span_precision"]} for r in results}, str(out))
    summary["ledger_run_id"] = run_id
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
