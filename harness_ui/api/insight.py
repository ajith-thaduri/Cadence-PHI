"""Read-only views: evaluation runs, ledger, gates, configuration, monitoring, documentation."""
from __future__ import annotations

import json
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException

from harness.paths import ROOT
from harness.phi import telemetry
from harness.phi.catalog import Catalog
from harness.phi.config import HarnessConfig
from harness.phi.monitoring import check_rates, run_canaries
from harness_ui import results
from harness_ui.deps import State, current, require, state
from harness_ui.security import Principal

router = APIRouter()


# ---------------------------------------------------------------------------- runs
@router.get("/runs", tags=["results"])
def runs(kind: Optional[str] = None, p: Principal = Depends(current)) -> list[dict[str, Any]]:
    if kind and kind not in results.KINDS:
        raise HTTPException(422, "unknown kind")
    return results.list_runs(kind)


@router.get("/runs/{kind}/{name}", tags=["results"])
def run(kind: str, name: str, p: Principal = Depends(current)) -> dict[str, Any]:
    try:
        return results.run_detail(kind, name)
    except FileNotFoundError:
        raise HTTPException(404, "no such run") from None


@router.get("/ledger", tags=["results"])
def ledger(limit: int = 300, p: Principal = Depends(current)) -> list[dict[str, Any]]:
    return results.ledger(min(max(limit, 1), 5000))


@router.get("/gates/latest", tags=["results"])
def gates_latest(p: Principal = Depends(current)) -> Optional[dict[str, Any]]:
    d = results.latest("gates")
    if d is None:
        return None
    defs = yaml.safe_load((ROOT / "configs" / "eval" / "gates.yaml").read_text())
    d["definitions"] = {k: {"name": v.get("name"), "metric": v.get("metric"), "evidence": v.get("evidence")}
                        for k, v in defs["gates"].items()}
    d["gates_status"] = defs.get("status")
    return d


# ---------------------------------------------------------------------------- configuration (read-only)
@router.get("/config", tags=["config"])
def config(p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    cfg = st.engines.base_cfg
    cat = Catalog(cfg)
    raw = cfg.raw
    ents = []
    for e in cfg.entity_ids:
        spec = raw["entities"][e]
        info = cat[e]
        ents.append({"id": e, "description": spec.get("description"), "category": info.category, "risk_tier": info.risk_tier,
                     "enabled": bool(spec.get("enabled", True)), "safe_harbor": list(info.hipaa_safe_harbor),
                     "default_action": info.default_action, "outbound_guard": info.outbound_guard,
                     "subtypes": list(info.subtypes), "high_risk": info.high_risk})
    recs = []
    for rid, s in raw["recognizers"].items():
        kind = "pattern" if s.get("type") == "pattern" else ("registry" if "kinds" in s else
                ("contextual" if s.get("contextual") else ("presidio" if "presidio_class" in s else "rule")))
        recs.append({"id": rid, "kind": kind, "enabled": cfg.recognizer_enabled(rid), "mode": s.get("mode", "production"),
                     "version": str(s.get("version", "1")), "developed_on": s.get("developed_on") or [],
                     "entities": s.get("emits") or ([s["entity"]] if s.get("entity") else list((s.get("entity_map") or {}).values()))})
    models_meta = (yaml.safe_load((ROOT / "configs" / "models.yaml").read_text()) or {}).get("models", {})
    models = [{"id": k, "enabled": bool(s.get("enabled")), "mode": s.get("mode", "production"),
               "candidate_floor": s.get("candidate_floor"), "weights_pinned": bool(s.get("weights_sha256")),
               "model_id": (models_meta.get(k) or {}).get("model_id"),
               "revision": str((models_meta.get(k) or {}).get("revision", ""))[:12]} for k, s in raw["models"].items()]
    red = raw.get("redaction") or {}
    vault = dict(red.get("vault") or {})
    return {
        "describe": cfg.describe(), "entities": ents, "recognizers": recs, "models": models,
        "aliases": raw.get("aliases", {}), "normalization_profiles": raw.get("normalization_profiles", {}),
        "policy_profiles": list(raw.get("policy_profiles", {})), "thresholds": raw.get("thresholds", {}),
        "redaction": {k: v for k, v in red.items() if k != "vault"} | {"vault": vault},
        "failure": {k: v for k, v in (raw.get("failure") or {}).items() if k != "startup_self_test"},
        "outbound_guard": raw.get("outbound_guard", {}), "type_refinements": raw.get("type_refinements", {}),
        "operators": ["TOKENIZE", "REDACT", "PSEUDONYMIZE", "ENCRYPT"],
    }


@router.get("/config/resolved", tags=["config"])
def resolved(p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    raw = {k: v for k, v in st.engines.base_cfg.raw.items() if not k.startswith("_")}
    return {"yaml": yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=120)}


@router.post("/config/validate", tags=["config"])
def validate(p: Principal = Depends(current)) -> list[dict[str, Any]]:
    out = []
    for env in ("base", "dev", "test", "prod"):
        try:
            c = HarnessConfig.load(None, env=env)
            c.validate()
            out.append({"environment": env, "ok": True, "config_sha256": c.sha256(), "message": "valid"})
        except Exception as e:  # noqa: BLE001 - configuration error text (no PHI)
            out.append({"environment": env, "ok": False, "config_sha256": None, "message": str(e)[:2000]})
    return out


# ---------------------------------------------------------------------------- monitoring
@router.get("/monitoring", tags=["monitoring"])
def monitoring(p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    cfg = st.engines.base_cfg
    mon = cfg.raw.get("monitoring") or {}
    dr = mon.get("detection_rate") or {}
    base_p = ROOT / dr.get("baseline", "results/monitoring/baseline.json")
    baseline = json.loads(base_p.read_text()) if base_p.exists() else None
    snap = telemetry.snapshot()
    alerts = check_rates(baseline, snap, dr.get("control_limit_sigma", 3.0), dr.get("min_chars_per_window", 20000)) if baseline else []
    return {"baseline": {k: baseline.get(k) for k in ("created", "config_sha256", "datasets", "documents", "chars",
                                                       "rates_per_1k_chars")} if baseline else None,
            "counters": snap, "alerts": alerts,
            "canaries": [{"id": c.get("id"), "entities": c.get("entities", []), "negative": bool(c.get("must_not_redact"))}
                         for c in (mon.get("canaries") or {}).get("cases", [])],
            "settings": {"interval_seconds": (mon.get("canaries") or {}).get("interval_seconds"), **dr}}


@router.post("/monitoring/canaries", tags=["monitoring"])
def canaries(p: Principal = Depends(require("analyze")), st: State = Depends(state)) -> dict[str, Any]:
    eng = st.engines.base()
    with st.engines.using(eng):
        rep = run_canaries(eng)
    st.audit.record(p.name, p.role, "canaries", passed=rep["passed"], cases=rep["cases"])
    return rep


# ---------------------------------------------------------------------------- documentation
@router.get("/docs-index", tags=["docs"])
def docs_index(p: Principal = Depends(current)) -> list[dict[str, str]]:
    return results.doc_list()


@router.get("/docs-page/{slug}", tags=["docs"])
def docs_page(slug: str, p: Principal = Depends(current)) -> dict[str, str]:
    try:
        path, text = results.doc(slug)
    except FileNotFoundError:
        raise HTTPException(404, "no such document") from None
    return {"slug": slug, "path": path, "markdown": text}
