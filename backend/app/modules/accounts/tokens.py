"""OAuth token lifecycle for connected accounts (refresh, persistence, failure handling).

* Google: access tokens live ~1 h; refreshed with the stored refresh token when they expire
  within 5 minutes. ``invalid_grant`` (revoked / expired grant) marks the account revoked.
* Instagram Login: long-lived tokens (60 days) are refreshed once they are >= 24 h old
  (``ig_refresh_token``); an expired token cannot be refreshed -> account expired.
* Facebook Login: long-lived user tokens cannot be refreshed without the user; when they
  are about to expire the account is marked expired and the user is asked to reconnect.

Every state change is persisted (committed) immediately, and a single ACCOUNT_ISSUE
notification is sent when an active account becomes unusable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt, encrypt
from app.core.database import utcnow
from app.core.errors import PermissionDenied
from app.integrations import google_oauth, meta_oauth
from app.integrations.http import ProviderError
from app.models import ConnectedAccount, User
from app.models.enums import AccountStatus, NotificationType, Platform

log = logging.getLogger(__name__)

PROVIDER_GOOGLE = "google"
PROVIDER_INSTAGRAM = meta_oauth.FLOW_INSTAGRAM
PROVIDER_FACEBOOK = meta_oauth.FLOW_FACEBOOK
PROVIDER_UNIFIED = meta_oauth.FLOW_UNIFIED
REFRESH_MARGIN = timedelta(minutes=5)
IG_REFRESH_AGE = timedelta(hours=24)
REAUTH_CODE = "account_reauth_required"


def reauth_error(account: ConnectedAccount, message: str | None = None) -> PermissionDenied:
    """Error raised when the account must be reconnected by the user."""
    return PermissionDenied(message or account.last_error or "This account must be reconnected.",
                            code=REAUTH_CODE, details={"account_id": str(account.id),
                                                       "platform": account.platform})


def mark_account_issue(db: Session, account: ConnectedAccount, status: str, message: str) -> None:
    """Set a non-active status + last_error; notify once on the active -> broken transition."""
    from app.modules.notifications.service import notify

    was_active = account.status == AccountStatus.ACTIVE
    account.status = str(status)
    account.last_error = message[:2000]
    account.last_checked_at = utcnow()
    if was_active:
        name = account.display_name or account.username or account.platform
        notify(db, account.user_id, NotificationType.ACCOUNT_ISSUE,
               f"Reconnect your {account.platform.capitalize()} account {name}", message,
               link="/app/accounts", data={"account_id": str(account.id), "status": str(status)})
    db.flush()


def store_tokens(account: ConnectedAccount, *, access_token: str, refresh_token: str | None = None,
                 expires_in: Any = None, now: datetime | None = None) -> None:
    """Encrypt and store tokens; ``expires_in`` seconds -> ``token_expires_at``."""
    now = now or utcnow()
    account.access_token_enc = encrypt(access_token)
    if refresh_token:
        account.refresh_token_enc = encrypt(refresh_token)
    try:
        seconds = int(expires_in) if expires_in not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        seconds = None
    account.token_expires_at = now + timedelta(seconds=seconds) if seconds else None
    account.account_metadata = {**(account.account_metadata or {}), "token_issued_at": now.isoformat()}


def _issued_at(account: ConnectedAccount) -> datetime | None:
    raw = (account.account_metadata or {}).get("token_issued_at")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _fail(db: Session, account: ConnectedAccount, status: str, message: str) -> PermissionDenied:
    mark_account_issue(db, account, status, message)
    db.commit()
    return reauth_error(account, message)


def _refresh_google(db: Session, account: ConnectedAccount) -> str:
    refresh = decrypt(account.refresh_token_enc)
    if not refresh:
        raise _fail(db, account, AccountStatus.EXPIRED,
                    "Google did not provide a refresh token for this connection. Reconnect the account.")
    try:
        resp = google_oauth.refresh_access_token(refresh)
    except ProviderError as exc:
        if exc.error_code == "invalid_grant":
            raise _fail(db, account, AccountStatus.REVOKED,
                        f"Google refused the stored authorization ({exc.provider_message}). "
                        "Access was revoked or expired; reconnect the account.") from exc
        raise
    store_tokens(account, access_token=resp["access_token"], refresh_token=resp.get("refresh_token"),
                 expires_in=resp.get("expires_in"))
    if resp.get("scope"):
        account.scopes = google_oauth.granted_scopes(resp)
    account.status = AccountStatus.ACTIVE
    account.last_error = None
    db.commit()
    log.info("Refreshed Google token for account %s", account.id)
    return resp["access_token"]


def _refresh_instagram(db: Session, account: ConnectedAccount, token: str, expiring: bool) -> str:
    try:
        resp = meta_oauth.refresh_long_lived_instagram(token)
    except ProviderError as exc:
        if expiring or exc.error_code == 190:  # 190 = OAuthException (invalid/expired token)
            raise _fail(db, account, AccountStatus.EXPIRED,
                        f"Instagram token could not be refreshed ({exc.provider_message}). "
                        "Reconnect the account.") from exc
        log.warning("Instagram token refresh failed for %s: %s", account.id, exc.provider_message)
        return token
    store_tokens(account, access_token=resp["access_token"], expires_in=resp.get("expires_in"))
    account.status = AccountStatus.ACTIVE
    account.last_error = None
    db.commit()
    log.info("Refreshed Instagram long-lived token for account %s", account.id)
    return resp["access_token"]


def ensure_token(db: Session, account: ConnectedAccount, *, margin: timedelta = REFRESH_MARGIN) -> str:
    """Return a usable access token, refreshing when it expires within ``margin``."""
    if account.status == AccountStatus.REVOKED:
        raise reauth_error(account)
    token = decrypt(account.access_token_enc)
    if not token:
        raise _fail(db, account, AccountStatus.EXPIRED, "No access token stored; reconnect the account.")
    now = utcnow()
    expires = account.token_expires_at
    expiring = expires is not None and expires - now <= margin
    if account.provider == PROVIDER_GOOGLE:
        if expiring or account.status == AccountStatus.EXPIRED:
            return _refresh_google(db, account)
        return token
    if expires is not None and expires <= now:
        raise _fail(db, account, AccountStatus.EXPIRED, "The Instagram access token has expired. "
                    "Reconnect the account.")
    if account.provider == PROVIDER_INSTAGRAM:
        issued = _issued_at(account)
        if expiring or issued is None or now - issued >= IG_REFRESH_AGE:
            return _refresh_instagram(db, account, token, expiring)
        return token
    if expiring:  # facebook_login / unified_login: Meta tokens cannot be refreshed server-side
        raise _fail(db, account, AccountStatus.EXPIRED,
                    "The Meta login for this Instagram account is about to expire. Reconnect the account to "
                    "continue publishing.")
    if account.status != AccountStatus.ACTIVE:
        raise reauth_error(account)
    return token


def get_access_token(db: Session, account: ConnectedAccount) -> str:
    """Contract: a valid access token for ``account`` (refreshes + persists when expiring)."""
    return ensure_token(db, account)


def find_google_account(db: Session, user: User, *, need_scope: str | None = None) -> ConnectedAccount | None:
    """The user's most recently updated active Google (YouTube) connection having ``need_scope``."""
    rows = db.scalars(select(ConnectedAccount).where(
        ConnectedAccount.user_id == user.id, ConnectedAccount.platform == Platform.YOUTUBE,
        ConnectedAccount.provider == PROVIDER_GOOGLE, ConnectedAccount.status == AccountStatus.ACTIVE,
    ).order_by(ConnectedAccount.updated_at.desc()))
    for acc in rows:
        if need_scope is None or need_scope in (acc.scopes or []):
            return acc
    return None
