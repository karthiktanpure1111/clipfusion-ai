"""Connected accounts: OAuth connect/callback, listing, disconnect and health checks."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.database import utcnow
from app.core.deps import get_owned
from app.core.errors import AppError, AuthError, NotFoundError, PermissionDenied, ValidationFailed
from app.core.security import sign_payload, verify_signed_payload
from app.integrations import google_oauth, meta_oauth, quota, youtube
from app.integrations.http import ProviderError
from app.models import ConnectedAccount, PublishingHistory, PublishingQueueItem, User
from app.models.enums import AccountStatus, Platform, QueueStatus
from app.modules.accounts.tokens import (
    PROVIDER_FACEBOOK,
    PROVIDER_GOOGLE,
    PROVIDER_INSTAGRAM,
    PROVIDER_UNIFIED,
    ensure_token,
    mark_account_issue,
    store_tokens,
)

log = logging.getLogger(__name__)

STATE_PURPOSE = "oauth_state"
STATE_TTL = 600
CALLBACK_PROVIDERS = {"google": PROVIDER_GOOGLE, "instagram": PROVIDER_INSTAGRAM, "facebook": PROVIDER_FACEBOOK}
PENDING_STATUSES = (QueueStatus.PENDING_APPROVAL, QueueStatus.SCHEDULED, QueueStatus.RENDERING)
_PUBLIC_META_KEYS = ("channel_id", "uploads_playlist_id", "custom_url", "ig_user_id", "username", "account_type",
                     "page_id", "page_name")


@dataclass
class ConnectStart:
    authorization_url: str
    nonce: str


# --------------------------------------------------------------------- read
def list_accounts(db: Session, user: User) -> list[ConnectedAccount]:
    return list(db.scalars(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id)
                           .order_by(ConnectedAccount.platform, ConnectedAccount.created_at)))


def account_to_dict(acc: ConnectedAccount) -> dict[str, Any]:
    """Public representation (never includes tokens)."""
    meta = acc.account_metadata or {}
    scopes = list(acc.scopes or [])
    return {
        "id": acc.id, "platform": acc.platform, "provider": acc.provider,
        "external_account_id": acc.external_account_id, "display_name": acc.display_name,
        "username": acc.username, "avatar_url": acc.avatar_url, "status": acc.status, "scopes": scopes,
        "has_drive_scope": google_oauth.SCOPE_DRIVE_READONLY in scopes,
        "can_publish": acc.status == AccountStatus.ACTIVE and (
            google_oauth.SCOPE_YOUTUBE_UPLOAD in scopes if acc.platform == Platform.YOUTUBE else True),
        "has_analytics_scope": google_oauth.SCOPE_YT_ANALYTICS in scopes if acc.platform == Platform.YOUTUBE
        else None,
        "metadata": {k: meta[k] for k in _PUBLIC_META_KEYS if k in meta},
        "token_expires_at": acc.token_expires_at, "last_error": acc.last_error,
        "last_checked_at": acc.last_checked_at, "created_at": acc.created_at, "updated_at": acc.updated_at,
    }


# ------------------------------------------------------------------ connect
def _state(user: User, platform: str, provider: str, nonce: str, verifier: str | None) -> str:
    return sign_payload({"uid": str(user.id), "platform": platform, "provider": provider, "nonce": nonce,
                         "verifier": verifier}, STATE_TTL, STATE_PURPOSE)


def start_connect(user: User, platform: str, *, include_drive: bool = False) -> ConnectStart:
    """Build the provider authorization URL (PKCE for Google) with a signed state."""
    settings = get_settings()
    nonce = secrets.token_urlsafe(24)
    if platform == Platform.YOUTUBE:
        google_oauth.require_configured()
        verifier, challenge = google_oauth.new_pkce_pair()
        scopes = list(google_oauth.YOUTUBE_SCOPES)
        if include_drive:
            scopes.append(google_oauth.SCOPE_DRIVE_READONLY)
        url = google_oauth.build_auth_url(
            redirect_uri=settings.oauth_redirect_uri("google"), scopes=scopes,
            state=_state(user, platform, "google", nonce, verifier), code_challenge=challenge,
            prompt_consent=True, offline=True)
        return ConnectStart(url, nonce)
    if platform == Platform.INSTAGRAM:
        flow = settings.instagram_auth_flow
        path = "facebook" if flow == meta_oauth.FLOW_FACEBOOK else "instagram"  # unified uses .../instagram
        meta_oauth.require_configured(flow)
        url = meta_oauth.build_auth_url(flow, redirect_uri=settings.oauth_redirect_uri(path),
                                        state=_state(user, platform, path, nonce, None))
        return ConnectStart(url, nonce)
    raise ValidationFailed(f"Unsupported platform '{platform}'")


def _verify_state(provider: str, state: str | None, nonce_cookie: str | None,
                  session_user_id: str | None) -> dict[str, Any]:
    if not state:
        raise ValidationFailed("Missing OAuth state; start the connection again.")
    try:
        payload = verify_signed_payload(state, STATE_PURPOSE)
    except AuthError as exc:
        raise ValidationFailed("The connection request expired or is invalid; start again.") from exc
    if payload.get("provider") != provider:
        raise ValidationFailed("OAuth state does not match this provider.")
    bound = (nonce_cookie and secrets.compare_digest(nonce_cookie, str(payload.get("nonce")))) or (
        session_user_id is not None and session_user_id == payload.get("uid"))
    if not bound:
        raise ValidationFailed("Could not verify that this browser started the connection. "
                               "Start again from the Connected Accounts page.")
    return payload


def complete_callback(db: Session, provider: str, *, code: str | None, state: str | None,
                      error: str | None = None, error_description: str | None = None,
                      nonce_cookie: str | None = None, session_user_id: str | None = None) -> str:
    """Finish an OAuth callback; upserts the account(s) and commits. Returns the platform."""
    if provider not in CALLBACK_PROVIDERS:
        raise NotFoundError("Unknown OAuth provider")
    payload = _verify_state(provider, state, nonce_cookie, session_user_id)
    user = db.get(User, uuid.UUID(str(payload["uid"])))
    if user is None or not user.is_active:
        raise ValidationFailed("User not found")
    if error:
        raise ValidationFailed(f"Authorization was not granted: {error_description or error}")
    if not code:
        raise ValidationFailed("The provider did not return an authorization code.")
    if provider == "google":
        _complete_google(db, user, code, payload.get("verifier"))
    elif provider == "instagram" and get_settings().instagram_auth_flow == meta_oauth.FLOW_UNIFIED:
        _complete_unified(db, user, code)
    elif provider == "instagram":
        _complete_instagram(db, user, code)
    else:
        _complete_facebook(db, user, code)
    db.commit()
    return str(payload.get("platform"))


def upsert_account(db: Session, user: User, *, platform: str, provider: str, external_id: str,
                   display_name: str, username: str | None, avatar_url: str | None,
                   metadata: dict[str, Any]) -> ConnectedAccount:
    acc = db.scalar(select(ConnectedAccount).where(
        ConnectedAccount.user_id == user.id, ConnectedAccount.platform == platform,
        ConnectedAccount.external_account_id == external_id))
    if acc is None:
        acc = ConnectedAccount(user_id=user.id, platform=platform, provider=provider,
                               external_account_id=external_id, scopes=[], account_metadata={})
        db.add(acc)
    acc.provider = provider
    acc.display_name = (display_name or username or external_id)[:255]
    acc.username = username
    acc.avatar_url = avatar_url
    acc.account_metadata = {**(acc.account_metadata or {}), **metadata}
    acc.status = AccountStatus.ACTIVE
    acc.last_error = None
    acc.last_checked_at = utcnow()
    return acc


def _complete_google(db: Session, user: User, code: str, verifier: str | None) -> ConnectedAccount:
    redirect = get_settings().oauth_redirect_uri("google")
    tok = google_oauth.exchange_code(code, redirect_uri=redirect, code_verifier=verifier)
    scopes = google_oauth.granted_scopes(tok)
    if google_oauth.SCOPE_YOUTUBE_UPLOAD not in scopes:
        raise ValidationFailed("Permission to upload videos to YouTube was not granted. Connect again and "
                               "keep 'Manage your YouTube videos' selected.", code="missing_scope")
    channel = youtube.fetch_my_channel(tok["access_token"])
    acc = upsert_account(db, user, platform=Platform.YOUTUBE, provider=PROVIDER_GOOGLE,
                         external_id=channel["channel_id"], display_name=channel["title"],
                         username=channel.get("custom_url"), avatar_url=channel.get("thumbnail_url"),
                         metadata={"channel_id": channel["channel_id"],
                                   "uploads_playlist_id": channel.get("uploads_playlist_id"),
                                   "custom_url": channel.get("custom_url")})
    store_tokens(acc, access_token=tok["access_token"], refresh_token=tok.get("refresh_token"),
                 expires_in=tok.get("expires_in"))
    acc.scopes = scopes
    db.flush()
    quota.record_usage(db, quota.YOUTUBE, str(acc.id), quota.COST_LIST)
    return acc


def _complete_instagram(db: Session, user: User, code: str) -> ConnectedAccount:
    redirect = get_settings().oauth_redirect_uri("instagram")
    short = meta_oauth.exchange_code_instagram(code, redirect_uri=redirect)
    perms = short.get("permissions") or meta_oauth.IG_LOGIN_SCOPES
    if "instagram_business_content_publish" not in perms:
        raise ValidationFailed("Permission to publish content was not granted. Connect again and allow "
                               "content publishing.", code="missing_scope")
    long = meta_oauth.exchange_long_lived_instagram(short["access_token"])
    profile = meta_oauth.fetch_instagram_profile(long["access_token"])
    ig_id = profile["ig_user_id"] or short["user_id"]
    acc = upsert_account(db, user, platform=Platform.INSTAGRAM, provider=PROVIDER_INSTAGRAM, external_id=ig_id,
                         display_name=profile["name"], username=profile.get("username"),
                         avatar_url=profile.get("profile_picture_url"),
                         metadata={"ig_user_id": ig_id, "username": profile.get("username"),
                                   "account_type": profile.get("account_type")})
    store_tokens(acc, access_token=long["access_token"], expires_in=long.get("expires_in"))
    acc.scopes = sorted(perms)
    db.flush()
    return acc


def _complete_facebook(db: Session, user: User, code: str) -> list[ConnectedAccount]:
    redirect = get_settings().oauth_redirect_uri("facebook")
    short = meta_oauth.exchange_code_facebook(code, redirect_uri=redirect)
    long = meta_oauth.exchange_long_lived_facebook(short["access_token"])
    token = long["access_token"]
    perms = meta_oauth.fetch_facebook_permissions(token)
    if "instagram_content_publish" not in perms:
        raise ValidationFailed("Permission 'instagram_content_publish' was not granted.", code="missing_scope")
    linked = meta_oauth.fetch_facebook_ig_accounts(token)
    if not linked:
        raise ValidationFailed("No Instagram professional account is linked to the Facebook Pages you shared. "
                               "Link your Business/Creator account to a Page and select that Page during login.",
                               code="instagram_not_linked")
    out = []
    for ig in linked:
        acc = upsert_account(db, user, platform=Platform.INSTAGRAM, provider=PROVIDER_FACEBOOK,
                             external_id=ig["ig_user_id"], display_name=ig["name"], username=ig.get("username"),
                             avatar_url=ig.get("profile_picture_url"),
                             metadata={k: ig.get(k) for k in ("ig_user_id", "username", "account_type", "page_id",
                                                              "page_name")})
        store_tokens(acc, access_token=token, expires_in=long.get("expires_in"))
        acc.scopes = perms
        out.append(acc)
    db.flush()
    return out


def _complete_unified(db: Session, user: User, code: str) -> list[ConnectedAccount]:
    """Unified Login: one connected account per Instagram professional account shared during login."""
    redirect = get_settings().oauth_redirect_uri("instagram")
    tok = meta_oauth.exchange_code_unified(code, redirect_uri=redirect)
    token = tok["access_token"]
    perms = meta_oauth.fetch_unified_permissions(token)
    if perms is not None and "instagram_content_publish" not in perms:
        raise ValidationFailed("Permission to publish content (instagram_content_publish) was not granted. Add it "
                               "to the login configuration in your Meta app and connect again.", code="missing_scope")
    shared = meta_oauth.fetch_unified_ig_accounts(token)
    if not shared:
        raise ValidationFailed("No Instagram professional account was shared. During the Meta login, pick your "
                               "business portfolio and tick your Instagram account, then connect again.",
                               code="instagram_not_shared")
    out = []
    for ig in shared:
        acc = upsert_account(db, user, platform=Platform.INSTAGRAM, provider=PROVIDER_UNIFIED,
                             external_id=ig["ig_user_id"], display_name=ig["name"] or ig.get("username") or "Instagram",
                             username=ig.get("username"), avatar_url=ig.get("profile_picture_url"),
                             metadata={k: ig.get(k) for k in ("ig_user_id", "username", "account_type")})
        store_tokens(acc, access_token=token, expires_in=tok.get("expires_in"))
        acc.scopes = sorted(perms or meta_oauth.UNIFIED_SCOPES)
        out.append(acc)
    db.flush()
    return out


# --------------------------------------------------------------- disconnect
def disconnect_account(db: Session, user: User, account_id: uuid.UUID) -> None:
    """Best-effort revoke, cancel pending publications (history kept), delete the account."""
    acc = get_owned(db, ConnectedAccount, account_id, user, name="Account")
    if acc.provider == PROVIDER_GOOGLE:
        try:
            token = decrypt(acc.refresh_token_enc) or decrypt(acc.access_token_enc)
        except RuntimeError:
            token = None
        if token:
            google_oauth.revoke_token(token)
    items = db.scalars(select(PublishingQueueItem).where(
        PublishingQueueItem.connected_account_id == acc.id, PublishingQueueItem.status.in_(PENDING_STATUSES)))
    for item in items:
        item.status = QueueStatus.CANCELLED
        db.add(PublishingHistory(user_id=user.id, queue_item_id=item.id, clip_id=item.clip_id,
                                 connected_account_id=None, platform=item.platform, action="cancel",
                                 status=QueueStatus.CANCELLED, message="Account disconnected"))
    db.flush()
    db.delete(acc)
    db.commit()


# -------------------------------------------------------------------- check
def check_account(db: Session, user: User, account_id: uuid.UUID) -> ConnectedAccount:
    """Validate the token (refreshing it) and refresh the profile from the platform."""
    acc = get_owned(db, ConnectedAccount, account_id, user, name="Account")
    try:
        token = ensure_token(db, acc)
        _refresh_identity(db, acc, token)
        acc.status = AccountStatus.ACTIVE
        acc.last_error = None
    except PermissionDenied:
        pass  # already marked expired/revoked by the token layer
    except ProviderError as exc:
        if exc.status in (401, 403) or exc.error_code == 190:
            mark_account_issue(db, acc, AccountStatus.EXPIRED, f"Platform rejected the token: {exc.provider_message}")
        else:
            acc.last_error = f"Check failed temporarily: {exc.provider_message}"
    except AppError as exc:
        mark_account_issue(db, acc, AccountStatus.ERROR, exc.message)
    acc.last_checked_at = utcnow()
    db.commit()
    return acc


def _refresh_identity(db: Session, acc: ConnectedAccount, token: str) -> None:
    if acc.platform == Platform.YOUTUBE:
        ch = youtube.fetch_my_channel(token)
        quota.record_usage(db, quota.YOUTUBE, str(acc.id), quota.COST_LIST)
        acc.display_name, acc.avatar_url = ch["title"] or acc.display_name, ch.get("thumbnail_url")
        acc.account_metadata = {**(acc.account_metadata or {}), "uploads_playlist_id": ch.get("uploads_playlist_id")}
    elif acc.provider == PROVIDER_INSTAGRAM:
        prof = meta_oauth.fetch_instagram_profile(token)
        acc.username, acc.avatar_url = prof.get("username"), prof.get("profile_picture_url")
        acc.account_metadata = {**(acc.account_metadata or {}), "account_type": prof.get("account_type"),
                                "username": prof.get("username")}
    elif acc.provider == PROVIDER_UNIFIED:
        shared = {a["ig_user_id"]: a for a in meta_oauth.fetch_unified_ig_accounts(token)}
        if acc.external_account_id not in shared:
            raise ValidationFailed("This Instagram account is no longer shared with ClipFusion; connect it again.")
        ig = shared[acc.external_account_id]
        acc.username = ig.get("username") or acc.username
        acc.avatar_url = ig.get("profile_picture_url") or acc.avatar_url
    else:
        linked = {a["ig_user_id"]: a for a in meta_oauth.fetch_facebook_ig_accounts(token)}
        if acc.external_account_id not in linked:
            raise ValidationFailed("This Instagram account is no longer linked to a Facebook Page you granted.")
        acc.avatar_url = linked[acc.external_account_id].get("profile_picture_url")
