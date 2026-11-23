"""FastAPI application factory: middleware, routers (all under ``settings.api_prefix``), lifespan."""

from __future__ import annotations

import importlib
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from limits import RateLimitItem, parse_many
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import AppError, register_exception_handlers
from app.core.logging import configure_logging, request_id_var
from app.core.ratelimit import install_rate_limiting, limiter
from app.core.security import get_secret_key

log = logging.getLogger(__name__)

AUTH_ROUTER = "app.modules.auth.router"  # mounted only when settings.auth_required
ROUTER_MODULES: list[str] = [
    "app.modules.system.router",
    AUTH_ROUTER,
    "app.modules.users.router",
    "app.modules.uploads.router",
    "app.modules.sources.router",
    "app.modules.jobs.router",
    "app.modules.clips.router",
    "app.modules.framing.router",
    "app.modules.rendering.router",
    "app.modules.assets.router",
    "app.modules.notifications.router",
    "app.modules.dashboard.router",
    "app.modules.media.router",
    "app.modules.accounts.router",
    "app.modules.publishing.router",
    "app.modules.scheduling.router",
    "app.modules.analytics.router",
    "app.modules.upload_runs.router",
    "app.modules.storage.router",
]
ALLOWED_HEADERS = ["Authorization", "Content-Type", "X-Requested-With", "Range", "Content-Range", "X-Request-ID"]
EXPOSED_HEADERS = ["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition", "X-Request-ID"]
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                    "Referrer-Policy": "strict-origin-when-cross-origin"}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
# Not subject to the default limit: signed media (a player scrubbing a video issues many small
# range requests) and orchestrator probes.
DEFAULT_LIMIT_EXEMPT = {"app.modules.media.router.get_media", "app.modules.system.router.health",
                        "app.modules.system.router.ready"}


@lru_cache(maxsize=8)
def _parse_limits(spec: str) -> tuple[RateLimitItem, ...]:
    return tuple(parse_many(spec))


def enforce_default_rate_limit(request: Request) -> None:
    """Apply ``settings.rate_limit_default`` per client and endpoint (429 ``rate_limited``).

    ``SlowAPIMiddleware`` resolves handlers through ``app.routes``; FastAPI wraps included
    routers in objects without ``.endpoint``, so it treats every route without its own
    ``@limiter.limit`` as exempt. This router-level dependency runs after routing, where the
    endpoint is known. A rate-limit storage outage is logged and does not block the request
    (the stricter per-route limits on the auth endpoints still apply)."""
    if not limiter.enabled:
        return
    endpoint = request.scope.get("endpoint")
    name = f"{endpoint.__module__}.{endpoint.__name__}" if endpoint is not None else request.url.path
    if name in DEFAULT_LIMIT_EXEMPT or name in getattr(limiter, "_route_limits", {}):
        return
    key = limiter._key_func(request)
    for item in _parse_limits(get_settings().rate_limit_default):
        try:
            allowed = limiter.limiter.hit(item, key, f"default:{name}")
        except Exception:
            log.exception("Rate-limit storage unavailable; request allowed")
            return
        if not allowed:
            raise AppError(f"Too many requests: {item}", code="rate_limited", status_code=429)


class RequestContextMiddleware:
    """Pure-ASGI middleware: X-Request-ID correlation (logs) and security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = rid
                for k, v in SECURITY_HEADERS.items():
                    if k not in headers:
                        headers[k] = v
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


def load_routers() -> list[APIRouter]:
    """Import every router module (the auth router only when ``settings.auth_required``).
    In ENVIRONMENT=test a module that does not exist yet is skipped."""
    settings = get_settings()
    routers = []
    for name in ROUTER_MODULES:
        if name == AUTH_ROUTER and not settings.auth_required:
            continue  # no-login mode: there is nothing to sign in to
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if settings.environment == "test" and exc.name == name:
                log.warning("Router module %s not found; skipped (test environment only)", name)
                continue
            raise
        routers.append(module.router)
    return routers


def create_app(*, start_workers: bool | None = None) -> FastAPI:
    """Build the application. ``start_workers`` (default: WORKER_MODE=local outside tests)
    starts the in-process executor's periodic loop and recovers interrupted jobs."""
    settings = get_settings()
    run_workers = (settings.worker_mode == "local" and settings.environment != "test") \
        if start_workers is None else start_workers

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        get_secret_key()  # refuses to start in production without SECRET_KEY
        started = False
        if run_workers:
            from app.workers.local import get_local_executor
            from app.workers.tasks_maintenance import recover_jobs

            get_local_executor().start_periodic()
            started = True
            try:
                recover_jobs()
            except Exception:
                log.exception("Job recovery at startup failed")
            log.info("Local worker executor started")
        try:
            yield
        finally:
            if started:
                from app.workers.local import shutdown_local_executor

                shutdown_local_executor()

    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan,
                  openapi_url=f"{settings.api_prefix}/openapi.json", docs_url=f"{settings.api_prefix}/docs",
                  redoc_url=None)
    register_exception_handlers(app)
    install_rate_limiting(app)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
                       allow_headers=ALLOWED_HEADERS, expose_headers=EXPOSED_HEADERS, max_age=600)
    for router in load_routers():
        app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(enforce_default_rate_limit)])
    return app


app = create_app()
