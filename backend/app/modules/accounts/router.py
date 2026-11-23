"""/accounts -- connect social accounts through official OAuth, list, check, disconnect."""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.deps import ACCESS_COOKIE, CurrentUser, DbSession
from app.core.errors import AppError, ValidationFailed
from app.core.security import decode_access_token
from app.modules.accounts import service
from app.modules.accounts.requirements import platform_requirements
from app.modules.accounts.schemas import AccountOut, ConnectUrlOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/accounts", tags=["accounts"])

NONCE_COOKIE = "cf_oauth_nonce"


def _callback_path() -> str:
    return f"{get_settings().api_prefix}/accounts/callback"


@router.get("", response_model=list[AccountOut])
def list_accounts(user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    """Connected accounts of the current user (no tokens)."""
    return [service.account_to_dict(a) for a in service.list_accounts(db, user)]


@router.get("/requirements")
def requirements(user: CurrentUser) -> dict[str, Any]:
    """Per-platform configuration status, scopes, eligibility and review requirements."""
    return platform_requirements()


@router.get("/connect/{platform}", response_model=ConnectUrlOut)
def connect(platform: str, user: CurrentUser, response: Response,
            include_drive: bool = Query(False)) -> ConnectUrlOut:
    """Start OAuth: returns the provider authorization URL and binds the flow to this browser."""
    if platform not in ("youtube", "instagram"):
        raise ValidationFailed("platform must be 'youtube' or 'instagram'")
    start = service.start_connect(user, platform, include_drive=include_drive)
    settings = get_settings()
    response.set_cookie(NONCE_COOKIE, start.nonce, max_age=service.STATE_TTL, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, domain=settings.cookie_domain, path=_callback_path())
    return ConnectUrlOut(authorization_url=start.authorization_url)


def _session_user_id(request: Request) -> str | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return None
    try:
        return str(decode_access_token(token)["sub"])
    except Exception:  # noqa: BLE001 - expired/invalid cookie simply doesn't bind
        return None


@router.get("/callback/{provider}", include_in_schema=False)
def oauth_callback(provider: str, request: Request, db: DbSession, code: str | None = None,
                   state: str | None = None, error: str | None = None,
                   error_description: str | None = None) -> RedirectResponse:
    """Provider redirect target. Always 302s back to the frontend accounts page."""
    settings = get_settings()
    base = f"{settings.frontend_url.rstrip('/')}/app/accounts"
    try:
        platform = service.complete_callback(
            db, provider, code=code, state=state, error=error, error_description=error_description,
            nonce_cookie=request.cookies.get(NONCE_COOKIE), session_user_id=_session_user_id(request))
        target = f"{base}?{urlencode({'connected': platform})}"
    except AppError as exc:
        db.rollback()
        log.info("OAuth callback for %s failed: %s", provider, exc.message)
        target = f"{base}?{urlencode({'error': exc.message[:500], 'provider': provider})}"
    except Exception:
        db.rollback()
        log.exception("Unexpected OAuth callback failure for %s", provider)
        target = f"{base}?{urlencode({'error': 'Unexpected error while connecting the account.'})}"
    resp = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(NONCE_COOKIE, path=_callback_path(), domain=settings.cookie_domain)
    return resp


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(account_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Revoke (best effort), cancel pending publications and delete the account."""
    service.disconnect_account(db, user, account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{account_id}/check", response_model=AccountOut)
def check(account_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Validate/refresh the token and refresh the profile from the platform."""
    return service.account_to_dict(service.check_account(db, user, account_id))
