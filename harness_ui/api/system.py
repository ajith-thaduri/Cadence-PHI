"""Health, status, session and audit endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from harness.phi import provenance, telemetry
from harness_ui import __version__
from harness_ui.deps import State, current, require, state
from harness_ui.schemas import LoginIn
from harness_ui.security import COOKIE, Principal

router = APIRouter()


@router.get("/healthz", tags=["system"])
def healthz(st: State = Depends(state)) -> dict[str, Any]:
    """Liveness / readiness (no authentication, no details)."""
    return {"status": "ready" if st.engines.ready else ("error" if st.engines.error else "starting")}


@router.get("/session", tags=["system"])
def session(request: Request, st: State = Depends(state)) -> dict[str, Any]:
    p = st.auth.principal(request)
    return {"auth_mode": st.settings.auth.mode, "user": {"name": p.name, "role": p.role} if p else None}


@router.post("/session", tags=["system"])
def login(body: LoginIn, request: Request, response: Response, st: State = Depends(state)) -> dict[str, Any]:
    client = request.client.host if request.client else "unknown"
    try:
        p = st.auth.login(client, body.username, body.secret)
    except Exception:
        st.audit.record(body.username[:64] or "?", "-", "login", ok=False)
        raise
    response.set_cookie(COOKIE, st.auth.issue(p), httponly=True, samesite="strict", secure=st.auth.cookie_secure(request),
                        max_age=int(st.settings.auth.session_hours * 3600), path="/")
    st.audit.record(p.name, p.role, "login")
    return {"user": {"name": p.name, "role": p.role}}


@router.delete("/session", tags=["system"])
def logout(response: Response, p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    response.delete_cookie(COOKIE, path="/")
    st.audit.record(p.name, p.role, "logout")
    return {"ok": True}


@router.get("/status", tags=["system"])
def status(p: Principal = Depends(current), st: State = Depends(state)) -> dict[str, Any]:
    """Everything the overview page needs: engine state, provenance, permissions, limits."""
    cfg = st.engines.base_cfg
    eng = st.engines._base
    roles = st.settings.roles.model_dump()
    return {
        "console_version": __version__,
        "engine": {"ready": st.engines.ready, "error": st.engines.error, "environment": cfg.raw.get("environment"),
                   "config_sha256": cfg.sha256(), "catalog_version": cfg.raw.get("catalog_version"),
                   "device": st.engines._device, "models": cfg.enabled_models,
                   "weights_verified": dict(eng.weight_hashes) if eng else {},
                   "self_test_required": bool(((cfg.raw.get("failure") or {}).get("startup_self_test") or {}).get("required")),
                   "consistency_scope": (cfg.raw.get("redaction") or {}).get("consistency_scope"),
                   "vault_backend": ((cfg.raw.get("redaction") or {}).get("vault") or {}).get("backend"),
                   "outbound_guard": bool((cfg.raw.get("outbound_guard") or {}).get("enabled")),
                   "code_commit": provenance.code_commit(), "code_fingerprint": provenance.code_fingerprint()},
        "user": {"name": p.name, "role": p.role},
        "auth_mode": st.settings.auth.mode,
        "permissions": {k: p.allows(v) for k, v in roles.items()},
        "features": {"what_if": st.settings.features.what_if,
                     "restore": st.settings.features.restore and p.allows(st.settings.roles.restore)},
        "limits": st.settings.limits.model_dump(),
    }


@router.get("/metrics", tags=["system"])
def metrics(p: Principal = Depends(current)) -> dict[str, Any]:
    return {"counters": telemetry.snapshot()}


@router.get("/audit", tags=["system"])
def audit(limit: int = 300, p: Principal = Depends(require("view_audit")), st: State = Depends(state)) -> list[dict[str, Any]]:
    return st.audit.tail(min(max(limit, 1), 5000))


@router.post("/engine/reload", tags=["system"])
def reload(p: Principal = Depends(require("admin")), st: State = Depends(state)) -> dict[str, Any]:
    """Re-read configs/phi after a reviewed configuration change has been deployed."""
    st.engines.reload()
    st.audit.record(p.name, p.role, "engine_reload", config_sha256=st.engines.base_cfg.sha256())
    return {"config_sha256": st.engines.base_cfg.sha256()}
