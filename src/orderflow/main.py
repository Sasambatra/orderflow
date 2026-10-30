import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from orderflow.api.dependencies import AdminActor
from orderflow.api.routes.api import router
from orderflow.core.logging import configure_logging, error_body, log_event, request_id
from orderflow.core.metrics import DB_ERRORS, HTTP_DURATION, HTTP_REQUESTS
from orderflow.db.session import session_factory
from orderflow.domain.errors import DomainError


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    yield


class RequestContext(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = request_id.set(str(uuid.uuid4()))
        started = time.monotonic()
        try:
            try:
                response = await call_next(request)
            except Exception as error:
                logging.getLogger("orderflow").error(
                    "unhandled_error request_id=%s type=%s", request_id.get(), type(error).__name__
                )
                response = JSONResponse(
                    error_body("internal_error", "An unexpected error occurred"), status_code=500
                )
            route = getattr(request.scope.get("route"), "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
            HTTP_DURATION.labels(request.method, route).observe(time.monotonic() - started)
            response.headers["X-Request-ID"] = request_id.get()
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            log_event(
                "http_request",
                request_id=request_id.get(),
                method=request.method,
                route=route,
                status=response.status_code,
            )
            return response
        finally:
            request_id.reset(token)


class BodyLimit:
    """Bound received bytes even when Content-Length is absent or incorrect."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 65536:
                response = JSONResponse(
                    error_body("body_too_large", "Request body exceeds 64 KiB"), status_code=413
                )
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


def create_app() -> FastAPI:
    app = FastAPI(
        title="OrderFlow",
        version="0.1.0",
        description="Transactional inventory reservations and idempotent mock payments.",
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimit)
    app.add_middleware(RequestContext)
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return JSONResponse(
            error_body(error.code, error.message, error.details), status_code=error.status
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Never include rejected input: it may contain a password or token.
        fields = [
            {"field": ".".join(map(str, item["loc"])), "type": item["type"]}
            for item in error.errors()
        ]
        return JSONResponse(
            error_body("validation_error", "Request validation failed", {"fields": fields}),
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse(
            error_body("http_error", str(error.detail)), status_code=error.status_code
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        DB_ERRORS.inc()
        logging.getLogger("orderflow").error(
            "database_error request_id=%s type=%s", request_id.get(), type(error).__name__
        )
        return JSONResponse(
            error_body(
                "database_unavailable", "Database temporarily unavailable; retry with the same key"
            ),
            status_code=503,
        )

    @app.get("/health/live", tags=["Operations"])
    def live():
        return {"status": "alive"}

    @app.get("/health/ready", tags=["Operations"])
    def ready():
        with session_factory()() as session:
            version = session.scalar(text("SELECT version_num FROM alembic_version"))
            if version != "0001_initial":
                raise DomainError("schema_not_ready", "Database migration required", 503)
        return {"status": "ready", "schema": version}

    @app.get("/metrics", tags=["Operations"])
    def metrics(actor: AdminActor):
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
