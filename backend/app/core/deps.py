"""FastAPI dependencies: database session, authenticated user, ownership checks.

Authentication accepts either ``Authorization: Bearer <access_token>`` or the httpOnly
``cf_access`` cookie set by the auth endpoints. Cookie-authenticated unsafe requests must
carry the ``X-Requested-With: ClipFusion`` header (custom header => CORS preflight => CSRF
protection, together with SameSite=Lax cookies).

With ``AUTH_REQUIRED=false`` (the default) there is no login: every request acts as a single
local user. To keep that safe the connection itself must come from this machine (the peer address,
which a client cannot forge, unlike headers), the Host must name this machine (stops DNS rebinding
from a website open in the local browser), and unsafe methods still need the CSRF header (other
websites cannot send it without a CORS preflight, which is refused).
"""

from __future__ import annotations

import ipaddress
import logging
import uuid
from typing import Annotated, TypeVar
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import AuthError, NotFoundError
from app.core.security import decode_access_token
from app.models import User

ACCESS_COOKIE = "cf_access"
REFRESH_COOKIE = "cf_refresh"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "ClipFusion"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_USER_EMAIL = "local-user@clipfusion.invalid"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

log = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]
T = TypeVar("T")


def _extract_token(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    cookie = request.cookies.get(ACCESS_COOKIE)
    return (cookie, True) if cookie else (None, False)


def _system_timezone() -> str:
    try:
        from tzlocal import get_localzone_name

        return get_localzone_name() or "UTC"
    except Exception:  # unknown/odd system configuration
        return "UTC"


def get_local_user(db: Session) -> User:
    """The single user of no-login mode (created on first use)."""
    stmt = select(User).where(User.email == LOCAL_USER_EMAIL)
    user = db.scalar(stmt)
    if user is None:
        user = User(email=LOCAL_USER_EMAIL, full_name="My Studio", timezone=_system_timezone(), settings={})
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # another request created it at the same time
            db.rollback()
            user = db.scalar(stmt)
            assert user is not None
    return user


def _allowed_local_hosts() -> set[str]:
    configured = urlsplit(get_settings().backend_public_url).hostname
    return LOCAL_HOSTS | ({configured.lower()} if configured else set())


def _peer_is_local(request: Request) -> bool:
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    peer = getattr(peer, "ipv4_mapped", None) or peer
    if peer.is_loopback:
        return True
    return any(peer in ipaddress.ip_network(net, strict=False)
               for net in get_settings().no_login_trusted_networks)


def _no_login_user(request: Request, db: Session) -> User:
    host = (request.url.hostname or "").lower()
    if not _peer_is_local(request) or host not in _allowed_local_hosts():
        raise AuthError("Without login this app only answers on this computer (localhost).",
                        code="local_only", status_code=403)
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise AuthError("Missing CSRF header", code="csrf_failed", status_code=403)
    user = get_local_user(db)
    request.state.user_id = str(user.id)
    return user


def get_current_user(request: Request, db: DbSession) -> User:
    if not get_settings().auth_required:
        return _no_login_user(request, db)
    token, from_cookie = _extract_token(request)
    if not token:
        raise AuthError("Not authenticated")
    if from_cookie and request.method not in SAFE_METHODS:
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise AuthError("Missing CSRF header", code="csrf_failed", status_code=403)
    payload = decode_access_token(token)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthError("Invalid token subject") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive")
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_owned(db: Session, model: type[T], obj_id: uuid.UUID, user: User, *, name: str | None = None) -> T:
    """Fetch a row by id that belongs to ``user`` (``model.user_id``) or raise 404.

    404 (not 403) avoids leaking the existence of other users' objects."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "user_id", None) != user.id:
        raise NotFoundError(f"{name or model.__name__} not found")
    return obj
