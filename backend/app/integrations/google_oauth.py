"""Google OAuth 2.0 (authorization-code flow with PKCE) for YouTube/Drive and Sign in with Google.

Endpoints are Google's documented ones; no client libraries are required.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.errors import IntegrationNotConfigured
from app.integrations import http
from app.integrations.http import ProviderError

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPE_OPENID = "openid"
SCOPE_EMAIL = "email"
SCOPE_PROFILE = "profile"
SCOPE_YOUTUBE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_YOUTUBE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_YT_ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPE_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

GOOGLE_SIGNIN_SCOPES: list[str] = [SCOPE_OPENID, SCOPE_EMAIL, SCOPE_PROFILE]
YOUTUBE_SCOPES: list[str] = [SCOPE_YOUTUBE_UPLOAD, SCOPE_YOUTUBE_READONLY, SCOPE_YT_ANALYTICS,
                             *GOOGLE_SIGNIN_SCOPES]

_SHORT_SCOPES = {"email": "https://www.googleapis.com/auth/userinfo.email",
                 "profile": "https://www.googleapis.com/auth/userinfo.profile"}


def missing_env() -> list[str]:
    """Names of the Google OAuth environment variables that are not set."""
    s = get_settings()
    return [name for name, val in (("GOOGLE_CLIENT_ID", s.google_client_id),
                                   ("GOOGLE_CLIENT_SECRET", s.google_client_secret)) if not val]


def require_configured() -> None:
    """Raise :class:`IntegrationNotConfigured` naming the missing variables."""
    missing = missing_env()
    if missing:
        raise IntegrationNotConfigured(
            "Google OAuth is not configured. Set " + ", ".join(missing)
            + " (Google Cloud Console > APIs & Services > Credentials > OAuth client ID, type 'Web').",
            details={"missing_env": missing})


def new_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256 (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
    return verifier, challenge.decode("ascii")


def build_auth_url(*, redirect_uri: str, scopes: list[str], state: str, code_challenge: str | None = None,
                   prompt_consent: bool = False, offline: bool = False, login_hint: str | None = None) -> str:
    """Build Google's authorization URL. ``offline`` requests a refresh token."""
    require_configured()
    params: dict[str, str] = {
        "client_id": get_settings().google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(dict.fromkeys(scopes)),
        "state": state,
        "include_granted_scopes": "true",
    }
    if offline:
        params["access_type"] = "offline"
    if prompt_consent:
        params["prompt"] = "consent"
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, *, redirect_uri: str, code_verifier: str | None = None) -> dict[str, Any]:
    """Exchange an authorization code. Returns Google's token response
    (``access_token``, ``expires_in``, ``refresh_token`` when offline, ``scope``, ``id_token``)."""
    require_configured()
    s = get_settings()
    data = {"code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
    if code_verifier:
        data["code_verifier"] = code_verifier
    return http.request_json("POST", TOKEN_URL, provider="google", data=data, retries=2)


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Use a refresh token. Raises ProviderError with ``error_code == "invalid_grant"`` when
    the grant was revoked or expired."""
    require_configured()
    s = get_settings()
    data = {"client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token"}
    return http.request_json("POST", TOKEN_URL, provider="google", data=data, retries=2)


def revoke_token(token: str) -> bool:
    """Best-effort revocation of an access or refresh token. Never raises."""
    try:
        http.request("POST", REVOKE_URL, provider="google", data={"token": token}, retries=1,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        return True
    except ProviderError as exc:
        log.info("Google token revocation failed: %s", exc.provider_message)
        return False


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    """OpenID Connect userinfo -> ``{"sub","email","email_verified","name","picture"}``."""
    info = http.request_json("GET", USERINFO_URL, provider="google",
                             headers={"Authorization": f"Bearer {access_token}"})
    return {"sub": info.get("sub"), "email": info.get("email"),
            "email_verified": bool(info.get("email_verified")), "name": info.get("name") or "",
            "picture": info.get("picture")}


def granted_scopes(token_response: dict[str, Any], requested: list[str] | None = None) -> list[str]:
    """Scopes actually granted (Google returns a space-separated ``scope``), normalised so
    ``email``/``profile`` and their URL forms compare equal."""
    raw = token_response.get("scope")
    scopes = raw.split() if isinstance(raw, str) and raw.strip() else list(requested or [])
    reverse = {v: k for k, v in _SHORT_SCOPES.items()}
    return sorted({reverse.get(s, s) for s in scopes})
