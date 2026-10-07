"""Read-only access to evaluation outputs under results/ for the run browser.

Only summary-level artefacts are exposed (summary.json, perf.json, gates.json, entity_metrics.csv, report.md);
per-span files (false_negatives.csv, predictions.jsonl, ...) are never served. Names are validated against a
strict pattern and resolved inside their kind's directory (no path traversal).
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from harness.paths import ROOT

KINDS: dict[str, dict[str, Any]] = {
    "phi": {"dir": "results/phi", "file": "summary.json", "label": "Dataset evaluation"},
    "suite": {"dir": "results/suite", "file": "summary.json", "label": "Prompt suite"},
    "checklist": {"dir": "results/checklist", "file": "summary.json", "label": "Behavioural checklist"},
    "perf": {"dir": "results/perf", "file": "perf.json", "label": "Performance"},
    "gates": {"dir": "results/gates", "file": "gates.json", "label": "Release gates"},
}
NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")
DOCS = {"readme": "README.md", "ui-guide": "docs/ui_guide.md", "system-card": "docs/system_card.md",
        "release-gates": "docs/release_gates.md", "security-controls": "docs/security_controls.md",
        "config-guide": "docs/config_guide.md", "evaluation-methodology": "docs/evaluation_methodology.md",
        "engine-contract": "docs/engine_contract.md", "annotation-guidelines": "docs/annotation_guidelines.md",
        "manual-evaluation": "docs/manual_evaluation.md", "rule-changelog": "docs/rule_changelog.md",
        "presidio-architecture": "docs/presidio_architecture.md", "plan": "docs/plans/industry_grade_harness_plan.md"}


def _run_dir(kind: str, name: str) -> Path:
    if kind not in KINDS or not NAME.match(name):
        raise FileNotFoundError(name)
    base = (ROOT / KINDS[kind]["dir"]).resolve()
    d = (base / name).resolve()
    if d.parent != base or not (d / KINDS[kind]["file"]).exists():
        raise FileNotFoundError(name)
    return d


def _num(x: Any) -> Optional[float]:
    return round(float(x), 4) if isinstance(x, (int, float)) else None


def headline(kind: str, s: dict[str, Any]) -> dict[str, Any]:
    """A few comparable numbers per run for the list view."""
    try:
        if kind == "phi":
            out = {}
            for ds, v in (s.get("datasets") or {}).items():
                out[ds] = {"documents": v.get("documents"), "residual_mean": _num((v.get("RESIDUAL_PHI_AFTER_SHIELD") or {}).get("mean")),
                           "high_risk_full_recall": _num(v.get("high_risk_full_recall")),
                           "span_precision": _num(v.get("span_precision"))}
            return {"datasets": out}
        if kind == "suite":
            return {k: s.get(k) for k in ("prompts", "expected_entities", "detected", "missed", "false_positives", "residual_phi_pct")}
        if kind == "checklist":
            return {"cases": s.get("cases"), "passed": s.get("passed"), "g5": s.get("gate_G5_mft_100pct"),
                    "mft_pass_rate": _num(s.get("mft_enabled_entities_pass_rate"))}
        if kind == "perf":
            lat = ((s.get("latency") or {}).get("<=4000") or {}).get("wall_ms") or {}
            return {"device": s.get("device"), "p95_ms_typical": lat.get("p95"), "g8": (s.get("G8") or {}).get("verdict")}
        if kind == "gates":
            return {"decision": s.get("decision"), "verdicts": {k: v.get("verdict") for k, v in (s.get("gates") or {}).items()}}
    except (AttributeError, TypeError):
        pass
    return {}


def list_runs(kind: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    out = []
    for k, spec in KINDS.items():
        if kind and k != kind:
            continue
        base = ROOT / spec["dir"]
        if not base.exists():
            continue
        for d in base.iterdir():
            f = d / spec["file"]
            if not (d.is_dir() and NAME.match(d.name) and f.exists()):
                continue
            try:
                s = json.loads(f.read_text())
            except ValueError:
                continue
            cfg = s.get("config") if isinstance(s.get("config"), dict) else {}
            out.append({"kind": k, "label": spec["label"], "name": d.name,
                        "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                        "config_sha256": cfg.get("config_sha256") or s.get("config_sha256"),
                        "environment": cfg.get("environment"), "headline": headline(k, s)})
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out[:limit]


def run_detail(kind: str, name: str) -> dict[str, Any]:
    d = _run_dir(kind, name)
    summary = json.loads((d / KINDS[kind]["file"]).read_text())
    if kind == "phi" and isinstance(summary.get("config"), dict):
        summary["config"] = {k: summary["config"].get(k) for k in ("config_sha256", "environment", "catalog_version",
                                                                   "models_enabled", "threshold_overlays")}
    detail: dict[str, Any] = {"kind": kind, "name": name, "label": KINDS[kind]["label"], "summary": summary,
                              "headline": headline(kind, summary)}
    em = d / "entity_metrics.csv"
    if em.exists():
        keep = ("dataset", "entity", "enabled", "risk_tier", "support", "full_span_recall", "full_span_recall_ci_low",
                "full_span_recall_ci_high", "strict_recall", "precision", "char_recall", "residual_chars", "insufficient_evidence")
        with open(em, newline="") as f:
            detail["entity_metrics"] = [{k: r.get(k) for k in keep} for r in csv.DictReader(f)]
    for md in ("perf.md", "gates.md"):                  # metric-only reports; evaluation reports can quote span text
        if (d / md).exists():
            detail["report_md"] = (d / md).read_text()[:200_000]
    return detail


def ledger(limit: int = 300) -> list[dict[str, Any]]:
    p = ROOT / "results" / "ledger.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(x) for x in p.read_text().splitlines()[-limit:] if x.strip()]
    return list(reversed(rows))


def latest(kind: str) -> Optional[dict[str, Any]]:
    runs = list_runs(kind, limit=1)
    return run_detail(kind, runs[0]["name"]) if runs else None


def doc(slug: str) -> tuple[str, str]:
    if slug not in DOCS:
        raise FileNotFoundError(slug)
    p = ROOT / DOCS[slug]
    if not p.exists():
        raise FileNotFoundError(slug)
    return DOCS[slug], p.read_text()


def doc_list() -> list[dict[str, str]]:
    out = []
    for slug, rel in DOCS.items():
        p = ROOT / rel
        if p.exists():
            first = next((l.lstrip("# ").strip() for l in p.read_text().splitlines() if l.startswith("#")), rel)
            out.append({"slug": slug, "path": rel, "title": first})
    return out
