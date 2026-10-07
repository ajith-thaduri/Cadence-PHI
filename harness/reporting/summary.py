"""Concise terminal summary. No winner is declared."""
from __future__ import annotations

import json
from pathlib import Path

from harness.config import model_entry

W = 78


def _pct(v) -> str:
    return "   n/a" if v is None else f"{v * 100:6.1f}%"


def print_terminal_summary(run_dir: Path) -> None:
    mdir = Path(run_dir) / "metrics"
    models = {}
    for p in sorted(mdir.glob("*.json")):
        if not p.name.startswith("_"):
            models[p.stem] = json.loads(p.read_text())
    ds = json.loads((mdir / "_datasets.json").read_text()) if (mdir / "_datasets.json").exists() else {}
    print("=" * W)
    print("CLINICAL PHI MODEL BENCHMARK (v2)")
    print("=" * W)
    for n, d in ds.items():
        print(f"  {n:16s} {d.get('status')}" + (f"  ({d.get('documents')} docs, {d.get('entities')} entities)" if d.get("documents") else ""))
    for test in ("synthetic_test", "meddocan_test"):
        tag = "[OUR SYNTHETIC BENCHMARK]" if test == "synthetic_test" else "[PUBLIC CORPUS - real case text, SYNTHETIC PHI]"
        print("-" * W)
        print(f"{test} {tag}")
        print(f"{'MODEL':28s} {'STATUS':6s} {'HR model':>9s} {'HR shield':>10s} {'Resid.shld':>10s} {'DocSan shld':>11s}")
        for k, r in models.items():
            pr = (r.get("pairs") or {}).get(test, {})
            mo = (pr.get("model_only_default") or {}).get("metrics", {})
            sh = ((pr.get("stages") or {}).get("MODEL_DETERMINISTIC_REGISTRY") or {})
            shm, shs = sh.get("metrics", {}), sh.get("safety", {})
            print(f"{model_entry(k)['display_name'][:28]:28s} {str(r.get('status'))[:6]:6s} {_pct(mo.get('high_risk_recall')):>9s} "
                  f"{_pct(shm.get('high_risk_recall')):>10s} {_pct((shs.get('RESIDUAL_PHI_AFTER_SHIELD') or {}).get('mean')):>10s} "
                  f"{_pct(shs.get('complete_document_sanitization_rate')):>11s}")
    print("=" * W)
    print("No model is declared best. Further analysis required - see reports/final_report.html")
    print("=" * W)
