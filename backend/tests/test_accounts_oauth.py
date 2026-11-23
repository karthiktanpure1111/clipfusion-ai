"""OAuth connect URLs, callbacks (token endpoints mocked), encrypted storage, token lifecycle."""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.core.database import utcnow
from app.core.errors import PermissionDenied
from app.integrations import google_oauth, meta_oauth
from app.integrations.youtube import API_BASE
from app.models import ConnectedAccount, Notification, PublishingHistory, PublishingQueueItem
from app.models.enums import AccountStatus, NotificationType, Platform, QueueStatus
from app.modules.accounts.tokens import find_google_account, get_access_token
from app.modules.scheduling import service as scheduling
from tests.test_publishing_factories import api_client, make_account, make_clip

V = get_settings().meta_graph_version


@pytest.fixture()
def configured(monkeypatch):
    s = get_settings()
    for key, val in (("google_client_id", "gid.apps.googleusercontent.com"), ("google_client_secret", "gsecret"),
                     ("instagram_app_id", "igapp"), ("instagram_app_secret", "igsecret"),
                     ("meta_app_id", "fbapp"), ("meta_app_secret", "fbsecret"),
                     ("instagram_auth_flow", "instagram_login")):
        monkeypatch.setattr(s, key, val)
    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)
    return s


def _connect(client, headers, platform: str, **params) -> dict[str, list[str]]:
    r = client.get(f"/api/v1/accounts/connect/{platform}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["authorization_url"]).query)


def test_connect_not_configured(user, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    monkeypatch.setattr(get_settings(), "google_client_secret", "")
    r = api_client().get("/api/v1/accounts/connect/youtube", headers=auth_headers)
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["code"] == "integration_not_configured"
    assert body["details"]["missing_env"] == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]


def test_requirements_endpoint(user, auth_headers, configured) -> None:
    r = api_client().get("/api/v1/accounts/requirements", headers=auth_headers)
    data = r.json()
    assert data["youtube"]["configured"] is True and data["youtube"]["missing_env"] == []
    assert data["youtube"]["redirect_uris"] == ["http://testserver/api/v1/accounts/callback/google"]
    assert data["instagram"]["provider"] == "instagram_login"
    assert {s["scope"] for s in data["instagram"]["scopes"]} == set(meta_oauth.IG_LOGIN_SCOPES)


@respx.mock
def test_google_connect_and_callback_stores_encrypted_tokens(db, user, auth_headers, configured) -> None:
    client = api_client()
    q = _connect(client, auth_headers, "youtube", include_drive="true")
    scopes = q["scope"][0].split()
    assert google_oauth.SCOPE_YOUTUBE_UPLOAD in scopes and google_oauth.SCOPE_DRIVE_READONLY in scopes
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["include_granted_scopes"] == ["true"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["http://testserver/api/v1/accounts/callback/google"]
    granted = " ".join(google_oauth.YOUTUBE_SCOPES[:3] + ["openid", "https://www.googleapis.com/auth/userinfo.email",
                                                            google_oauth.SCOPE_DRIVE_READONLY])
    token = respx.post(google_oauth.TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "ya29.access", "refresh_token": "1//refresh", "expires_in": 3599, "scope": granted,
        "token_type": "Bearer"}))
    respx.get(f"{API_BASE}/channels").mock(return_value=httpx.Response(200, json={"items": [{
        "id": "UCchannel0000000000000001", "snippet": {"title": "My Channel", "customUrl": "@me",
                                                       "thumbnails": {"default": {"url": "https://yt3/a.jpg"}}},
        "contentDetails": {"relatedPlaylists": {"uploads": "UUchannel"}}}]}))
    r = client.get("/api/v1/accounts/callback/google", params={"code": "4/abc", "state": q["state"][0]})
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "http://localhost:3000/app/accounts?connected=youtube"
    form = parse_qs(token.calls[0].request.content.decode())
    verifier = form["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == q["code_challenge"][0] and form["grant_type"] == ["authorization_code"]
    acc = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id))
    assert acc.external_account_id == "UCchannel0000000000000001" and acc.display_name == "My Channel"
    assert acc.access_token_enc != "ya29.access" and decrypt(acc.access_token_enc) == "ya29.access"
    assert decrypt(acc.refresh_token_enc) == "1//refresh"
    assert "email" in acc.scopes and google_oauth.SCOPE_DRIVE_READONLY in acc.scopes
    assert acc.token_expires_at > utcnow() + timedelta(minutes=55)
    listed = client.get("/api/v1/accounts", headers=auth_headers).json()
    assert listed[0]["has_drive_scope"] is True and listed[0]["metadata"]["uploads_playlist_id"] == "UUchannel"
    assert "ya29" not in str(listed) and "refresh" not in str(listed).replace("refresh_", "")
    assert find_google_account(db, user, need_scope=google_oauth.SCOPE_DRIVE_READONLY).id == acc.id


def test_callback_rejects_bad_or_unbound_state(user, auth_headers, configured) -> None:
    client = api_client()
    r = client.get("/api/v1/accounts/callback/google", params={"code": "x", "state": "forged.state"})
    assert r.status_code == 302 and "error=" in r.headers["location"]
    q = _connect(client, auth_headers, "youtube")
    fresh = api_client()  # different browser: no nonce cookie, no session
    r = fresh.get("/api/v1/accounts/callback/google", params={"code": "x", "state": q["state"][0]})
    assert "error=" in r.headers["location"] and "browser" in r.headers["location"]
    r = client.get("/api/v1/accounts/callback/google", params={"error": "access_denied", "state": q["state"][0]})
    assert "error=Authorization+was+not+granted" in r.headers["location"]


@respx.mock
def test_instagram_login_callback(db, user, auth_headers, configured) -> None:
    client = api_client()
    q = _connect(client, auth_headers, "instagram")
    assert q["scope"] == [",".join(meta_oauth.IG_LOGIN_SCOPES)] and q["client_id"] == ["igapp"]
    respx.post(meta_oauth.IG_TOKEN_URL).mock(return_value=httpx.Response(200, json={"data": [{
        "access_token": "IGshort", "user_id": "1789", "permissions": "instagram_business_basic,"
                                                                    "instagram_business_content_publish"}]}))
    respx.get(f"{meta_oauth.IG_GRAPH}/access_token").mock(return_value=httpx.Response(200, json={
        "access_token": "IGlong", "token_type": "bearer", "expires_in": 5183944}))
    me = respx.get(f"{meta_oauth.IG_GRAPH}/{V}/me").mock(return_value=httpx.Response(200, json={
        "user_id": "17841400000000001", "username": "creator", "account_type": "MEDIA_CREATOR",
        "profile_picture_url": "https://cdn/p.jpg"}))
    r = client.get("/api/v1/accounts/callback/instagram", params={"code": "AQB#_", "state": q["state"][0]})
    assert r.headers["location"].endswith("connected=instagram")
    acc = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id,
                                                   ConnectedAccount.platform == Platform.INSTAGRAM))
    assert acc.external_account_id == "17841400000000001" and decrypt(acc.access_token_enc) == "IGlong"
    assert acc.provider == "instagram_login" and acc.account_metadata["account_type"] == "MEDIA_CREATOR"
    # Personal accounts are rejected with a clear message.
    me.mock(return_value=httpx.Response(200, json={"user_id": "1", "username": "me", "account_type": "PERSONAL"}))
    q = _connect(client, auth_headers, "instagram")
    r = client.get("/api/v1/accounts/callback/instagram", params={"code": "c2", "state": q["state"][0]})
    assert "personal+Instagram+account" in r.headers["location"]


@respx.mock
def test_facebook_login_callback_links_page_accounts(db, user, auth_headers, configured, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "instagram_auth_flow", "facebook_login")
    client = api_client()
    q = _connect(client, auth_headers, "instagram")
    assert "business_management" in q["scope"][0]
    fb = f"{meta_oauth.FB_GRAPH}/{V}"
    respx.get(f"{fb}/oauth/access_token").mock(side_effect=lambda req: httpx.Response(200, json={
        "access_token": "FBlong" if req.url.params.get("grant_type") == "fb_exchange_token" else "FBshort",
        "expires_in": 5184000}))
    respx.get(f"{fb}/me/permissions").mock(return_value=httpx.Response(200, json={"data": [
        {"permission": p, "status": "granted"} for p in meta_oauth.FB_LOGIN_SCOPES]}))
    respx.get(f"{fb}/me/accounts").mock(return_value=httpx.Response(200, json={"data": [
        {"id": "page1", "name": "Page", "instagram_business_account": {"id": "1784199", "username": "brand"}},
        {"id": "page2", "name": "No IG"}]}))
    r = client.get("/api/v1/accounts/callback/facebook", params={"code": "fbcode", "state": q["state"][0]})
    assert r.headers["location"].endswith("connected=instagram"), r.headers["location"]
    acc = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id,
                                                   ConnectedAccount.provider == "facebook_login"))
    assert acc.external_account_id == "1784199" and acc.account_metadata["page_id"] == "page1"
    assert decrypt(acc.access_token_enc) == "FBlong"


@respx.mock
def test_google_token_refresh_and_invalid_grant(db, user, configured) -> None:
    acc = make_account(db, user, token="old", refresh="rt", expires_in=60)
    route = respx.post(google_oauth.TOKEN_URL).mock(return_value=httpx.Response(200, json={
        "access_token": "new-token", "expires_in": 3599}))
    assert get_access_token(db, acc) == "new-token"
    assert parse_qs(route.calls[0].request.content.decode())["grant_type"] == ["refresh_token"]
    db.refresh(acc)
    assert decrypt(acc.access_token_enc) == "new-token" and decrypt(acc.refresh_token_enc) == "rt"
    assert get_access_token(db, acc) == "new-token" and route.call_count == 1  # still fresh
    acc.token_expires_at = utcnow()
    db.commit()
    route.mock(return_value=httpx.Response(400, json={"error": "invalid_grant",
                                                      "error_description": "Token has been expired or revoked."}))
    with pytest.raises(PermissionDenied) as exc:
        get_access_token(db, acc)
    assert exc.value.code == "account_reauth_required"
    db.refresh(acc)
    assert acc.status == AccountStatus.REVOKED and "expired or revoked" in acc.last_error
    notes = db.scalars(select(Notification).where(Notification.user_id == user.id,
                                                  Notification.type == NotificationType.ACCOUNT_ISSUE)).all()
    assert len(notes) == 1


@respx.mock
def test_instagram_refresh_after_24h_and_facebook_expiry(db, user, configured) -> None:
    ig = make_account(db, user, Platform.INSTAGRAM, token="ig-old", expires_in=50 * 86400, issued_hours_ago=30)
    respx.get(f"{meta_oauth.IG_GRAPH}/refresh_access_token").mock(return_value=httpx.Response(200, json={
        "access_token": "ig-new", "token_type": "bearer", "expires_in": 5184000}))
    assert get_access_token(db, ig) == "ig-new"
    assert get_access_token(db, ig) == "ig-new"  # issued just now: no second refresh
    fb = make_account(db, user, Platform.INSTAGRAM, provider="facebook_login", token="fb", expires_in=120)
    with pytest.raises(PermissionDenied):
        get_access_token(db, fb)
    db.refresh(fb)
    assert fb.status == AccountStatus.EXPIRED and "reconnect" in fb.last_error.lower()


@respx.mock
def test_disconnect_revokes_and_cancels_pending(db, user, auth_headers, configured) -> None:
    acc = make_account(db, user, refresh="rt-to-revoke")
    item = scheduling.enqueue_clip(db, make_clip(db, user), acc, rights_confirmed=True)
    db.commit()
    acc_id, item_id, clip_id = acc.id, item.id, item.clip_id
    revoke = respx.post(google_oauth.REVOKE_URL).mock(return_value=httpx.Response(200))
    r = api_client().delete(f"/api/v1/accounts/{acc_id}", headers=auth_headers)
    assert r.status_code == 204
    assert parse_qs(revoke.calls[0].request.content.decode())["token"] == ["rt-to-revoke"]
    db.expire_all()
    db.expunge_all()
    assert db.get(ConnectedAccount, acc_id) is None and db.get(PublishingQueueItem, item_id) is None
    cancel = db.scalar(select(PublishingHistory).where(PublishingHistory.clip_id == clip_id,
                                                       PublishingHistory.action == "cancel"))
    assert cancel.status == QueueStatus.CANCELLED and cancel.message == "Account disconnected"


@respx.mock
def test_check_account_refreshes_profile(db, user, auth_headers, configured, other_user) -> None:
    acc = make_account(db, user)
    respx.get(f"{API_BASE}/channels").mock(return_value=httpx.Response(200, json={"items": [{
        "id": acc.external_account_id, "snippet": {"title": "Renamed"}, "contentDetails": {}}]}))
    client = api_client()
    r = client.post(f"/api/v1/accounts/{acc.id}/check", headers=auth_headers)
    assert r.status_code == 200 and r.json()["display_name"] == "Renamed" and r.json()["status"] == "active"
    from app.core.security import create_access_token

    other = {"Authorization": f"Bearer {create_access_token(other_user.id)}"}
    assert client.post(f"/api/v1/accounts/{acc.id}/check", headers=other).status_code == 404
    assert client.delete(f"/api/v1/accounts/{acc.id}", headers=other).status_code == 404


@respx.mock
def test_unified_login_callback_without_facebook_page(db, user, auth_headers, configured, monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "instagram_auth_flow", "unified_login")
    monkeypatch.setattr(s, "instagram_config_id", "cfg123")
    monkeypatch.setattr(s, "instagram_redirect_uri", "https://localhost:8011/api/v1/accounts/callback/instagram")
    client = api_client()
    r = client.get("/api/v1/accounts/requirements", headers=auth_headers).json()["instagram"]
    assert r["provider"] == "unified_login" and r["missing_env"] == []
    assert r["redirect_uris"] == ["https://localhost:8011/api/v1/accounts/callback/instagram"]
    q = _connect(client, auth_headers, "instagram")
    assert q["client_id"] == ["fbapp"] and q["config_id"] == ["cfg123"] and "scope" not in q
    assert q["redirect_uri"] == ["https://localhost:8011/api/v1/accounts/callback/instagram"]
    fb = f"{meta_oauth.FB_GRAPH}/{V}"
    token = respx.get(f"{fb}/oauth/access_token").mock(return_value=httpx.Response(200, json={
        "access_token": "SUAT", "token_type": "bearer"}))
    respx.get(f"{fb}/me/permissions").mock(return_value=httpx.Response(400, json={"error": {
        "message": "Unsupported for this token", "code": 100}}))
    respx.get(f"{fb}/me/assigned_instagram_accounts").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "1784155", "username": "gamingclips"}]}))
    respx.get(f"{fb}/1784155").mock(return_value=httpx.Response(200, json={
        "username": "gamingclips", "name": "Gaming Clips", "profile_picture_url": "https://cdn/a.jpg"}))
    r = client.get("/api/v1/accounts/callback/instagram", params={"code": "uc", "state": q["state"][0]})
    assert r.headers["location"].endswith("connected=instagram"), r.headers["location"]
    params = token.calls[0].request.url.params
    assert params["client_id"] == "fbapp" and params["client_secret"] == "fbsecret"
    assert params["redirect_uri"] == "https://localhost:8011/api/v1/accounts/callback/instagram"
    acc = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id,
                                                   ConnectedAccount.provider == "unified_login"))
    assert acc.external_account_id == "1784155" and acc.display_name == "Gaming Clips"
    assert decrypt(acc.access_token_enc) == "SUAT" and acc.token_expires_at is None
    assert "instagram_content_publish" in acc.scopes
    assert get_access_token(db, acc) == "SUAT"  # no expiry: used as is
    from app.integrations.instagram import InstagramClient

    assert "graph.facebook.com" in InstagramClient("t", "1784155", flow=acc.provider).base
    # nothing shared during login -> clear message
    respx.get(f"{fb}/me/assigned_instagram_accounts").mock(return_value=httpx.Response(200, json={"data": []}))
    q = _connect(client, auth_headers, "instagram")
    r = client.get("/api/v1/accounts/callback/instagram", params={"code": "uc2", "state": q["state"][0]})
    assert "No+Instagram+professional+account+was+shared" in r.headers["location"]


def test_https_callback_forwarder_redirects_only_callbacks(tmp_path, monkeypatch) -> None:
    import importlib.util
    import threading
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "https_callback", Path(__file__).resolve().parents[1] / "scripts" / "https_callback.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CERT_DIR", tmp_path)
    cert, key = mod.ensure_certificate()
    assert cert.is_file() and key.is_file() and mod.ensure_certificate() == (cert, key)  # reused
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler("http://localhost:8010"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        r = httpx.get(f"{base}/api/v1/accounts/callback/instagram?code=abc&state=xyz")
        assert r.status_code == 302
        assert r.headers["location"] == "http://localhost:8010/api/v1/accounts/callback/instagram?code=abc&state=xyz"
        assert httpx.get(f"{base}/anything").status_code == 404
    finally:
        server.shutdown()
