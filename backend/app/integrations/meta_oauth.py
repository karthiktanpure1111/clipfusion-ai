"""Meta OAuth for Instagram professional accounts.

Two official flows (``INSTAGRAM_AUTH_FLOW``):

* ``instagram_login`` -- "Instagram API with Instagram Login": authorize at
  instagram.com, code exchange at api.instagram.com, long-lived (60 day) tokens from
  graph.instagram.com that can be refreshed once they are at least 24 h old.
* ``facebook_login`` -- "Instagram API with Facebook Login": Facebook dialog, long-lived
  user token (``fb_exchange_token``, ~60 days, cannot be refreshed without the user), and
  the Instagram professional account linked to one of the user's Facebook Pages.
* ``unified_login`` -- "Instagram API with Unified Login" (Meta's recommended setup): Facebook Login for
  Business dialog with a ``config_id`` (no ``scope``), the user signs in with Instagram or Facebook and
  shares Instagram professional accounts of a business portfolio (no Facebook Page needed). The code is
  exchanged with the Meta app ID/secret for a business integration system-user token; the accounts come
  from ``/me/assigned_instagram_accounts`` and every call goes to graph.facebook.com.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from app.core.config import get_settings
from app.core.errors import IntegrationNotConfigured, ValidationFailed
from app.integrations import http

log = logging.getLogger(__name__)

FLOW_INSTAGRAM = "instagram_login"
FLOW_FACEBOOK = "facebook_login"
FLOW_UNIFIED = "unified_login"

IG_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
IG_GRAPH = "https://graph.instagram.com"
FB_GRAPH = "https://graph.facebook.com"

IG_LOGIN_SCOPES = ["instagram_business_basic", "instagram_business_content_publish"]
FB_LOGIN_SCOPES = ["instagram_basic", "instagram_content_publish", "pages_show_list", "pages_read_engagement",
                   "business_management"]
UNIFIED_SCOPES = ["instagram_basic", "instagram_content_publish"]  # chosen in the login configuration
PROFESSIONAL_TYPES = {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}


def _version() -> str:
    return get_settings().meta_graph_version


def credentials(flow: str) -> tuple[str, str]:
    s = get_settings()
    if flow == FLOW_INSTAGRAM:
        return s.instagram_app_id, s.instagram_app_secret
    return s.meta_app_id, s.meta_app_secret


def missing_env(flow: str | None = None) -> list[str]:
    """Environment variables still required for ``flow`` (default: the configured flow)."""
    flow = flow or get_settings().instagram_auth_flow
    app_id, secret = credentials(flow)
    if flow == FLOW_UNIFIED:
        values = {"META_APP_ID": app_id, "META_APP_SECRET": secret, "INSTAGRAM_CONFIG_ID": get_settings().instagram_config_id}
        return [n for n, v in values.items() if not v]
    names = ("INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET") if flow == FLOW_INSTAGRAM else ("META_APP_ID",
                                                                                           "META_APP_SECRET")
    return [n for n, v in zip(names, (app_id, secret), strict=True) if not v]


def require_configured(flow: str) -> None:
    missing = missing_env(flow)
    if missing:
        raise IntegrationNotConfigured(
            f"Instagram publishing ({flow}) is not configured. Set " + ", ".join(missing)
            + " from your Meta app (developers.facebook.com > App settings). See docs/INTEGRATIONS.md.",
            details={"missing_env": missing, "flow": flow})


def scopes_for(flow: str) -> list[str]:
    if flow == FLOW_UNIFIED:
        return list(UNIFIED_SCOPES)
    return list(IG_LOGIN_SCOPES if flow == FLOW_INSTAGRAM else FB_LOGIN_SCOPES)


def build_auth_url(flow: str, *, redirect_uri: str, state: str) -> str:
    """Authorization URL for the chosen flow."""
    require_configured(flow)
    app_id, _ = credentials(flow)
    if flow == FLOW_UNIFIED:  # the configuration replaces "scope"; prompt=login offers Instagram and Facebook
        params = {"client_id": app_id, "config_id": get_settings().instagram_config_id, "redirect_uri": redirect_uri,
                  "response_type": "code", "state": state, "prompt": "login"}
        return f"https://www.facebook.com/{_version()}/dialog/oauth?{urlencode(params)}"
    params = {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
              "scope": ",".join(scopes_for(flow))}
    if flow == FLOW_INSTAGRAM:
        params["enable_fb_login"] = "0"
        params["force_reauth"] = "true"
        return f"{IG_AUTHORIZE_URL}?{urlencode(params)}"
    return f"https://www.facebook.com/{_version()}/dialog/oauth?{urlencode(params)}"


# ------------------------------------------------------------ Instagram Login
def exchange_code_instagram(code: str, *, redirect_uri: str) -> dict[str, Any]:
    """Short-lived token: ``{"access_token","user_id","permissions"}``."""
    require_configured(FLOW_INSTAGRAM)
    app_id, secret = credentials(FLOW_INSTAGRAM)
    body = http.request_json("POST", IG_TOKEN_URL, provider="instagram", retries=2, data={
        "client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri, "code": code.removesuffix("#_")})
    data = body.get("data")
    if isinstance(data, list) and data:
        body = data[0]
    if not body.get("access_token"):
        raise ValidationFailed("Instagram did not return an access token")
    perms = body.get("permissions")
    if isinstance(perms, str):
        perms = [p for p in perms.split(",") if p]
    return {"access_token": body["access_token"], "user_id": str(body.get("user_id") or ""),
            "permissions": perms or []}


def exchange_long_lived_instagram(short_token: str) -> dict[str, Any]:
    """``ig_exchange_token`` -> ``{"access_token","token_type","expires_in"}`` (~60 days)."""
    _, secret = credentials(FLOW_INSTAGRAM)
    return http.request_json("GET", f"{IG_GRAPH}/access_token", provider="instagram", params={
        "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": short_token})


def refresh_long_lived_instagram(token: str) -> dict[str, Any]:
    """``ig_refresh_token`` (token must be >= 24 h old and unexpired)."""
    return http.request_json("GET", f"{IG_GRAPH}/refresh_access_token", provider="instagram",
                             params={"grant_type": "ig_refresh_token", "access_token": token})


def fetch_instagram_profile(token: str) -> dict[str, Any]:
    """``/me`` of an Instagram Login token; rejects non-professional accounts."""
    me = http.request_json("GET", f"{IG_GRAPH}/{_version()}/me", provider="instagram", params={
        "fields": "user_id,username,account_type,profile_picture_url,name", "access_token": token})
    account_type = str(me.get("account_type") or "").upper()
    if account_type and account_type not in PROFESSIONAL_TYPES:
        raise ValidationFailed(
            f"@{me.get('username')} is a personal Instagram account. Publishing through the official API "
            "requires a professional (Business or Creator) account: switch it in the Instagram app under "
            "Settings > Account type and tools, then connect again.", code="instagram_not_professional")
    return {"ig_user_id": str(me.get("user_id") or me.get("id") or ""), "username": me.get("username"),
            "account_type": account_type or None, "profile_picture_url": me.get("profile_picture_url"),
            "name": me.get("name") or me.get("username") or ""}


# ------------------------------------------------------------- Facebook Login
def exchange_code_facebook(code: str, *, redirect_uri: str) -> dict[str, Any]:
    require_configured(FLOW_FACEBOOK)
    app_id, secret = credentials(FLOW_FACEBOOK)
    return http.request_json("GET", f"{FB_GRAPH}/{_version()}/oauth/access_token", provider="facebook",
                             params={"client_id": app_id, "client_secret": secret, "redirect_uri": redirect_uri,
                                     "code": code})


def exchange_long_lived_facebook(short_token: str) -> dict[str, Any]:
    """``fb_exchange_token`` -> long-lived user token (``expires_in`` may be absent)."""
    app_id, secret = credentials(FLOW_FACEBOOK)
    return http.request_json("GET", f"{FB_GRAPH}/{_version()}/oauth/access_token", provider="facebook",
                             params={"grant_type": "fb_exchange_token", "client_id": app_id,
                                     "client_secret": secret, "fb_exchange_token": short_token})


def fetch_facebook_permissions(user_token: str) -> list[str]:
    body = http.request_json("GET", f"{FB_GRAPH}/{_version()}/me/permissions", provider="facebook",
                             params={"access_token": user_token})
    return sorted(p["permission"] for p in body.get("data") or [] if p.get("status") == "granted")


def fetch_facebook_ig_accounts(user_token: str) -> list[dict[str, Any]]:
    """Instagram professional accounts linked to the user's Facebook Pages."""
    fields = "id,name,instagram_business_account{id,username,name,profile_picture_url}"
    url = f"{FB_GRAPH}/{_version()}/me/accounts"
    params: dict[str, Any] | None = {"fields": fields, "limit": 100, "access_token": user_token}
    out: list[dict[str, Any]] = []
    for _ in range(10):  # paginate defensively
        body = http.request_json("GET", url, provider="facebook", params=params)
        for page in body.get("data") or []:
            ig = page.get("instagram_business_account")
            if not ig:
                continue
            out.append({"ig_user_id": str(ig.get("id")), "username": ig.get("username"),
                        "name": ig.get("name") or ig.get("username") or "",
                        "profile_picture_url": ig.get("profile_picture_url"), "page_id": str(page.get("id")),
                        "page_name": page.get("name"), "account_type": "BUSINESS"})
        nxt = (body.get("paging") or {}).get("next")
        if not nxt:
            break
        url, params = nxt, None
    return out


# -------------------------------------------------------------- Unified Login
def exchange_code_unified(code: str, *, redirect_uri: str) -> dict[str, Any]:
    """Code -> business integration system-user token (Meta app ID/secret, not the Instagram ones)."""
    require_configured(FLOW_UNIFIED)
    app_id, secret = credentials(FLOW_UNIFIED)
    body = http.request_json("GET", f"{FB_GRAPH}/{_version()}/oauth/access_token", provider="facebook",
                             params={"client_id": app_id, "client_secret": secret, "redirect_uri": redirect_uri,
                                     "code": code.removesuffix("#_")})
    if not body.get("access_token"):
        raise ValidationFailed("Meta did not return an access token")
    return body


def fetch_unified_permissions(token: str) -> list[str] | None:
    """Granted permissions, or None when Meta does not report them for this token type."""
    try:
        return fetch_facebook_permissions(token) or None
    except Exception:  # noqa: BLE001 - system-user tokens may not expose /me/permissions
        return None


def fetch_unified_ig_accounts(token: str) -> list[dict[str, Any]]:
    """Instagram professional accounts shared during Unified Login (``/me/assigned_instagram_accounts``)."""
    url = f"{FB_GRAPH}/{_version()}/me/assigned_instagram_accounts"
    params: dict[str, Any] | None = {"fields": "id,username", "limit": 100, "access_token": token}
    found: list[dict[str, Any]] = []
    for _ in range(10):  # paginate defensively
        body = http.request_json("GET", url, provider="facebook", params=params)
        found.extend(a for a in body.get("data") or [] if a.get("id"))
        nxt = (body.get("paging") or {}).get("next")
        if not nxt:
            break
        url, params = nxt, None
    out = []
    for acc in found:
        ig_id = str(acc["id"])
        info: dict[str, Any] = {}
        try:  # profile details are best effort
            info = http.request_json("GET", f"{FB_GRAPH}/{_version()}/{ig_id}", provider="facebook", params={
                "fields": "username,name,profile_picture_url", "access_token": token})
        except Exception:  # noqa: BLE001
            log.info("Could not read the profile of Instagram account %s", ig_id)
        username = info.get("username") or acc.get("username")
        out.append({"ig_user_id": ig_id, "username": username, "name": info.get("name") or username or "",
                    "profile_picture_url": info.get("profile_picture_url"), "account_type": "BUSINESS"})
    return out
