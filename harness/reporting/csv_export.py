"""CSV outputs: metrics, per-category, subsets, stages, false negatives, provenance, latency."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from harness.config import models_registry, published_evidence
from harness.reporting.analysis import FULL, g, ok, stage_metric
from harness.shield.pipeline import STAGES


def write_csvs(run, run_dir: Path, out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    pm, pc, sub, stg, lat = [], [], [], [], []
    for k, r in ok(run).items():
        for test in ("synthetic_test", "meddocan_test"):
            ev = "SYNTHETIC DATA" if test == "synthetic_test" else "PUBLIC CORPUS (real case text, synthetic PHI)"
            for cond, path in [("model_only_default", ("model_only_default",)), ("model_only_dev_threshold", ("model_only_dev_threshold",))] + \
                              [(f"stage:{st}", ("stages", st)) for st in STAGES if g(r, "pairs", test, "stages", st)]:
                node = g(r, "pairs", test, *path) or {}
                m, s = node.get("metrics") or {}, node.get("safety") or {}
                if not m:
                    continue
                row = {"model": k, "test_set": test, "evidence_label": ev, "condition": cond, "threshold": node.get("threshold"),
                       "entity_recall_full": m.get("entity_recall"), "char_recall": m.get("char_recall"),
                       "residual_phi_char_rate": m.get("residual_phi_character_rate"), "high_risk_recall": m.get("high_risk_recall"),
                       "precision_overlap": g(m, "detection", "overlap", "precision"), "f1_overlap": g(m, "detection", "overlap", "f1"),
                       "doc_sanitization": s.get("complete_document_sanitization_rate"),
                       "residual_after_shield_mean": g(s, "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
                       "residual_after_shield_median": g(s, "RESIDUAL_PHI_AFTER_SHIELD", "median"),
                       "residual_after_shield_p95": g(s, "RESIDUAL_PHI_AFTER_SHIELD", "p95"),
                       "residual_after_shield_worst_doc": g(s, "RESIDUAL_PHI_AFTER_SHIELD", "worst_document"),
                       "false_all_clear_rate": s.get("false_all_clear_rate"), "over_redaction_char_rate": s.get("over_redaction_char_rate"),
                       "false_negatives": m.get("false_negatives"), "false_positives": m.get("false_positives")}
                (stg if cond.startswith("stage:") else pm).append(row)
                if cond in ("model_only_default", f"stage:{FULL}"):
                    for c, v in (m.get("per_category") or {}).items():
                        if v.get("support"):
                            pc.append({"model": k, "test_set": test, "condition": cond, "category": c, "support": v["support"],
                                       "recall_full": v["recall_full"], "recall_overlap": v["recall_overlap"],
                                       "char_coverage": v["char_coverage"], "typed_precision": v["typed_precision"],
                                       "false_negatives": v["false_negatives_full"], "false_positives_typed": v["false_positives_typed"]})
                    for dim in ("by_subset", "by_language", "by_noise_level", "by_phenomenon"):
                        for grp, v in (m.get(dim) or {}).items():
                            sub.append({"model": k, "test_set": test, "condition": cond, "dimension": dim[3:], "group": grp,
                                        "documents": v.get("n_documents"), "support": v.get("support"), "recall_full": v.get("recall_full"),
                                        "char_coverage": v.get("char_coverage"), "doc_sanitization": v.get("complete_document_sanitization_rate"),
                                        "false_positives": v.get("false_positives")})
        for dev, L in (("mps", run["latency"].get(k)), ("cpu", run["latency_cpu"].get(k))):
            if L and "single_doc" in L:
                lat.append({"model": k, "device": dev, "load_time_s": L["load_time_s"], "median_ms": L["single_doc"]["median_ms"],
                            "mean_ms": L["single_doc"]["mean_ms"], "p95_ms": L["single_doc"]["p95_ms"],
                            "batch_median_ms": L["batch"]["median_ms"], "docs_per_second_batch": L["docs_per_second_batch"],
                            "peak_rss_mb": L.get("peak_rss_mb"), "accelerator_mb": L.get("peak_mps_driver_allocated_mb"),
                            "model_file_mb": (L.get("model_file_bytes") or 0) / 1e6,
                            "cold_start_s": g(L, "cold_start", "cold_start_total_s")})
    for test, b in (run.get("baselines") or {}).items():
        for st, v in b.items():
            m, s = v["metrics"], v["safety"]
            stg.append({"model": "(none)", "test_set": test, "condition": f"stage:{st}", "entity_recall_full": m["entity_recall"],
                        "high_risk_recall": m["high_risk_recall"], "precision_overlap": m["detection"]["overlap"]["precision"],
                        "doc_sanitization": s["complete_document_sanitization_rate"],
                        "residual_after_shield_mean": s["RESIDUAL_PHI_AFTER_SHIELD"]["mean"],
                        "residual_after_shield_p95": s["RESIDUAL_PHI_AFTER_SHIELD"]["p95"]})
    fns = []
    for p in sorted((run_dir / "false_negatives").glob("*.jsonl")):
        fns += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    prov = [{"key": k, **{f: (json.dumps(v) if isinstance(v, (list, dict)) else v) for f, v in e.items()}} for k, e in models_registry().items()]
    dprov = [{"dataset": n, **{f: (json.dumps(v) if isinstance(v, (list, dict)) else v) for f, v in d.items() if f != "by_subset"}}
             for n, d in (run.get("datasets") or {}).items()]
    files = {}
    for name, rows in [("per_model_metrics", pm), ("pipeline_stage_metrics", stg), ("per_category_metrics", pc),
                       ("subset_metrics", sub), ("false_negatives", fns), ("latency_metrics", lat),
                       ("model_provenance", prov), ("dataset_provenance", dprov),
                       ("published_evidence", published_evidence()["entries"])]:
        df = pd.DataFrame(rows)
        for col in df.columns:
            if df[col].map(lambda v: isinstance(v, (list, dict))).any():
                df[col] = df[col].map(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
        p = out / f"{name}.csv"
        df.to_csv(p, index=False)
        files[name] = p
    return files
