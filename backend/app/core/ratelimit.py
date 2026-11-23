"""Rate limiting (slowapi). Keys by authenticated user id when available, else client IP.

Storage: Redis in celery mode (shared across API replicas), in-memory otherwise.
Usage in routers::

    from app.core.ratelimit import limiter
    @router.post("/login")
    @limiter.limit(get_settings().rate_limit_auth)
    def login(request: Request, ...): ...

Note: slowapi requires the endpoint to accept a ``request: Request`` parameter.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.config import get_settings


def _key(request: Request) -> str:
    uid = getattr(request.state, "user_id", None)
    if uid:
        return f"user:{uid}"
    # Fall back to the access token subject without verifying (verification happens later).
    return f"ip:{get_remote_address(request)}"


def _storage_uri() -> str:
    s = get_settings()
    if s.rate_limit_storage_uri:
        return s.rate_limit_storage_uri
    if s.worker_mode == "celery" and s.environment != "test":
        return s.redis_url
    return "memory://"


limiter = Limiter(
    key_func=_key,
    default_limits=[get_settings().rate_limit_default],
    storage_uri=_storage_uri(),
    enabled=get_settings().environment != "test",
    headers_enabled=False,
)


def install_rate_limiting(app: FastAPI) -> None:
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(status_code=429, content={"error": {
            "code": "rate_limited", "message": f"Too many requests: {exc.detail}", "details": None}})

    app.add_middleware(SlowAPIMiddleware)
