"""/auth — register, login, refresh (rotation), logout, me, Sign in with Google."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.deps import REFRESH_COOKIE, CurrentUser, DbSession
from app.core.errors import AppError
from app.core.ratelimit import limiter
from app.modules.auth import service
from app.modules.auth.schemas import AuthorizationUrlOut, AuthOut, LoginIn, RefreshIn, RegisterIn
from app.modules.users.schemas import UserOut
from app.modules.users.service import user_out

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
_AUTH_LIMIT = get_settings().rate_limit_auth


def _auth_out(response: Response, user, pair: service.TokenPair) -> AuthOut:  # noqa: ANN001
    service.set_auth_cookies(response, pair)
    return AuthOut(access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in,
                   user=user_out(user))


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(_AUTH_LIMIT)
def register(request: Request, response: Response, body: RegisterIn, db: DbSession) -> AuthOut:
    """Create an account (password >= 10 chars, mixed classes) and sign in."""
    user, pair = service.register(db, body, request)
    return _auth_out(response, user, pair)


@router.post("/login", response_model=AuthOut)
@limiter.limit(_AUTH_LIMIT)
def login(request: Request, response: Response, body: LoginIn, db: DbSession) -> AuthOut:
    """Password sign-in. Sets the auth cookies and returns the tokens."""
    user, pair = service.login(db, body, request)
    return _auth_out(response, user, pair)


@router.post("/refresh", response_model=AuthOut)
@limiter.limit(_AUTH_LIMIT)
def refresh(request: Request, response: Response, db: DbSession, body: RefreshIn | None = None) -> AuthOut:
    """Rotate the refresh token (body or ``cf_refresh`` cookie) and issue a new access token."""
    raw = service.refresh_token_from(request, body.refresh_token if body else None)
    user, pair = service.rotate_refresh_token(db, raw, request)
    return _auth_out(response, user, pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: DbSession, body: RefreshIn | None = None) -> Response:
    """Revoke the refresh-token family and clear the cookies (idempotent)."""
    # No token at all is fine (already signed out); a cookie without the CSRF header is refused
    # (403) rather than reporting a logout that did not revoke anything.
    body_token = body.refresh_token if body else None
    raw = None
    if body_token or request.cookies.get(REFRESH_COOKIE):
        raw = service.refresh_token_from(request, body_token)
    service.revoke_refresh_token(db, raw)
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    service.clear_auth_cookies(resp)
    return resp


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """The authenticated user."""
    return user_out(user)


@router.get("/google/login", response_model=AuthorizationUrlOut)
@limiter.limit(_AUTH_LIMIT)
def google_login(request: Request, response: Response) -> AuthorizationUrlOut:
    """Start Sign in with Google (503 ``integration_not_configured`` without Google credentials)."""
    url, nonce = service.start_google_signin()
    s = get_settings()
    response.set_cookie(service.GOOGLE_NONCE_COOKIE, nonce, max_age=service.GOOGLE_STATE_TTL, httponly=True,
                        samesite="lax", secure=s.cookie_secure, domain=s.cookie_domain,
                        path=service.google_cookie_path())
    return AuthorizationUrlOut(authorization_url=url)


@router.get("/google/callback", include_in_schema=False)
def google_callback(request: Request, db: DbSession, code: str | None = None, state: str | None = None,
                    error: str | None = None) -> RedirectResponse:
    """Google redirect target: signs the user in and 302s to the app (or the login page with an error)."""
    s = get_settings()
    front = s.frontend_url.rstrip("/")
    try:
        _user, pair = service.complete_google_signin(
            db, code=code, state=state, error=error, nonce_cookie=request.cookies.get(service.GOOGLE_NONCE_COOKIE),
            request=request)
        resp = RedirectResponse(f"{front}/app", status_code=status.HTTP_302_FOUND)
        service.set_auth_cookies(resp, pair)
    except AppError as exc:
        db.rollback()
        log.info("Google sign-in failed: %s", exc.message)
        resp = RedirectResponse(f"{front}/login?{urlencode({'error': exc.message[:300]})}",
                                status_code=status.HTTP_302_FOUND)
    except Exception:
        db.rollback()
        log.exception("Unexpected Google sign-in failure")
        resp = RedirectResponse(f"{front}/login?{urlencode({'error': 'Google sign-in failed unexpectedly.'})}",
                                status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(service.GOOGLE_NONCE_COOKIE, path=service.google_cookie_path(), domain=s.cookie_domain)
    return resp
