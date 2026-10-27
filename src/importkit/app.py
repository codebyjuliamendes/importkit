"""Application factory: wires settings, database, middleware, errors and static UI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from importkit import __version__, migrate
from importkit.api.middleware import BodyLimitMiddleware, EdgeMiddleware, RateLimitMiddleware
from importkit.api.routes import router
from importkit.config import Settings, get_settings
from importkit.db import create_db_engine, create_session_factory
from importkit.domain.errors import ImportRejectedError
from importkit.logs import configure_logging, get_logger
from importkit.services import ConflictError, NotFoundError

log = get_logger("importkit")


def _error(request: Request, status: int, message: str, code: str, **extra: Any) -> JSONResponse:
    payload: dict[str, Any] = {"error": message, "code": code, **extra}
    request_id = getattr(request.state, "request_id", None) or request.scope.get("state", {}).get(
        "request_id"
    )
    if request_id:
        payload["requestId"] = request_id
    return JSONResponse(payload, status_code=status)


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(ImportRejectedError)
    async def _rejected(request: Request, exc: ImportRejectedError) -> JSONResponse:
        return _error(request, exc.status, exc.message, exc.code)

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return _error(request, 404, str(exc), "not_found")

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError) -> JSONResponse:
        return _error(request, 409, str(exc), "conflict")

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": [str(p) for p in e.get("loc", ())], "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return _error(request, 400, "The request is invalid.", "invalid_request", details=details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 413: "request_too_large"}
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404 and message == "Not Found":
            message = "Not found."
        response = _error(request, exc.status_code, message, codes.get(exc.status_code, "error"))
        for key, value in (exc.headers or {}).items():
            response.headers[key] = value
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_migrate:
            migrate.upgrade(settings.database_url)
        engine = create_db_engine(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        log.info("startup", version=__version__, environment=settings.environment)
        try:
            yield
        finally:
            engine.dispose()
            log.info("shutdown")

    app = FastAPI(
        title="ImportKit API",
        version=__version__,
        summary="Map, validate, preview, commit and revert CSV contact imports.",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )
    app.state.settings = settings
    _register_errors(app)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")

    # Added innermost first; EdgeMiddleware ends up outermost.
    app.add_middleware(
        RateLimitMiddleware,
        limit=settings.rate_limit_writes,
        window_seconds=settings.rate_limit_window_seconds,
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
            max_age=600,
        )
    app.add_middleware(
        EdgeMiddleware,
        frame_ancestors=settings.frame_ancestors,
        hsts=settings.environment == "production",
    )
    return app
