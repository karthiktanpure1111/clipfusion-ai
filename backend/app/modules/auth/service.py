"""Registration, login, rotating refresh tokens (family revocation on reuse), cookies,
and Sign in with Google (OpenID Connect, PKCE)."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import is_postgres, utcnow
from app.core.deps import ACCESS_COOKIE, CSRF_HEADER, CSRF_VALUE, REFRESH_COOKIE
from app.core.errors import AuthError, ConflictError, ValidationFailed
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    sign_payload,
    validate_password_strength,
    verify_password,
    verify_signed_payload,
)
from app.models import RefreshToken, User
from app.modules.auth.schemas import LoginIn, RegisterIn
from app.modules.users.service import validate_timezone

log = logging.getLogger(__name__)

GOOGLE_STATE_PURPOSE = "google_signin"
GOOGLE_STATE_TTL = 600
GOOGLE_NONCE_COOKIE = "cf_google_nonce"


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


# ------------------------------------------------------------------ cookies
def _auth_path() -> str:
    return f"{get_settings().api_prefix}/auth"


def set_auth_cookies(response: Response, tokens: TokenPair) -> None:
    """Set the httpOnly ``cf_access`` (path /) and ``cf_refresh`` (path /api/v1/auth) cookies."""
    s = get_settings()
    response.set_cookie(ACCESS_COOKIE, tokens.access_token, max_age=s.access_token_ttl_minutes * 60,
                        path="/", httponly=True, samesite="lax", secure=s.cookie_secure, domain=s.cookie_domain)
    response.set_cookie(REFRESH_COOKIE, tokens.refresh_token, max_age=s.refresh_token_ttl_days * 86400,
                        path=_auth_path(), httponly=True, samesite="lax", secure=s.cookie_secure,
                        domain=s.cookie_domain)


def clear_auth_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(ACCESS_COOKIE, path="/", domain=s.cookie_domain, secure=s.cookie_secure,
                           httponly=True, samesite="lax")
    response.delete_cookie(REFRESH_COOKIE, path=_auth_path(), domain=s.cookie_domain, secure=s.cookie_secure,
                           httponly=True, samesite="lax")


def refresh_token_from(request: Request, body_token: str | None) -> str:
    """The refresh token from the body, else from the cookie (cookie use requires the CSRF header)."""
    if body_token:
        return body_token
    cookie = request.cookies.get(REFRESH_COOKIE)
    if not cookie:
        raise AuthError("No refresh token", code="unauthorized")
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise AuthError("Missing CSRF header", code="csrf_failed", status_code=403)
    return cookie


# ------------------------------------------------------------------- tokens
def _client_meta(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ua = (request.headers.get("user-agent") or "")[:400] or None
    ip = request.client.host if request.client else None
    return ua, ip


def issue_tokens(db: Session, user: User, request: Request | None = None, *,
                 family_id: uuid.UUID | None = None) -> tuple[TokenPair, RefreshToken]:
    """Create an access token and a new refresh token (in ``family_id`` or a new family)."""
    s = get_settings()
    raw, digest = new_refresh_token()
    ua, ip = _client_meta(request)
    row = RefreshToken(user_id=user.id, token_hash=digest, family_id=family_id or uuid.uuid4(),
                       expires_at=utcnow() + timedelta(days=s.refresh_token_ttl_days), user_agent=ua, ip_address=ip)
    db.add(row)
    db.flush()
    pair = TokenPair(access_token=create_access_token(user.id), refresh_token=raw,
                     expires_in=s.access_token_ttl_minutes * 60)
    return pair, row


def _revoke_family(db: Session, family_id: uuid.UUID) -> int:
    res = db.execute(update(RefreshToken).where(RefreshToken.family_id == family_id,
                                                RefreshToken.revoked_at.is_(None)).values(revoked_at=utcnow()))
    return int(res.rowcount or 0)


def rotate_refresh_token(db: Session, raw: str, request: Request | None = None) -> tuple[User, TokenPair]:
    """Exchange a refresh token for a new pair. Presenting an already-rotated (revoked) token
    is treated as theft: the whole token family is revoked."""
    stmt = select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
    if is_postgres(db):
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise AuthError("Invalid refresh token", code="invalid_refresh_token")
    if row.revoked_at is not None:
        revoked = _revoke_family(db, row.family_id)
        db.commit()
        log.warning("Refresh token reuse detected for user %s; revoked %s tokens of family %s",
                    row.user_id, revoked, row.family_id)
        raise AuthError("Refresh token was already used; please sign in again", code="token_reused")
    if row.expires_at <= utcnow():
        raise AuthError("Refresh token expired", code="refresh_expired")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("User not found or inactive")
    pair, new_row = issue_tokens(db, user, request, family_id=row.family_id)
    row.revoked_at = utcnow()
    row.replaced_by_id = new_row.id
    db.commit()
    return user, pair


def revoke_refresh_token(db: Session, raw: str | None) -> None:
    """Logout: revoke the presented token's whole family (unknown tokens are ignored)."""
    if not raw:
        return
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if row is not None:
        _revoke_family(db, row.family_id)
        db.commit()


# -------------------------------------------------------------- accounts
def _find_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def register(db: Session, data: RegisterIn, request: Request | None = None) -> tuple[User, TokenPair]:
    """Create a password account and sign it in."""
    email = str(data.email).strip().lower()
    if _find_by_email(db, email) is not None:
        raise ConflictError("An account with this email already exists", code="email_taken")
    validate_password_strength(data.password)
    tz = validate_timezone(data.timezone) if data.timezone else "UTC"
    user = User(email=email, password_hash=hash_password(data.password), full_name=data.full_name.strip(),
                timezone=tz, settings={}, last_login_at=utcnow())
    db.add(user)
    db.flush()
    pair, _ = issue_tokens(db, user, request)
    db.commit()
    log.info("Registered user %s", user.id)
    return user, pair


def login(db: Session, data: LoginIn, request: Request | None = None) -> tuple[User, TokenPair]:
    """Password sign-in (same error for unknown email and wrong password)."""
    user = _find_by_email(db, str(data.email))
    if user is None or not verify_password(data.password, user.password_hash):
        raise AuthError("Invalid email or password", code="invalid_credentials")
    if not user.is_active:
        raise AuthError("This account is disabled", code="account_disabled", status_code=403)
    user.last_login_at = utcnow()
    pair, _ = issue_tokens(db, user, request)
    db.commit()
    return user, pair


# ----------------------------------------------------------- Google sign-in
def google_redirect_uri() -> str:
    s = get_settings()
    return f"{s.backend_public_url.rstrip('/')}{s.api_prefix}/auth/google/callback"


def google_cookie_path() -> str:
    return f"{_auth_path()}/google"


def start_google_signin() -> tuple[str, str]:
    """Return ``(authorization_url, nonce)``; the nonce goes into a browser-binding cookie."""
    from app.integrations import google_oauth

    google_oauth.require_configured()
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = google_oauth.new_pkce_pair()
    state = sign_payload({"n": nonce, "v": verifier}, GOOGLE_STATE_TTL, GOOGLE_STATE_PURPOSE)
    url = google_oauth.build_auth_url(redirect_uri=google_redirect_uri(), scopes=google_oauth.GOOGLE_SIGNIN_SCOPES,
                                      state=state, code_challenge=challenge)
    return url, nonce


def complete_google_signin(db: Session, *, code: str | None, state: str | None, error: str | None,
                           nonce_cookie: str | None, request: Request | None = None) -> tuple[User, TokenPair]:
    """Validate the callback, exchange the code, and sign in / link / create the user."""
    from app.integrations import google_oauth

    if error:
        raise ValidationFailed(f"Google sign-in was cancelled or refused ({error}).", code="google_denied")
    if not code or not state:
        raise ValidationFailed("Missing code or state from Google.", code="google_invalid_callback")
    payload = verify_signed_payload(state, GOOGLE_STATE_PURPOSE)
    if not nonce_cookie or not secrets.compare_digest(str(payload.get("n", "")), nonce_cookie):
        raise ValidationFailed("Sign-in session mismatch; please try again from the same browser.",
                               code="google_state_mismatch")
    tokens = google_oauth.exchange_code(code, redirect_uri=google_redirect_uri(), code_verifier=payload.get("v"))
    info = google_oauth.fetch_userinfo(str(tokens.get("access_token") or ""))
    sub, email = info.get("sub"), (info.get("email") or "").strip().lower()
    if not sub or not email:
        raise ValidationFailed("Google did not return an account identifier and email.", code="google_no_email")
    if not info.get("email_verified"):
        raise ValidationFailed("Your Google email address is not verified.", code="google_email_unverified")
    user = db.scalar(select(User).where(User.google_sub == sub)) or _find_by_email(db, email)
    if user is None:
        user = User(email=email, password_hash=None, full_name=(info.get("name") or email.split("@")[0])[:200],
                    avatar_url=info.get("picture"), google_sub=sub, timezone="UTC", settings={})
        db.add(user)
        db.flush()
        log.info("Created user %s via Google sign-in", user.id)
    else:
        if user.google_sub and user.google_sub != sub:
            raise ValidationFailed("This email is linked to a different Google account.", code="google_conflict")
        if not user.is_active:
            raise AuthError("This account is disabled", code="account_disabled", status_code=403)
        user.google_sub = sub
        if not user.avatar_url and info.get("picture"):
            user.avatar_url = info.get("picture")
    user.last_login_at = utcnow()
    pair, _ = issue_tokens(db, user, request)
    db.commit()
    return user, pair
