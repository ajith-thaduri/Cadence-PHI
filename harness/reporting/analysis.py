"""Evidence-first analysis for the v2 report.

Nothing here combines synthetic and real results into one score. The recommendation is a
transparent, documented procedure over separately reported evidence:

  MODEL SELECTION (contextual ML candidate)
    1. eligibility: valid provenance, commercially usable licence, English support
    2. real clinical evidence tier (independent > author-reported held-out > uncertain identity > none)
    3. within the best available tier: lowest RESIDUAL_PHI_AFTER_SHIELD (full shield, synthetic test)
    4. tie-break: contextual-name recall of the model alone (names the rules cannot catch)
  PRODUCTION ARCHITECTURE
    decided from the measured stage comparison (model alone vs rules vs rules+registry vs
    model+rules+registry) plus published field findings - independent of which model wins.
"""
from __future__ import annotations

from typing import Any, Optional

from harness.config import model_entry, published_evidence

TAG = {"syn": "[OUR SYNTHETIC BENCHMARK]", "med": "[OUR PUBLIC-CORPUS TEST — MEDDOCAN: real case text, SYNTHETIC PHI]",
       "pub": "[PUBLISHED REAL DATA]", "inf": "[ENGINEERING INFERENCE]", "unk": "[UNKNOWN]",
       "real": "[OUR REAL-DATA REPRODUCTION]"}
TIER_NAMES = {1: "A - independent evaluation on real clinical data (exact checkpoint)",
              2: "B - author-reported, held-out real clinical data",
              3: "C - real clinical results exist but checkpoint identity uncertain",
              4: "D - no real clinical evaluation found"}
CONTEXT_CATS = ["NAME", "RELATIVE_NAME", "PROVIDER_NAME", "CARE_TEAM_MEMBER", "FACILITY_NAME"]
GATE_CATS = ["NAME", "DOB", "PHONE", "ADDRESS", "MEMBER_ID", "MRN", "NPI", "PROVIDER_NAME", "FACILITY_NAME"]
FULL = "MODEL_DETERMINISTIC_REGISTRY"


def g(d: Any, *path, default=None):
    for p in path:
        if not isinstance(d, dict) or p not in d or d[p] is None:
            return default
        d = d[p]
    return d


def pct(v: Optional[float], digits: int = 1) -> str:
    return "n/a" if v is None else f"{v * 100:.{digits}f}%"


def ok(run) -> dict[str, dict]:
    return {k: r for k, r in run["models"].items() if r.get("status") == "PASS"}


def evidence_tier(key: str) -> int:
    ents = [e for e in published_evidence()["entries"] if e["model"] == key and e.get("real_clinical")]
    if any(e["evaluator"] == "independent" and e["checkpoint_identity"] == "exact" for e in ents):
        return 1
    def has_number(v) -> bool:
        import re
        return isinstance(v, (int, float)) or bool(re.search(r"\d\.\d", str(v)))
    if any(e["evaluator"] == "authors" and e.get("held_out") is True and e["checkpoint_identity"] in ("exact", "likely")
           and has_number(e.get("value")) for e in ents):
        return 2
    if ents:
        return 3
    return 4


def license_ok(key: str) -> bool:
    lic = str(model_entry(key).get("license", "")).lower()
    return any(x in lic for x in ("mit", "apache"))


def english(key: str) -> bool:
    return "en" in (model_entry(key).get("languages_documented") or [])


def stage_metric(r, test, stage, *path):
    if stage == "MODEL_ONLY_DEFAULT":
        return g(r, "pairs", test, "model_only_default", *path)
    return g(r, "pairs", test, "stages", stage, *path)


def context_recall(r, test, stage="MODEL_ONLY_DEFAULT") -> Optional[float]:
    pc = stage_metric(r, test, stage, "metrics", "per_category") or {}
    sup = sum((pc.get(c) or {}).get("support", 0) for c in CONTEXT_CATS)
    hit = sum(((pc.get(c) or {}).get("recall_full") or 0) * (pc.get(c) or {}).get("support", 0) for c in CONTEXT_CATS)
    return hit / sup if sup else None


def candidate_table(run) -> list[dict[str, Any]]:
    rows = []
    for k, r in ok(run).items():
        rows.append({
            "key": k, "name": model_entry(k)["display_name"], "tier": evidence_tier(k),
            "license_ok": license_ok(k), "english": english(k), "role": model_entry(k).get("evaluation_role"),
            "syn_model_hr": stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "metrics", "high_risk_recall"),
            "syn_model_ctx": context_recall(r, "synthetic_test"),
            "syn_full_resid": stage_metric(r, "synthetic_test", FULL, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
            "syn_full_san": stage_metric(r, "synthetic_test", FULL, "safety", "complete_document_sanitization_rate"),
            "syn_full_hr": stage_metric(r, "synthetic_test", FULL, "metrics", "high_risk_recall"),
            "syn_full_prec": stage_metric(r, "synthetic_test", FULL, "metrics", "detection", "overlap", "precision"),
            "syn_asr": stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "metrics", "by_subset", "NOISY_ASR", "recall_full"),
            "med_model_hr": stage_metric(r, "meddocan_test", "MODEL_ONLY_DEFAULT", "metrics", "high_risk_recall"),
            "med_full_resid": stage_metric(r, "meddocan_test", FULL, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
            "med_modeldet_resid": stage_metric(r, "meddocan_test", "MODEL_DETERMINISTIC", "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
            "syn_es_full": stage_metric(r, "synthetic_test", FULL, "metrics", "by_language", "es", "recall_full"),
            "neg_docs_redacted": stage_metric(r, "synthetic_test", "MODEL_ONLY_DEFAULT", "safety", "negative_control_docs_with_any_redaction"),
            "cpu_p95": g(run["latency_cpu"].get(k), "single_doc", "p95_ms"),
        })
    return rows


def recommend(run) -> dict[str, Any]:
    rows = candidate_table(run)
    eligible = [r for r in rows if r["license_ok"] and r["english"] and r["role"] != "spanish_specialist"
                and r["syn_full_resid"] is not None]
    if not eligible:
        return {"status": "NO ELIGIBLE MODEL", "rows": rows}
    best_tier = min(r["tier"] for r in eligible)
    tier_set = [r for r in eligible if r["tier"] == best_tier]
    pick = sorted(tier_set, key=lambda r: (round(r["syn_full_resid"], 4), -(r["syn_model_ctx"] or 0)))[0]
    any_measured_best = sorted(eligible, key=lambda r: (round(r["syn_full_resid"], 4), -(r["syn_model_ctx"] or 0)))[0]
    es_rows = [r for r in rows if r["med_modeldet_resid"] is not None]
    es_best = sorted(es_rows, key=lambda r: r["med_modeldet_resid"])[0] if es_rows else None
    # where does the pick rank poorly among eligible models? (lower-is-better metrics flagged accordingly)
    weak = []
    for field, label, higher in [("syn_model_hr", "high-risk recall, model alone [SYN]", True),
                                 ("syn_model_ctx", "contextual-name recall, model alone [SYN]", True),
                                 ("syn_asr", "ASR recall, model alone [SYN]", True),
                                 ("syn_full_prec", "precision, full shield [SYN]", True),
                                 ("med_model_hr", "high-risk recall, model alone [MEDDOCAN]", True),
                                 ("neg_docs_redacted", "negative-control documents redacted [SYN]", False)]:
        vals = [r[field] for r in eligible if r[field] is not None]
        v = pick.get(field)
        if v is None or len(vals) < 3:
            continue
        rank = sorted(vals, reverse=higher).index(v) + 1
        if rank > (len(vals) + 1) // 2:
            weak.append((label, v, rank, len(vals)))
    return {"rows": rows, "eligible": [r["key"] for r in eligible], "best_tier": best_tier,
            "pick": pick, "measured_best_any_tier": any_measured_best, "spanish_best_full_shield": es_best,
            "pick_weaknesses": weak,
            "shortlist": _shortlist(pick, any_measured_best, eligible),
            "production_validated": False,
            "reason_not_validated": "No result labelled [OUR REAL-DATA REPRODUCTION] exists: i2b2 2006/2014, PhysioNet and SHIELD were not legally obtainable here, and MEDDOCAN contains synthetic PHI."}


def architecture_evidence(run) -> dict[str, Any]:
    out = {"per_model": [], "baselines": {}}
    for test in ("synthetic_test", "meddocan_test"):
        b = run["baselines"].get(test) or {}
        out["baselines"][test] = {st: {"hr": g(v, "metrics", "high_risk_recall"),
                                       "resid": g(v, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
                                       "san": g(v, "safety", "complete_document_sanitization_rate")} for st, v in b.items()}
    for k, r in ok(run).items():
        for test in ("synthetic_test", "meddocan_test"):
            mo = g(r, "pairs", test, "stages", "MODEL_ONLY")
            fu = g(r, "pairs", test, "stages", FULL)
            if not mo or not fu:
                continue
            out["per_model"].append({
                "key": k, "test": test,
                "model_resid": g(mo, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
                "full_resid": g(fu, "safety", "RESIDUAL_PHI_AFTER_SHIELD", "mean"),
                "model_san": g(mo, "safety", "complete_document_sanitization_rate"),
                "full_san": g(fu, "safety", "complete_document_sanitization_rate"),
                "model_hr": g(mo, "metrics", "high_risk_recall"), "full_hr": g(fu, "metrics", "high_risk_recall"),
                "model_prec": g(mo, "metrics", "detection", "overlap", "precision"),
                "full_prec": g(fu, "metrics", "detection", "overlap", "precision")})
    pm = [x for x in out["per_model"] if x["model_resid"] is not None and x["full_resid"] is not None]
    out["combined_better_everywhere"] = bool(pm) and all(x["full_resid"] < x["model_resid"] for x in pm)
    detreg = {t: g(out["baselines"], t, "DETERMINISTIC_REGISTRY", "resid") for t in ("synthetic_test", "meddocan_test")}
    out["ml_adds_over_rules_registry"] = {
        t: [x["key"] for x in pm if x["test"] == t and detreg.get(t) is not None and x["full_resid"] < detreg[t]]
        for t in ("synthetic_test", "meddocan_test")}
    out["best_single_model_alone"] = {
        t: min((x for x in pm if x["test"] == t), key=lambda x: x["model_resid"], default=None) for t in ("synthetic_test", "meddocan_test")}
    return out


def gate_table(run, test: str, stage: str) -> list[list[str]]:
    rows = []
    for k, r in ok(run).items():
        pc = stage_metric(r, test, stage, "metrics", "per_category") or {}
        cells = []
        for c in GATE_CATS:
            v = (pc.get(c) or {}).get("recall_full") if (pc.get(c) or {}).get("support") else None
            cells.append("n/a" if v is None else (f"{pct(v)} ✗" if v < 0.95 else f"{pct(v)} ✓"))
        hr = stage_metric(r, test, stage, "metrics", "high_risk_recall")
        cells.append("n/a" if hr is None else (f"{pct(hr)} ✗" if hr < 0.95 else f"{pct(hr)} ✓"))
        rows.append([model_entry(k)["display_name"]] + cells)
    return rows


def _shortlist(pick, measured_best, eligible) -> list[tuple[str, str]]:
    """Models to carry into Cadence-data validation, each with the reason it is on the list."""
    out = [(pick["key"], "best real-clinical evidence tier")]
    if measured_best["key"] != pick["key"]:
        out.append((measured_best["key"], "lowest residual PHI after the full shield [SYN]"))
    with_evidence = [r for r in eligible if r["tier"] <= 2 and r["syn_asr"] is not None]
    if with_evidence:
        asr = max(with_evidence, key=lambda r: r["syn_asr"])
        if asr["key"] not in [k for k, _ in out]:
            out.append((asr["key"], "best ASR robustness among models with real-clinical evidence (tier A/B) [SYN]"))
    return out
