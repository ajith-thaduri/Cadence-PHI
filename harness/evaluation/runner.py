"""Evaluation engine (v2): one model across all evaluation pairs, every pipeline stage.

Evaluation pairs keep calibration and test strictly separate:
  synthetic_dev  -> synthetic_test   (disjoint template families)
  meddocan_dev   -> meddocan_test    (official MEDDOCAN split)
Thresholds are chosen on dev only (max F2, recall-weighted) - separately for MODEL_ONLY
and for the full shield - and applied unchanged to test.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from harness.adapters import BaseModelAdapter, RawOutput, build_adapter
from harness.config import benchmark_config
from harness.datasets.base import Dataset
from harness.evaluation.boundary import run_boundary_test
from harness.evaluation.export import false_negatives, false_positives
from harness.evaluation.matching import DEFAULT_TITLES, SampleMatch, match_sample
from harness.evaluation.streaming import run_streaming_test
from harness.metrics.core import breakdown, compute_metrics, entity_breakdown, gold_block
from harness.metrics.flags import evaluate_flags
from harness.metrics.safety import doc_safety, safety_summary
from harness.metrics.sweep import sweep
from harness.privacy import TextPolicy
from harness.schema import Prediction
from harness.shield.pipeline import MODEL_FREE, STAGES, ShieldComponents

log = logging.getLogger(__name__)

GROUPS = [{"NAME", "RELATIVE_NAME", "PROVIDER_NAME", "CARE_TEAM_MEMBER"},
          {"ADDRESS", "CITY", "STATE", "ZIP", "GEOGRAPHIC_CONTEXT"}, {"DATE", "DOB"},
          {"MRN", "MEMBER_ID", "ACCOUNT_ID", "NPI", "OTHER_UNIQUE_ID"}, {"PHONE", "EMAIL", "URL"},
          {"FACILITY_NAME", "EMPLOYER", "ORGANIZATION"}, {"LANGUAGE"}]
_GROUP_OF = {c: frozenset(g) for g in GROUPS for c in g}
EVAL_PAIRS = [("synthetic_dev", "synthetic_test"), ("meddocan_dev", "meddocan_test")]
TOKEN_LEVEL_SWEEP = [0.1, 0.2, 0.3, 0.5, 0.7]


def canonical_compatible(pred_canonical: str, gold: str) -> bool:
    return gold in _GROUP_OF.get(pred_canonical, frozenset({pred_canonical}))


def _as_canonical(preds: list[Prediction]) -> list[Prediction]:
    return [p.model_copy(update={"native_label": p.canonical_label}) for p in preds]


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


def _titles() -> set[str]:
    return set(benchmark_config()["matching"].get("title_tokens", DEFAULT_TITLES))


def full_metrics(sms: list[SampleMatch]) -> dict[str, Any]:
    mp = float(benchmark_config()["matching"]["partial_min_gold_coverage"])
    m = compute_metrics(sms, mp)
    m["by_subset"] = breakdown(sms, lambda s: s.sample.subset, mp)
    m["by_language"] = breakdown(sms, lambda s: s.sample.language, mp)
    m["by_noise_level"] = breakdown(sms, lambda s: s.sample.metadata.get("noise_level"), mp)
    m["by_phenomenon"] = breakdown(sms, lambda s: s.sample.metadata.get("phenomena"), mp)
    m["by_difficulty"] = entity_breakdown(sms, lambda s, g: g.entity.difficulty, mp)
    m["by_entity_tag"] = entity_breakdown(sms, lambda s, g: g.entity.tags, mp)
    m["by_subset_category"] = {
        sub: entity_breakdown([x for x in sms if x.sample.subset == sub], lambda s, g: g.entity.label, mp)
        for sub in sorted({x.sample.subset for x in sms})}
    return m


def evaluate_spans(ds: Dataset, preds: dict[str, list[Prediction]], compat) -> tuple[list[SampleMatch], dict, dict]:
    t = _titles()
    sms = [match_sample(s, preds.get(s.id, []), compat, t) for s in ds.samples]
    docs = [doc_safety(s, preds.get(s.id, [])) for s in ds.samples]
    return sms, full_metrics(sms), {**safety_summary(docs), "per_document": docs}


def _f2(sms: list[SampleMatch]) -> float:
    m = compute_metrics(sms)
    return m["detection"]["full_coverage"]["f2"] or 0.0


class ModelRun:
    """Caches raw inference per (dataset, threshold) for one loaded adapter."""

    def __init__(self, adapter: BaseModelAdapter) -> None:
        self.a = adapter
        self.raw: dict[tuple[str, Optional[float]], dict[str, RawOutput]] = {}
        self.secs: dict[str, float] = {}

    def raws(self, ds: Dataset, thr: Optional[float]) -> dict[str, RawOutput]:
        key = (ds.name, None if self.a.filterable else thr)
        if key not in self.raw:
            t0 = time.perf_counter()
            out = {}
            for i, s in enumerate(ds.samples):
                out[s.id] = self.a.infer_raw(s.text, key[1])
                if (i + 1) % 50 == 0:
                    log.info("  %s %s thr=%s: %d/%d", self.a.key, ds.name, key[1], i + 1, len(ds.samples))
            self.raw[key] = out
            self.secs[f"{ds.name}@{key[1]}"] = time.perf_counter() - t0
        return self.raw[key]

    def preds(self, ds: Dataset, thr: Optional[float]) -> dict[str, list[Prediction]]:
        r = self.raws(ds, thr)
        return {s.id: self.a.decode(r[s.id], s.text, thr) for s in ds.samples}


def _thresholds(a: BaseModelAdapter) -> list[Optional[float]]:
    base = [float(t) for t in benchmark_config()["threshold_sweep"]] if a.filterable else TOKEN_LEVEL_SWEEP
    ts: list[Optional[float]] = sorted(set(base) | ({a.default_threshold} if a.default_threshold is not None else set()))
    return ([None] if a.default_threshold is None else []) + ts


def _pick(rows: list[tuple[Optional[float], float]]) -> Optional[float]:
    """Max F2; ties -> lower threshold (more recall); None (argmax) ranks as its own candidate."""
    return sorted(rows, key=lambda r: (-round(r[1], 6), -1 if r[0] is None else r[0]))[0][0]


def evaluate_pair(run: ModelRun, dev: Dataset, test: Dataset, shield: dict[str, ShieldComponents],
                  run_dir: Path, policy: TextPolicy) -> dict[str, Any]:
    a = run.a
    compat = a.mapping.compatible
    ts = _thresholds(a)
    Cd, Ct = shield[dev.name], shield[test.name]
    # ---- calibration on dev -------------------------------------------------------------
    dev_rows = []
    for t in ts:
        p = run.preds(dev, t)
        sm_model = [match_sample(s, p[s.id], compat, _titles()) for s in dev.samples]
        full = {s.id: _as_canonical(Cd.stage("MODEL_DETERMINISTIC_REGISTRY", s, p[s.id])) for s in dev.samples}
        sm_full = [match_sample(s, full[s.id], canonical_compatible, _titles()) for s in dev.samples]
        mm, mf = compute_metrics(sm_model), compute_metrics(sm_full)
        dev_rows.append({"threshold": t, "model_only_f2": mm["detection"]["full_coverage"]["f2"] or 0.0,
                         "model_only_recall": mm["entity_recall"], "model_only_precision": mm["detection"]["overlap"]["precision"],
                         "shield_f2": mf["detection"]["full_coverage"]["f2"] or 0.0, "shield_recall": mf["entity_recall"],
                         "shield_precision": mf["detection"]["overlap"]["precision"]})
    best_model = _pick([(r["threshold"], r["model_only_f2"]) for r in dev_rows])
    best_shield = _pick([(r["threshold"], r["shield_f2"]) for r in dev_rows])
    # ---- test --------------------------------------------------------------------------
    default = a.default_threshold
    out: dict[str, Any] = {"dev": dev.name, "test": test.name, "thresholds_considered": ts,
                           "calibration": {"criterion": "max F2 (full-coverage, label-agnostic) on dev",
                                           "default_threshold": default, "best_dev_threshold_model_only": best_model,
                                           "best_dev_threshold_shield": best_shield, "dev_sweep": dev_rows}}
    p_def = run.preds(test, default)
    sms, m, saf = evaluate_spans(test, p_def, compat)
    out["model_only_default"] = {"threshold": default, "metrics": m, "safety": {k: v for k, v in saf.items() if k != "per_document"}}
    if best_model != default:
        _, m2, saf2 = evaluate_spans(test, run.preds(test, best_model), compat)
        out["model_only_dev_threshold"] = {"threshold": best_model, "metrics": m2,
                                           "safety": {k: v for k, v in saf2.items() if k != "per_document"}}
    else:
        out["model_only_dev_threshold"] = {**out["model_only_default"], "same_as_default": True}
    # pipeline stages at the shield-calibrated threshold (model-free stages are reported separately)
    p_sh = run.preds(test, best_shield)
    stages = {}
    stage_docs = {}
    for st in STAGES:
        if st in MODEL_FREE:
            continue
        preds = {s.id: (p_sh[s.id] if st == "MODEL_ONLY" else _as_canonical(Ct.stage(st, s, p_sh[s.id])))
                 for s in test.samples}
        cmp = compat if st == "MODEL_ONLY" else canonical_compatible
        sm_st, m_st, saf_st = evaluate_spans(test, preds, cmp)
        stages[st] = {"threshold": best_shield, "metrics": m_st,
                      "safety": {k: v for k, v in saf_st.items() if k != "per_document"}}
        stage_docs[st] = saf_st["per_document"]
        if st == "MODEL_DETERMINISTIC_REGISTRY":
            raws = run.raws(test, best_shield)
            fns = [r for sm in sm_st for r in false_negatives(sm, a.key, policy, dataset=test.name, stage=st,
                                                               chunk_bounds=raws[sm.sample.id].truncation.chunk_char_bounds,
                                                               compatible=cmp)]
            _jsonl(run_dir / "false_negatives" / f"{a.key}__{test.name}__full_shield.jsonl", fns)
            out["full_shield_failure_counts"] = _count([r["primary_failure"] for r in fns])
    out["stages"] = stages
    _dump(run_dir / "raw" / f"{a.key}__{test.name}__per_document_safety.json", stage_docs)
    # FN / FP export for MODEL_ONLY at the default operating point
    raws = run.raws(test, default)
    fns = [r for sm in sms for r in false_negatives(sm, a.key, policy, dataset=test.name, stage="MODEL_ONLY",
                                                     chunk_bounds=raws[sm.sample.id].truncation.chunk_char_bounds,
                                                     compatible=compat)]
    fps = [r for sm in sms for r in false_positives(sm, a.key, policy)]
    _jsonl(run_dir / "false_negatives" / f"{a.key}__{test.name}.jsonl", fns)
    _jsonl(run_dir / "false_positives" / f"{a.key}__{test.name}.jsonl", fps)
    _jsonl(run_dir / "predictions" / f"{a.key}__{test.name}.jsonl",
           [{"sample_id": s.id, "predictions": [{**p.model_dump(), "text": policy.text(p.text)} for p in p_def[s.id]]}
            for s in test.samples])
    out["model_only_failure_counts"] = _count([r["primary_failure"] for r in fns])
    out["model_only_failure_categories_all"] = _count([c for r in fns for c in r["failure_categories"]])
    # threshold sweep on test (reported only; never used for selection)
    if a.filterable:
        out["test_sweep"] = sweep(ts, lambda t: compute_metrics(
            [match_sample(s, pp, compat, _titles()) for s, pp in zip(test.samples, run.preds(test, t).values())]))
    else:
        out["test_sweep"] = None
        out["test_sweep_note"] = "token-level model: test sweep not computed (requires re-inference per threshold)"
    return out


def _count(xs: list[str]) -> dict[str, int]:
    d: dict[str, int] = {}
    for x in xs:
        d[x] = d.get(x, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def long_document_analysis(run: ModelRun, test: Dataset) -> list[dict[str, Any]]:
    a = run.a
    samples = [s for s in test.samples if s.subset == "LONG_DOCUMENTS"]
    thr = a.default_threshold
    rows = []
    original = a.long_text_strategy
    try:
        for strat in ("author", "production", "naive", "truncate"):
            a.long_text_strategy = strat
            for s in samples:
                raw = a.infer_raw(s.text, None if a.filterable else thr)
                preds = a.decode(raw, s.text, thr)
                sm = match_sample(s, preds, a.mapping.compatible, _titles())
                tr = raw.truncation
                gb = gold_block(sm.gold)
                rows.append({"sample_id": s.id, "target_tokens": s.metadata.get("target_tokens"), "strategy": strat,
                             "max_supported_input": tr.max_supported, "unit": tr.unit, "actual_length": tr.input_length,
                             "truncated": tr.truncated, "visible_char_end": tr.visible_char_end,
                             "entities": len(s.entities),
                             "entities_beyond_truncation": sum(1 for e in s.entities if tr.visible_char_end is not None and e.start >= tr.visible_char_end),
                             "entities_split_by_chunker": sum(1 for e in s.entities if any(e.start < b < e.end for b in tr.chunk_char_bounds)),
                             "chunks": tr.num_chunks, "recall_full": gb["recall_full"], "char_coverage": gb["char_coverage"]})
    finally:
        a.long_text_strategy = original
    return rows


def evaluate_model(key: str, device: str, datasets: dict[str, Dataset], shield: dict[str, ShieldComponents],
                   run_dir: Path, show_public_text: bool = False) -> dict[str, Any]:
    adapter = build_adapter(key, device)
    log.info("[%s] loading %s @ %s on %s", key, adapter.entry["model_id"], adapter.entry["revision"][:7], device)
    adapter.load()
    meta = adapter.get_metadata()
    meta["coverage"] = adapter.mapping.coverage()
    meta["unmapped_native_labels"] = adapter.mapping.unmapped_native_labels()
    summary: dict[str, Any] = {"model": key, "status": meta["status"], "metadata": meta, "pairs": {}}
    _dump(run_dir / "models" / f"{key}.json", {**meta, "load_traceback": adapter.load_traceback})
    if adapter.status.value != "LOADED":
        summary["error"] = adapter.load_error
        _dump(run_dir / "metrics" / f"{key}.json", summary)
        return summary
    run = ModelRun(adapter)
    t_start = time.perf_counter()
    try:
        for dev_name, test_name in EVAL_PAIRS:
            if dev_name not in datasets or test_name not in datasets:
                summary["pairs"][test_name] = {"status": "NOT RUN — DATASET UNAVAILABLE"}
                continue
            test = datasets[test_name]
            policy = TextPolicy(test.is_synthetic or test.name.startswith("meddocan"), show_public_text)
            log.info("[%s] %s -> %s", key, dev_name, test_name)
            summary["pairs"][test_name] = evaluate_pair(run, datasets[dev_name], test, shield, run_dir, policy)
        if "synthetic_test" in datasets:
            log.info("[%s] long-document strategies", key)
            summary["long_documents"] = long_document_analysis(run, datasets["synthetic_test"])
            log.info("[%s] chunk-boundary split test", key)
            summary["chunk_boundary"] = run_boundary_test(adapter)
            summary["streaming"] = run_streaming_test(adapter, datasets["synthetic_test"])
        syn = summary["pairs"].get("synthetic_test", {})
        if "model_only_default" in syn:
            m = syn["model_only_default"]["metrics"]
            summary["flags"] = evaluate_flags(
                m, benchmark_config()["gates"], spanish_recall=m["by_language"].get("es", {}).get("recall_full"),
                truncation_detected=any(r["truncated"] for r in summary.get("long_documents", []) if r["strategy"] == "truncate"),
                coverage=meta["coverage"])
            full = syn["stages"]["MODEL_DETERMINISTIC_REGISTRY"]["metrics"]
            summary["flags_full_shield"] = evaluate_flags(
                full, benchmark_config()["gates"], spanish_recall=full["by_language"].get("es", {}).get("recall_full"),
                truncation_detected=False, coverage={})
        summary["status"] = "PASS"
        summary["inference_seconds"] = run.secs
        summary["wall_seconds"] = time.perf_counter() - t_start
    except Exception as exc:  # noqa: BLE001
        import traceback
        log.exception("[%s] evaluation failed", key)
        summary.update({"status": "FAILED DURING INFERENCE", "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc()})
    finally:
        adapter.unload()
    _dump(run_dir / "metrics" / f"{key}.json", summary)
    return summary


def evaluate_shield_baselines(datasets: dict[str, Dataset], shield: dict[str, ShieldComponents], run_dir: Path) -> dict[str, Any]:
    """Model-free stages (deterministic rules, rules + registry) per test dataset."""
    out: dict[str, Any] = {}
    for _, test_name in EVAL_PAIRS:
        if test_name not in datasets:
            continue
        test = datasets[test_name]
        C = shield[test_name]
        out[test_name] = {}
        for st in MODEL_FREE:
            preds = {s.id: _as_canonical(C.stage(st, s, None)) for s in test.samples}
            sms, m, saf = evaluate_spans(test, preds, canonical_compatible)
            out[test_name][st] = {"metrics": m, "safety": {k: v for k, v in saf.items() if k != "per_document"}}
            fns = [r for sm in sms for r in false_negatives(sm, "deterministic", TextPolicy(True), dataset=test_name,
                                                             stage=st, compatible=canonical_compatible)]
            _jsonl(run_dir / "false_negatives" / f"{st.lower()}__{test_name}.jsonl", fns)
            out[test_name][st]["failure_counts"] = _count([r["primary_failure"] for r in fns])
    _dump(run_dir / "metrics" / "_shield_baselines.json", out)
    return out
