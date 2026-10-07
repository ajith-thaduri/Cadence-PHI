"""Ablation ladder and configuration comparison over ONE collected evidence set per dataset.

Every variant differs from another only in configuration (presets in the YAML), so each delta
is attributable:
  presets       RULES_ONLY, REGISTRY_ONLY, RULES_REGISTRY, OBI_ONLY, OBI_RULES, OBI_FULL_BASELINE,
                OBI_FULL_NORMALIZED, OBI_FULL, STANFORD_FULL, MEDNOTES_FULL, OBI_STANFORD_FULL, ...
  normalizers   OBI_FULL_BASELINE + each normalizer alone; OBI_FULL - each normalizer
  contextual    OBI_FULL - each contextual recognizer
Plus: per-entity outcome attribution, second-model measurements (residual, high-risk and
critical-category recovery, added false positives / over-redaction, latency, memory) and the
ASR stage analysis (word error rate, entity survival, detector recall conditional on survival).
The production recommendation is NOT made here - this only measures.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

from harness.decision.experiment import materially_lower
from harness.phi.config import HarnessConfig
from harness_eval.attribution import source_group
from harness_eval.scoring import Bundle, breakdown, csv_rows, high_risk, prepare, score
from harness.phi.normalizers import NORMALIZERS
from harness_eval.report import pct, write_reports

log = logging.getLogger(__name__)
FULL = "OBI_FULL"
BASE = "OBI_FULL_BASELINE"


def variants(cfg: HarnessConfig, only: Optional[list[str]] = None) -> dict[str, HarnessConfig]:
    out: dict[str, HarnessConfig] = {}
    for name in cfg.raw.get("presets") or {}:
        c = cfg.copy()
        c.apply_preset(name)
        out[name] = c
    for n in NORMALIZERS:
        c = cfg.copy()
        c.apply_preset(BASE)
        c.raw["normalizers"][n]["enabled"] = True
        out[f"{BASE}+{n}"] = c
        c = cfg.copy()
        c.apply_preset(FULL)
        c.raw["normalizers"][n]["enabled"] = False
        out[f"{FULL}-{n}"] = c
    for rid, spec in cfg.raw["recognizers"].items():
        if spec.get("contextual"):
            c = cfg.copy()
            c.apply_preset(FULL)
            c.raw["recognizers"][rid]["enabled"] = False
            out[f"{FULL}-{rid}"] = c
    for g in GROUPS:                       # source-group ablation: FULL minus one evidence family
        c = cfg.copy()
        c.apply_preset(FULL)
        for rid in list(c.raw["recognizers"]):
            if source_group(rid) == g:
                c.raw["recognizers"][rid]["enabled"] = False
        if g.startswith("MODEL:"):
            c.raw["models"][g.split(":", 1)[1]]["enabled"] = False
        out[f"{FULL}-GROUP:{g}"] = c
    return {k: v for k, v in out.items() if not only or k in only}


GROUPS = ["DETERMINISTIC", "CONVERSATIONAL", "REGISTRY", "MODEL:obi_roberta"]


def group_ablation(results: dict, names: list[str]) -> list[dict[str, Any]]:
    """Per dataset x source group x entity: gold entities protected by FULL that are NOT protected when the
    group is removed (= uniquely protected by that group), and the spurious spans the group adds."""
    rows = []
    ok = lambda o: o.residual_chars == 0 and o.start is not None and o.outcome != "redaction_failure"
    for n in names:
        full = results.get((FULL, n))
        if not full:
            continue
        base = {o.annotation_id: o for o in full["outcomes"]}
        for g in GROUPS:
            abl = results.get((f"{FULL}-GROUP:{g}", n))
            if not abl:
                continue
            lost = defaultdict(int)
            for o in abl["outcomes"]:
                b0 = base.get(o.annotation_id)
                if b0 is not None and ok(b0) and not ok(o):
                    lost[o.entity_type] += 1
            rows.append({"dataset": n, "group": g, "entities_uniquely_protected": sum(lost.values()),
                         "by_entity": json.dumps(dict(sorted(lost.items(), key=lambda kv: -kv[1]))),
                         "spurious_added_by_group": full["summary"]["spurious_spans"] - abl["summary"]["spurious_spans"],
                         "residual_without_group": abl["summary"]["RESIDUAL_PHI_AFTER_SHIELD"]["mean"],
                         "residual_full": full["summary"]["RESIDUAL_PHI_AFTER_SHIELD"]["mean"]})
    return rows


def _wer(ref: str, hyp: str) -> float:
    r = re.findall(r"[\wÀ-ÿ']+", ref.lower())
    h = re.findall(r"[\wÀ-ÿ']+", hyp.lower())
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
        prev = cur
    return prev[-1] / max(1, len(r))


def asr_analysis(bt: Bundle, ba: Bundle, rt: dict, ra: dict) -> dict[str, Any]:
    """Is a Spanish (or any) failure an ASR problem or a detector problem?
    survival   = gold entities still present after ASR / (present + deleted + garbled)
    recall|surv = detector full recall on the ASR text, counting only entities that survived."""
    text_by_src = {g.metadata["doc_id"]: (r, g) for r, g in ((r, bt.golds[r.request_id]) for r in bt.requests)}
    surv = defaultdict(lambda: defaultdict(int))
    wer = defaultdict(list)
    for req in ba.requests:
        g = ba.golds[req.request_id]
        lang = g.metadata.get("language")
        surv[lang]["survived"] += sum(1 for e in g.entities if not e.ignore_region)
        for lost in g.asr_lost:
            surv[lang][f"{lost['kind']}_by_asr"] += 1
        src = g.metadata.get("source_id")
        if src in text_by_src:
            ref = re.sub(r"^[A-Z_]+:\s*", "", text_by_src[src][0].text, flags=re.M)
            hyp = re.sub(r"^[A-Z_]+:\s*", "", req.text, flags=re.M)
            wer[lang].append(_wer(ref, hyp))
    spoken = {ba.golds[r.request_id].metadata.get("source_id") for r in ba.requests}

    def by_lang(res: dict, restrict: Optional[set] = None) -> dict[str, dict]:
        agg = defaultdict(lambda: [0, 0, 0, 0])
        for rec, d in zip(res["records"], res["document_rows"]):
            if restrict is not None and d["doc"] not in restrict:
                continue
            a = agg[d["language"]]
            ents = [o for o in rec.outcomes if o.outcome != "ASR_destroyed"]
            a[0] += len(ents)
            a[1] += sum(1 for o in ents if o.residual_chars == 0)
            a[2] += d["core_chars"]
            a[3] += d["residual_chars"]
        return {l: {"entities": a[0], "full_recall": a[1] / a[0] if a[0] else None,
                    "residual_char_rate": a[3] / a[2] if a[2] else None} for l, a in agg.items()}

    return {"wer_raw_mean": {l: float(np.mean(v)) for l, v in wer.items() if v},
            "wer_note": "raw word error rate vs the authored reference; digits vs number words count as errors (overstates semantic error)",
            "entity_survival": {l: {**v, "survival_rate": v["survived"] / max(1, sum(v.values()))} for l, v in surv.items()},
            "detector_clean_text_same_docs": by_lang(rt, spoken), "detector_asr_given_survival": by_lang(ra)}


def _row(name: str, ds: str, r: dict) -> dict[str, Any]:
    s = r["summary"]
    rr = s["RESIDUAL_PHI_AFTER_SHIELD"]
    return {"variant": name, "dataset": ds, "residual_mean": rr["mean"], "residual_p95": rr["p95"],
            "corpus_residual": rr["corpus_residual_char_rate"], "sanitized": s["complete_document_sanitization_rate"],
            "high_risk_full_recall": s["high_risk_full_recall"], "entity_full_recall": s["entity_full_recall"],
            "span_precision": s["span_precision"], "spurious": s["spurious_spans"], "over_redaction": s["over_redaction_char_rate"]}


def _latency_memory(bundles: list[Bundle]) -> dict[str, dict[str, Any]]:
    out = {}
    eng = bundles[0].engine
    for key, m in eng.models.items():
        ms = [c.per_recognizer_ms.get(f"model_{key}", 0.0) for b in bundles for c in b.collected.values()]
        live = [x for x in ms if x > 1.0]          # cache hits are ~0 ms; only real inference counts
        a = eng.adapters[key]
        out[key] = {"mean_ms_per_doc_inference": float(np.mean(live)) if live else None, "docs_timed": len(live),
                    "params": a._param_count(), "weights_bytes": a.model_file_bytes(), "device": a.device}
    return out


def second_model(results: dict, names: list[str], cfg: HarnessConfig, lat: dict) -> list[dict[str, Any]]:
    HR = set(high_risk(cfg))
    rows = []
    for pair in [k for k in results if k[0].startswith("OBI_") and k[0].endswith("_FULL") and k[0] != FULL and
                 k[0].count("_") == 2]:
        vname, ds = pair
        a, ab = results.get((FULL, ds)), results[pair]
        if not a:
            continue
        added = vname.split("_")[1].lower()
        added = {"stanford": "stanford_v2", "mednotes": "mednotes_gliner"}.get(added, added)
        ra, rab = a["summary"]["RESIDUAL_PHI_AFTER_SHIELD"]["mean"], ab["summary"]["RESIDUAL_PHI_AFTER_SHIELD"]["mean"]
        oa = {o.annotation_id: o for o in a["outcomes"]}
        rec_hr = rec_any = lost = 0
        crit = defaultdict(int)
        for o in ab["outcomes"]:
            p = oa.get(o.annotation_id)
            if p is None or o.outcome == "ASR_destroyed":
                continue
            if p.residual_chars > 0 and o.residual_chars == 0:
                rec_any += 1
                if o.entity_type in HR:
                    rec_hr += 1
                    crit[o.entity_type] += 1
            if p.residual_chars == 0 and o.residual_chars > 0:
                lost += 1
        rows.append({"primary": "obi_roberta", "added": added, "dataset": ds, "residual_primary": ra, "residual_pair": rab,
                     "abs_reduction": ra - rab, "rel_reduction": (ra - rab) / ra if ra else 0.0,
                     "entities_recovered": rec_any, "high_risk_recovered": rec_hr, "critical_recovered": json.dumps(dict(crit)),
                     "entities_lost": lost,
                     "added_false_positives": ab["summary"]["spurious_spans"] - a["summary"]["spurious_spans"],
                     "added_over_redaction": ab["summary"]["over_redaction_char_rate"] - a["summary"]["over_redaction_char_rate"],
                     "added_latency_ms_per_doc": (lat.get(added) or {}).get("mean_ms_per_doc_inference"),
                     "added_params": (lat.get(added) or {}).get("params"),
                     "material_M1": materially_lower(ra, rab)})
    return rows


def run_compare(cfg: HarnessConfig, names: list[str], device: str, out: Path, only: Optional[list[str]], refresh: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    V = variants(cfg, only)
    bundles = {n: prepare(n, cfg, device, refresh) for n in names}
    results, rows, ent_rows = {}, [], []
    for vname, vc in V.items():
        for n, b in bundles.items():
            r = score(b, vc, keep=vname in (FULL, BASE))
            results[(vname, n)] = r
            rows.append(_row(vname, n, r))
            ent_rows += [{"variant": vname, "dataset": n, **{k: e[k] for k in ("entity", "support", "full_span_recall", "residual_chars",
                                                                               "core_chars", "fp", "precision")}}
                         for e in r["entity_rows"] if e["support"]]
        log.info("variant %s done", vname)
    lat = _latency_memory(list(bundles.values()))
    second = second_model(results, names, cfg, lat)
    ablation = group_ablation(results, names)
    csv_rows(out / "group_ablation.csv", ablation)
    att = {n: breakdown(results[(FULL, n)])["attribution"] for n in names if (FULL, n) in results}
    asr = {}
    for t, a in (("cadence_text", "cadence_asr"), ("cadence_dev_text", "cadence_dev_asr")):
        if t in bundles and a in bundles and (FULL, t) in results:
            asr[a] = asr_analysis(bundles[t], bundles[a], results[(FULL, t)], results[(FULL, a)])
    csv_rows(out / "compare.csv", rows)
    csv_rows(out / "compare_entities.csv", ent_rows)
    csv_rows(out / "second_model.csv", second)
    csv_rows(out / "attribution.csv", [{"dataset": n, "entity": e, **v} for n, a in att.items() for e, v in a.items()])
    summary = {"datasets": names, "variants": list(V), "rows": rows, "attribution": att, "second_model": second,
               "group_ablation": ablation,
               "latency_memory": lat, "asr_analysis": asr, "config": cfg.describe(), "config_path": cfg.path}
    (out / "compare_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    extra = []
    for n in names:
        extra.append((f"Ablation ladder — {n}", ["variant", "residual mean", "P95", "docs sanitised", "high-risk full recall",
                                                  "span precision", "spurious", "over-redaction"],
                      [[r["variant"], pct(r["residual_mean"]), pct(r["residual_p95"]), pct(r["sanitized"]),
                        pct(r["high_risk_full_recall"]), pct(r["span_precision"]), str(r["spurious"]), pct(r["over_redaction"], 2)]
                       for r in rows if r["dataset"] == n], "Each row differs from OBI_FULL / OBI_FULL_BASELINE only in configuration."))
    if second:
        extra.append(("Second model (experimental capability; not a recommendation)",
                      ["added", "dataset", "residual primary", "residual pair", "abs", "rel", "recovered (high-risk)",
                       "lost", "added FP", "added ms/doc", "material (M1)?"],
                      [[s["added"], s["dataset"], pct(s["residual_primary"]), pct(s["residual_pair"]), pct(s["abs_reduction"], 2),
                        pct(s["rel_reduction"]), f"{s['entities_recovered']} ({s['high_risk_recovered']})", str(s["entities_lost"]),
                        str(s["added_false_positives"]), "–" if s["added_latency_ms_per_doc"] is None else f"{s['added_latency_ms_per_doc']:.0f}",
                        "YES" if s["material_M1"] else "no"] for s in second],
                      "M1: >=30% relative AND >=0.5 pp absolute reduction in mean residual (configs/decision_protocol.yaml)."))
    for a_name, x in asr.items():
        extra.append((f"ASR stage — {a_name}", ["language", "survived", "deleted", "garbled", "survival", "WER (raw)",
                                                "full recall clean", "full recall ASR | survived", "residual clean", "residual ASR | survived"],
                      [[l, str(v.get("survived", 0)), str(v.get("deleted_by_asr", 0)), str(v.get("garbled_by_asr", 0)),
                        pct(v["survival_rate"]), pct(x["wer_raw_mean"].get(l)),
                        pct((x["detector_clean_text_same_docs"].get(l) or {}).get("full_recall")),
                        pct((x["detector_asr_given_survival"].get(l) or {}).get("full_recall")),
                        pct((x["detector_clean_text_same_docs"].get(l) or {}).get("residual_char_rate")),
                        pct((x["detector_asr_given_survival"].get(l) or {}).get("residual_char_rate"))]
                       for l, v in x["entity_survival"].items()], x["wer_note"]))
    full_results = [results[(FULL, n)] for n in names if (FULL, n) in results]
    fsum = {"config": results and cfg.describe(), "config_path": cfg.path,
            "datasets": {r["dataset"]: {"documents": r["documents"], **r["summary"], "breakdown": breakdown(r)} for r in full_results}}
    write_reports(out, fsum, full_results, None, extra, title="PHI engine — comparison / ablation (FULL = OBI_FULL)")
    print(f"comparison: {out}")
    for r in rows:
        print(f"  {r['variant']:<40} {r['dataset']:<17} residual {r['residual_mean']*100:5.1f}%  hr-full {(r['high_risk_full_recall'] or 0)*100:5.1f}%"
              f"  prec {(r['span_precision'] or 0)*100:5.1f}%  spurious {r['spurious']}")
    return summary
