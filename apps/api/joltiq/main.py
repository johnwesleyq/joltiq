from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .core.context import new_request_id, request_id_var
from .core.errors import AppError
from .core.logging import configure_logging

log = logging.getLogger("joltiq.api")


def _err(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details,
                                                               "request_id": request_id_var.get()}})


def create_app() -> FastAPI:
    configure_logging()
    s = get_settings()
    app = FastAPI(title=f"{s.product_name} API", version="1.0.0", openapi_url="/api/v1/openapi.json",
                  docs_url="/api/v1/docs", redoc_url=None)

    app.add_middleware(
        CORSMiddleware, allow_origins=[s.web_origin], allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-csrf-token", "authorization", "idempotency-key", "x-request-id", "x-joltiq-client"],
        expose_headers=["x-request-id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        if len(rid) > 64 or not rid.replace("-", "").isalnum():
            rid = new_request_id()
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            request_id_var.reset(token)
            raise
        dur = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = rid
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        response.headers["cache-control"] = response.headers.get("cache-control", "no-store")
        if s.cookie_secure:
            response.headers["strict-transport-security"] = "max-age=63072000; includeSubDomains"
        log.info("request", extra={"rid": rid, "method": request.method, "path": request.url.path,
                                   "status": response.status_code, "ms": round(dur, 1)})
        from .services import metrics

        metrics.observe_request(request.method, getattr(request.scope.get("route"), "path", "unmatched"),
                                response.status_code, dur)
        request_id_var.reset(token)
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return _err(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _err(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return _err(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return _err(500, "internal_error", "An unexpected error occurred. Reference the request id when contacting support.")

    @app.get("/healthz", tags=["ops"])
    def healthz():
        return {"status": "ok", "service": "api"}

    @app.get("/readyz", tags=["ops"])
    def readyz():
        from .db import engine

        checks = {}
        try:
            with engine().connect() as c:
                c.execute(text("select 1"))
                rev = c.execute(text("select version_num from alembic_version")).scalar()
            checks["database"] = {"ok": True, "migration": rev}
        except Exception as e:  # noqa: BLE001
            checks["database"] = {"ok": False, "error": type(e).__name__}
        from .security.ratelimit import _get_redis

        checks["redis"] = {"ok": _get_redis() is not None, "required": False}
        ok = checks["database"]["ok"]
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready", "checks": checks})

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def prom_metrics():
        from fastapi.responses import PlainTextResponse

        from .services import metrics

        return PlainTextResponse(metrics.render())

    from .routers import ALL_ROUTERS

    for r in ALL_ROUTERS:
        app.include_router(r, prefix="/api/v1")

    from .core.telemetry import setup_tracing

    setup_tracing(app)
    return app


app = create_app()
