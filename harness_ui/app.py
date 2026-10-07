"""FastAPI application factory: API under /api/v1, the built single-page app under /.

Hardening: strict Content-Security-Policy (everything from 'self'; no inline scripts), no-store caching for API
responses, no CORS, frame-ancestors none, request-size limit, error responses without exception text. Uvicorn's
access log records method, path and status only - the API never puts text in URLs.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from harness_ui import __version__
from harness_ui.api import analyze, insight, jobs, system
from harness_ui.audit import AuditLog
from harness_ui.deps import State
from harness_ui.engines import EngineManager
from harness_ui.jobs import JobManager
from harness_ui.security import Authenticator
from harness_ui.settings import UISettings, load

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("harness_ui")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
       "object-src 'none'")


class Hardening(BaseHTTPMiddleware):
    def __init__(self, app, max_body: int) -> None:
        super().__init__(app)
        self.max_body = max_body

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.max_body:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        resp = await call_next(request)
        h = resp.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"
        return resp


def create_app(settings: Optional[UISettings] = None, warm: Optional[bool] = None) -> FastAPI:
    s = settings or load()
    s.check_safe()
    app = FastAPI(title="PHI Engine Console", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json")
    app.state.console = State(settings=s, auth=Authenticator(s), audit=AuditLog(s.path(s.storage.audit_log)),
                              engines=EngineManager(s), jobs=JobManager(s.path(s.storage.jobs_dir),
                                                                         s.limits.max_concurrent_jobs, s.engine_env))
    app.add_middleware(Hardening, max_body=(s.limits.max_upload_mb + 1) * 2 ** 20)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        # never echo the input back (pydantic includes the offending value): location + message only
        errs = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "invalid")} for e in exc.errors()]
        return JSONResponse({"detail": "invalid request", "errors": errs}, status_code=422)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception):
        log.error("unhandled %s on %s", type(exc).__name__, request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    for r in (system.router, analyze.router, jobs.router, insight.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/api/{rest:path}", include_in_schema=False)
    def _api_404(rest: str):
        raise HTTPException(404, "no such endpoint")

    if (STATIC / "index.html").exists():
        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str):
            f = (STATIC / path).resolve()
            if path and f.is_file() and STATIC.resolve() in f.parents:
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {}
                return FileResponse(f, headers=headers)
            return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        def no_build():
            return JSONResponse({"detail": "the web UI has not been built: run scripts/build_ui.sh "
                                           "(the API is available under /api/v1)"}, status_code=503)

    if warm if warm is not None else s.warm_start:
        def _warm():
            try:
                app.state.console.engines.base()
            except Exception:  # noqa: BLE001 - surfaced on /status
                log.error("engine failed to start: %s", app.state.console.engines.error)
        threading.Thread(target=_warm, daemon=True, name="engine-warmup").start()
    return app
