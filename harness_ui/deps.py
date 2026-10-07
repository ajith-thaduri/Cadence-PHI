"""Shared application state and FastAPI dependencies (authentication + role checks)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request

from harness_ui.audit import AuditLog
from harness_ui.engines import EngineManager
from harness_ui.jobs import JobManager
from harness_ui.security import Authenticator, Principal
from harness_ui.settings import UISettings


@dataclass
class State:
    settings: UISettings
    auth: Authenticator
    audit: AuditLog
    engines: EngineManager
    jobs: JobManager


def state(request: Request) -> State:
    return request.app.state.console


def current(request: Request, st: State = Depends(state)) -> Principal:
    p = st.auth.principal(request)
    if p is None:
        raise HTTPException(401, "sign in required")
    return p


def require(role_key: str) -> Callable[..., Principal]:
    """role_key: a field of settings.roles (analyze, batch, run_jobs, ...) or a literal role name."""
    def dep(p: Principal = Depends(current), st: State = Depends(state)) -> Principal:
        needed = getattr(st.settings.roles, role_key, role_key)
        if not p.allows(needed):
            raise HTTPException(403, f"this action needs the {needed} role")
        return p
    return dep
