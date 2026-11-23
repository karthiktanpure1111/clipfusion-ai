"""Auth + users API: register/login, refresh rotation + reuse detection, logout, cookie auth + CSRF."""

from __future__ import annotations

import base64
import hashlib
import uuid
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from app.core.config import get_settings
from app.models import RefreshToken, User
from tests.test_api_core_helpers import CSRF

API = "/api/v1"
PASSWORD = "Sup3rSecret!pw"


def _register(client, email: str | None = None, **extra) -> dict:
    email = email or f"auth-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(f"{API}/auth/register", json={"email": email, "password": PASSWORD, "full_name": "Ada",
                                                    **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_register_login_and_user_out(client) -> None:
    body = _register(client, timezone="Europe/Berlin")
    assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60
    user = body["user"]
    assert user["timezone"] == "Europe/Berlin" and user["has_password"] is True and user["google_linked"] is False
    assert user["settings"]["default_clip_durations"] == [30.0, 60.0] and user["settings"]["default_max_clips"] == 10
    assert user["settings"]["default_processing_mode"] == "balanced" and user["settings"]["default_privacy"] == "private"
    assert (user["settings"]["default_min_duration"], user["settings"]["default_max_duration"]) == (20.0, 60.0)
    assert user["settings"]["auto_delete_original"] is False
    cookies = {c.name: c for c in client.cookies.jar}
    assert cookies["cf_access"].path == "/" and cookies["cf_refresh"].path == "/api/v1/auth"
    # duplicate email (case-insensitive) -> 409
    r = client.post(f"{API}/auth/register", json={"email": user["email"].upper(), "password": PASSWORD,
                                                  "full_name": "X"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken"
    # weak password / invalid timezone
    r = client.post(f"{API}/auth/register", json={"email": "weak@example.com", "password": "short", "full_name": "X"})
    assert r.status_code == 422
    r = client.post(f"{API}/auth/register", json={"email": "tz@example.com", "password": PASSWORD, "full_name": "X",
                                                  "timezone": "Mars/Base"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_timezone"
    # login
    r = client.post(f"{API}/auth/login", json={"email": user["email"], "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user"]["id"] == user["id"]
    r = client.post(f"{API}/auth/login", json={"email": user["email"], "password": "wrong-Password1"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_cookie_auth_requires_csrf_header_for_unsafe_methods(client) -> None:
    _register(client)
    assert client.get(f"{API}/auth/me").status_code == 200  # cookie, safe method
    r = client.patch(f"{API}/users/me", json={"full_name": "New"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.patch(f"{API}/users/me", json={"full_name": "New"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["full_name"] == "New"


def test_refresh_rotation_and_reuse_detection(client, db) -> None:
    body = _register(client)
    r1 = body["refresh_token"]
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": r1})
    assert r.status_code == 200
    r2 = r.json()["refresh_token"]
    assert r2 != r1
    # cookie-based refresh needs the CSRF header
    client.cookies.set("cf_refresh", r2, path="/api/v1/auth")
    assert client.post(f"{API}/auth/refresh").status_code == 403
    # reuse of the rotated token revokes the whole family (r2 included)
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": r1})
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_reused"
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": r2})
    assert r.status_code == 401
    uid = uuid.UUID(body["user"]["id"])
    rows = db.scalars(select(RefreshToken).where(RefreshToken.user_id == uid)).all()
    assert rows and all(t.revoked_at is not None for t in rows)
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": "garbage"}).status_code == 401


def test_cookie_refresh_and_logout(client) -> None:
    _register(client)
    r = client.post(f"{API}/auth/refresh", headers=CSRF)
    assert r.status_code == 200
    new_refresh = r.json()["refresh_token"]
    r = client.post(f"{API}/auth/logout")  # cookie without the CSRF header: refused, nothing revoked
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.post(f"{API}/auth/logout", headers=CSRF)
    assert r.status_code == 204
    assert "cf_access" not in {c.name for c in client.cookies.jar}
    assert client.post(f"{API}/auth/refresh", json={"refresh_token": new_refresh}).status_code == 401
    assert client.get(f"{API}/auth/me").status_code == 401


def test_google_signin_not_configured(client, monkeypatch) -> None:
    from app.core.config import get_settings

    # independent of the developer's .env (which may contain real Google credentials)
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    monkeypatch.setattr(get_settings(), "google_client_secret", "")
    r = client.get(f"{API}/auth/google/login")
    assert r.status_code == 503 and r.json()["error"]["code"] == "integration_not_configured"
    assert "GOOGLE_CLIENT_ID" in r.json()["error"]["message"]
    r = client.get(f"{API}/auth/google/callback", params={"code": "x", "state": "bad"}, follow_redirects=False)
    assert r.status_code == 302 and "/login?error=" in r.headers["location"]


def _google(client, monkeypatch, info: dict, *, keep_nonce: bool = True):
    """Run the whole sign-in redirect flow with Google's HTTP calls replaced."""
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid")
    monkeypatch.setattr(s, "google_client_secret", "secret")
    seen: dict = {}

    def exchange(code, *, redirect_uri, code_verifier=None):  # noqa: ANN001, ANN202
        seen.update(code=code, redirect_uri=redirect_uri, verifier=code_verifier)
        return {"access_token": "at"}

    monkeypatch.setattr("app.integrations.google_oauth.exchange_code", exchange)
    monkeypatch.setattr("app.integrations.google_oauth.fetch_userinfo", lambda token: dict(info))
    url = client.get(f"{API}/auth/google/login").json()["authorization_url"]
    q = parse_qs(urlsplit(url).query)
    if not keep_nonce:
        client.cookies.delete("cf_google_nonce")
    r = client.get(f"{API}/auth/google/callback", params={"code": "c0de", "state": q["state"][0]},
                   follow_redirects=False)
    return r, q, seen


def test_google_signin_creates_and_links_users(client, db, user, monkeypatch) -> None:
    email = f"g-{uuid.uuid4().hex[:8]}@example.com"
    info = {"sub": uuid.uuid4().hex, "email": email.upper(), "email_verified": True, "name": "Gee", "picture": "p"}
    r, q, seen = _google(client, monkeypatch, info)
    assert r.status_code == 302 and r.headers["location"] == "http://localhost:3000/app"
    assert seen["redirect_uri"] == "http://testserver/api/v1/auth/google/callback" == q["redirect_uri"][0]
    # PKCE: the challenge sent to Google is derived from the verifier used in the exchange
    digest = base64.urlsafe_b64encode(hashlib.sha256(seen["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"][0] == digest
    me = client.get(f"{API}/auth/me").json()
    assert me["email"] == email and me["google_linked"] and not me["has_password"]
    # existing password account with the same verified email is linked, not duplicated
    r, *_ = _google(client, monkeypatch, {**info, "sub": uuid.uuid4().hex, "email": user.email})
    assert r.headers["location"].endswith("/app")
    me = client.get(f"{API}/auth/me").json()
    assert me["id"] == str(user.id) and me["google_linked"] and me["has_password"]


def test_google_signin_refusals(client, monkeypatch) -> None:
    info = {"sub": uuid.uuid4().hex, "email": f"r-{uuid.uuid4().hex[:8]}@example.com", "email_verified": False}
    r, *_ = _google(client, monkeypatch, info)
    assert r.status_code == 302 and "/login?error=" in r.headers["location"] and "verified" in r.headers["location"]
    r, *_ = _google(client, monkeypatch, {**info, "email_verified": True}, keep_nonce=False)
    assert "/login?error=" in r.headers["location"] and "cf_access" not in {c.name for c in client.cookies.jar}


def test_users_me_settings_password_and_delete(client, db, user, auth_headers) -> None:
    r = client.patch(f"{API}/users/me", headers=auth_headers,
                     json={"timezone": "America/New_York", "settings": {"default_max_clips": 5,
                                                                         "default_clip_durations": [15, 45]}})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["default_max_clips"] == 5 and s["default_clip_durations"] == [15.0, 45.0] and s["default_use_llm"]
    r = client.patch(f"{API}/users/me", headers=auth_headers, json={"settings": {"notify_email": True}})
    assert r.json()["settings"]["default_max_clips"] == 5 and r.json()["settings"]["notify_email"] is True
    assert client.patch(f"{API}/users/me", headers=auth_headers, json={"timezone": "Nope/Zone"}).status_code == 422
    r = client.patch(f"{API}/users/me", headers=auth_headers,
                     json={"settings": {"default_framing_preset_id": str(uuid.uuid4())}})
    assert r.status_code == 422
    # password: current required
    r = client.post(f"{API}/users/me/password", headers=auth_headers, json={"new_password": "An0therGoodPass"})
    assert r.status_code == 403
    r = client.post(f"{API}/users/me/password", headers=auth_headers,
                    json={"current_password": PASSWORD, "new_password": "An0therGoodPass"})
    assert r.status_code == 204
    assert client.post(f"{API}/auth/login", json={"email": user.email, "password": "An0therGoodPass"}).status_code == 200
    # delete
    uid = user.id
    assert client.delete(f"{API}/users/me", headers=auth_headers).status_code == 204
    db.expire_all()
    assert db.get(User, uid) is None
