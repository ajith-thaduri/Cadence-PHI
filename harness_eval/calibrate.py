"""Threshold selection + calibration research on DEVELOPMENT data only (the CLI refuses test data).

Selection rule (pre-registered, recall first):
  model level   for each model, the HIGHEST thresholds.model.<m> whose total residual PHI
                characters (all entities, all dev datasets) is within `tolerance` x total core
                characters of the grid minimum
  model x entity the same rule per entity (thresholds.model_entity.<m>.<E>), holding the
                model-level value elsewhere; only when the entity has >= min_support dev gold
                mentions, otherwise the model-level value is inherited

Calibration research (reported, never applied automatically): reliability bins + ECE of the
model's raw confidence per entity (a candidate is "correct" if it overlaps a gold entity of a
compatible type). Platt / isotonic are flagged SUITABLE only with >= 50 positive and >= 50
negative candidates and ECE > 0.05. Deterministic / registry scores are constants: not calibratable.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from harness.evaluation.runner import canonical_compatible
from harness.phi.config import HarnessConfig
from harness_eval.scoring import GRID, Bundle, csv_rows, prepare, score, threshold_curves

log = logging.getLogger(__name__)


def _pick(curve: dict[float, int], core: int, tol: float) -> float:
    lo = min(curve.values())
    return max(t for t, r in curve.items() if r <= lo + tol * core)


def reliability(bundles: list[Bundle], model: str) -> list[dict[str, Any]]:
    per = defaultdict(list)
    rid = f"model_{model}"
    for b in bundles:
        for req in b.requests:
            gold = [g for g in b.golds[req.request_id].entities if not g.ignore_region]
            ign = [g for g in b.golds[req.request_id].entities if g.ignore_region]
            for e in b.collected[req.request_id].evidence:
                if e.recognizer_id != rid:
                    continue
                ov = [g for g in gold if e.original_start < g.end and g.start < e.original_end]
                if not ov and any(e.original_start < g.end and g.start < e.original_end for g in ign):
                    continue
                ok = any(canonical_compatible(e.entity_type, g.entity_type) for g in ov)
                per[e.entity_type].append((e.raw_score, ok))
                per["*ALL*"].append((e.raw_score, ok))
    rows = []
    for ent, xs in sorted(per.items()):
        s = np.array([x[0] for x in xs])
        y = np.array([x[1] for x in xs], dtype=float)
        ece, bins = 0.0, []
        edges = np.linspace(0, 1, 11)
        for a, b_ in zip(edges[:-1], edges[1:]):
            m = (s >= a) & ((s < b_) if b_ < 1 else (s <= b_))
            if m.sum():
                ece += m.sum() / len(s) * abs(s[m].mean() - y[m].mean())
                bins.append(f"{a:.1f}-{b_:.1f}:{int(m.sum())}@{y[m].mean():.2f}")
        pos, neg = int(y.sum()), int(len(y) - y.sum())
        rows.append({"model": model, "entity": ent, "candidates": len(xs), "positives": pos, "negatives": neg,
                     "mean_score": float(s.mean()), "observed_precision": float(y.mean()), "ece": round(ece, 4),
                     "reliability_bins": " ".join(bins),
                     "platt_isotonic": ("SUITABLE (not applied; thresholds used instead)" if pos >= 50 and neg >= 50 and ece > 0.05
                                        else "NOT NEEDED (ECE <= 0.05)" if pos >= 50 and neg >= 50 else "INSUFFICIENT DATA")})
    return rows


def _total(bundles: list[Bundle], c: HarnessConfig) -> tuple[int, int]:
    tot = core = 0
    for b in bundles:
        r = score(b, c, keep=False, redact=False)
        tot += sum(x["residual_chars"] for x in r["entity_rows"])
        core += sum(x["core_chars"] for x in r["entity_rows"])
    return tot, core


def run_calibration(cfg: HarnessConfig, names: list[str], device: str, out: Path, write: Path,
                    tol: float, min_support: int, refresh: bool) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if cfg.raw.get("_overlays"):
        log.warning("calibrating on top of existing threshold overlays %s - use --no-calibrated for a clean run",
                    len(cfg.raw["_overlays"]))
    bundles = [prepare(n, cfg, device, refresh) for n in names]
    overlay: dict[str, Any] = {"_provenance": {"developed_on": names, "languages": cfg.supported_languages,
                                               "date": str(date.today()), "tolerance": tol,
                                               "min_support": min_support, "config_sha256": cfg.sha256(),
                                               "recognizers_enabled": cfg.describe()["recognizers_enabled"],
                                               "rule": __doc__.split("Calibration research")[0].strip()},
                               "thresholds": {"model": {}, "model_entity": {}}, "_support": {}}
    curves_all, rel_rows, table = [], [], []
    for m in cfg.raw["models"]:
        solo = cfg.copy()
        solo.only_models([m])                # each model calibrated in the production shape: alone + rules + registry
        curve, core = {}, 0
        for t in GRID:
            c = solo.copy()
            c.set_value(f"thresholds.model.{m}", t)
            curve[t], core = _total(bundles, c)
        mt = _pick(curve, core, tol)
        solo.set_value(f"thresholds.model.{m}", mt)
        overlay["thresholds"]["model"][m] = mt
        curves = [r for b in bundles for r in threshold_curves(b, solo, [m])]
        curves_all += curves
        agg, sup, ecore = defaultdict(dict), defaultdict(int), defaultdict(int)
        for r in curves:
            agg[r["entity"]][r["threshold"]] = agg[r["entity"]].get(r["threshold"], 0) + r["residual_chars"]
        seen = set()
        for r in curves:
            if (r["dataset"], r["entity"]) not in seen:
                seen.add((r["dataset"], r["entity"]))
                sup[r["entity"]] += r["support"]
                ecore[r["entity"]] += r["core_chars"]
        per = {}
        for ent, cv in agg.items():
            if sup[ent] >= min_support:
                et = _pick(cv, ecore[ent], tol)
                if et != mt:
                    per[ent] = et
        overlay["thresholds"]["model_entity"][m] = per
        overlay["_support"][m] = dict(sup)
        final = solo.copy()
        for ent, t in per.items():
            final.set_value(f"thresholds.model_entity.{m}.{ent}", t)
        for b in bundles:
            for er in score(b, final, keep=False, redact=False)["entity_rows"]:
                t, src = final.threshold_for(er["entity"], f"model_{m}", m, "model_confidence")
                table.append({"model": m, "dataset": b.name, "entity": er["entity"], "threshold": t, "threshold_source": src,
                              "support": er["support"], "recall": er["recall"], "full_span_recall": er["full_span_recall"],
                              "precision": er["precision"], "residual_chars": er["residual_chars"], "fp": er["fp"], "fn": er["fn"]})
        rel_rows += reliability(bundles, m)
        log.info("calibrated %s: model threshold %.2f, %d entity overrides", m, mt, len(per))
    write.parent.mkdir(parents=True, exist_ok=True)
    header = ("# GENERATED by `python -m harness.calibrate` - DO NOT EDIT BY HAND.\n"
              f"# Developed on: {', '.join(names)} (development split only). Never tuned on test data.\n"
              "# Applied automatically by the config loader (environments with apply_generated: true);\n"
              "# the loader refuses this file if _provenance.developed_on lists any non-development dataset.\n")
    write.write_text(header + yaml.safe_dump(overlay, sort_keys=False))
    csv_rows(out / "threshold_curves.csv", curves_all)
    csv_rows(out / "thresholds.csv", table)
    csv_rows(out / "calibration_reliability.csv", rel_rows)
    (out / "calibration.json").write_text(json.dumps({"overlay": overlay, "reliability": rel_rows}, indent=1, default=str))
    print(f"threshold overlay: {write}\nreport: {out}")
    for m in cfg.raw["models"]:
        print(f"  {m}: model threshold {overlay['thresholds']['model'][m]}  model x entity {overlay['thresholds']['model_entity'][m]}")
    return overlay


if __name__ == "__main__":      # python -m harness_eval.calibrate --dataset cadence_dev
    from harness_eval.cli import calibrate_main
    calibrate_main()
