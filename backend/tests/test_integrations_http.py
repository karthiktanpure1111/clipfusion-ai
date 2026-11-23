"""Shared HTTP helper: retries/backoff, Retry-After, provider error messages, token redaction."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from app.integrations import http
from app.integrations.http import ProviderError, redact_url


@pytest.fixture()
def sleeps(monkeypatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(http, "_sleep", calls.append)
    return calls


@respx.mock
def test_retries_on_5xx_and_honours_retry_after(sleeps) -> None:
    route = respx.get("https://api.example.com/x").mock(side_effect=[
        httpx.Response(503, json={"error": {"message": "busy"}}),
        httpx.Response(429, headers={"Retry-After": "7"}, json={"error": {"message": "slow down"}}),
        httpx.Response(200, json={"ok": True}),
    ])
    assert http.request_json("GET", "https://api.example.com/x", provider="test") == {"ok": True}
    assert route.call_count == 3 and sleeps[1] == 7.0 and 0 < sleeps[0] < 2


@respx.mock
def test_retry_after_too_long_is_surfaced(sleeps) -> None:
    respx.get("https://api.example.com/q").mock(return_value=httpx.Response(
        429, headers={"Retry-After": "3600"}, json={"error": {"message": "quota"}}))
    with pytest.raises(ProviderError) as exc:
        http.request("GET", "https://api.example.com/q", provider="test")
    assert exc.value.retry_after == 3600 and exc.value.transient and sleeps == []


@respx.mock
def test_connection_errors_are_retried_then_raised(sleeps) -> None:
    route = respx.get("https://api.example.com/down").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderError) as exc:
        http.request("GET", "https://api.example.com/down", provider="test", retries=2)
    assert route.call_count == 3 and exc.value.status is None and exc.value.transient


@respx.mock
def test_provider_error_messages(sleeps) -> None:
    respx.get("https://www.googleapis.com/a").mock(return_value=httpx.Response(403, json={"error": {
        "code": 403, "message": "The request cannot be completed because you have exceeded your quota.",
        "errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(ProviderError) as exc:
        http.request("GET", "https://www.googleapis.com/a", provider="youtube")
    assert exc.value.reason == "quotaExceeded" and "exceeded your quota" in exc.value.message
    assert not exc.value.transient and exc.value.status_code == 502
    respx.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(400, json={
        "error": "invalid_grant", "error_description": "Bad Request"}))
    with pytest.raises(ProviderError) as exc:
        http.request("POST", "https://oauth2.googleapis.com/token", provider="google")
    assert exc.value.error_code == "invalid_grant" and exc.value.provider_message == "Bad Request"
    respx.get("https://graph.instagram.com/v1/me").mock(return_value=httpx.Response(400, json={"error": {
        "message": "Invalid OAuth access token", "type": "OAuthException", "code": 190, "error_subcode": 463}}))
    with pytest.raises(ProviderError) as exc:
        http.request("GET", "https://graph.instagram.com/v1/me", provider="instagram")
    assert exc.value.error_code == 190 and exc.value.reason == "463"


@respx.mock
def test_tokens_never_logged(sleeps, caplog) -> None:
    respx.get(url__startswith="https://graph.instagram.com/v1/me").mock(return_value=httpx.Response(
        500, json={"error": {"message": "oops"}}))
    with caplog.at_level(logging.DEBUG, logger="app.integrations.http"), pytest.raises(ProviderError):
        http.request("GET", "https://graph.instagram.com/v1/me", provider="instagram",
                     params={"access_token": "SECRET-TOKEN", "fields": "id"}, retries=1)
    assert "SECRET-TOKEN" not in caplog.text and "access_token=%2A%2A%2A" in caplog.text
    assert "SECRET" not in redact_url("https://x.test/p?client_secret=SECRET&code=SECRET&a=1")
