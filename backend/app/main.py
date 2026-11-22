"""Switchyard – SSO-protected control plane for vendor MCP endpoints and multi-endpoint workflows."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import get_settings
from .db import create_all, session_scope
from .mcp.client import McpError
from .routes import access, auth, endpoints, workflows
from .seed import seed
from .workflows.engine import recover_interrupted

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("switchyard")

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "switchyard"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.validate_for_production()
    create_all()
    with session_scope() as db:
        seed(db)
    if n := recover_interrupted():
        log.warning("marked %s interrupted run(s) as failed", n)
    log.info("Switchyard %s ready (auth=%s, env=%s)", __version__, s.auth_mode, s.environment)
    yield
    await asyncio.sleep(0)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Switchyard", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if not s.is_production else None, openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def security(request: Request, call_next):
        # CSRF: state-changing API calls must carry a custom header, which browsers will not add
        # cross-site without a CORS preflight (and we allow no cross-origin access).
        if (request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE")
                and request.headers.get(CSRF_HEADER) != CSRF_VALUE):
            return JSONResponse({"detail": "Missing CSRF header"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if s.is_production:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, session_cookie="switchyard_session",
                       max_age=s.session_max_age_s, same_site="lax", https_only=s.is_production)

    @app.exception_handler(McpError)
    async def mcp_error(_: Request, exc: McpError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz():
        from sqlalchemy import text

        with session_scope() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(auth.router)
    app.include_router(endpoints.router)
    app.include_router(workflows.router)
    app.include_router(access.router)

    if s.static_dir and Path(s.static_dir).is_dir():
        static = Path(s.static_dir).resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "auth/")):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            target = (static / path).resolve()
            if path and target.is_file() and static in target.parents:
                return FileResponse(target)
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
