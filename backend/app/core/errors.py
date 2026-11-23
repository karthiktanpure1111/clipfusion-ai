"""Domain exceptions and their HTTP mapping.

Raise these from services; ``register_exception_handlers`` turns them into JSON errors
of the shape ``{"error": {"code": str, "message": str, "details": any}}``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None,
                 status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PermissionDenied(AppError):
    status_code = 403
    code = "forbidden"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class IntegrationNotConfigured(AppError):
    """An external integration lacks credentials/approval. Message explains what's needed."""

    status_code = 503
    code = "integration_not_configured"


class ExternalServiceError(AppError):
    status_code = 502
    code = "external_service_error"


class QuotaExceeded(AppError):
    status_code = 429
    code = "quota_exceeded"


class DependencyMissing(AppError):
    """A local processing dependency (ffmpeg, model files, ...) is unavailable."""

    status_code = 503
    code = "dependency_missing"


def _payload(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_payload(exc.code, exc.message, exc.details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                429: "rate_limited"}.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=_payload(code, str(exc.detail)),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content=_payload("validation_failed", "Invalid request", errors))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=_payload("internal_error", "An unexpected error occurred"))
