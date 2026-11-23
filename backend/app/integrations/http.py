"""Shared HTTP helper for official external APIs (Google, YouTube, Meta, Drive, URL import).

* One pooled :class:`httpx.Client` with sane timeouts.
* Retries with exponential backoff (+ jitter) on 429 / 5xx / connection errors, honouring
  ``Retry-After`` (capped; longer waits are surfaced to the caller instead of blocking).
* Non-2xx responses raise :class:`ProviderError` (an ``ExternalServiceError``) carrying the
  provider's own error message, error code and reason so callers can classify failures.
* Tokens never reach the logs: URLs are logged with secret query parameters redacted and
  request headers/bodies are never logged.
"""

from __future__ import annotations

import email.utils
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.core.errors import ExternalServiceError

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0
_SENSITIVE_PARAMS = frozenset({
    "access_token", "refresh_token", "client_secret", "code", "code_verifier", "key", "token",
    "fb_exchange_token", "id_token", "input_token", "password", "upload_id",
})

# Indirection so tests can disable real sleeping.
_sleep: Callable[[float], None] = time.sleep

_client: httpx.Client | None = None
_client_lock = threading.Lock()


class ProviderError(ExternalServiceError):
    """A non-successful answer (or transport failure) from an external API."""

    def __init__(self, provider: str, message: str, *, status: int | None = None,
                 error_code: str | int | None = None, reason: str | None = None,
                 payload: Any = None, retry_after: float | None = None) -> None:
        super().__init__(f"{provider}: {message}",
                         details={"provider": provider, "status": status, "error_code": error_code,
                                  "reason": reason})
        self.provider = provider
        self.provider_message = message
        self.status = status
        self.error_code = error_code
        self.reason = reason
        self.payload = payload
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        """True for failures that are worth retrying later (network, 429, 5xx)."""
        return self.status is None or self.status in RETRY_STATUSES


def get_client() -> httpx.Client:
    """Return the shared, thread-safe pooled client."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=DEFAULT_TIMEOUT, follow_redirects=False,
                                   headers={"User-Agent": "ClipFusion/1.0 (+official-api-client)"})
        return _client


def redact_url(url: str | httpx.URL) -> str:
    """Return ``url`` with secret query parameter values replaced by ``***``."""
    parts = urlsplit(str(url))
    if not parts.query:
        return str(url)
    query = [(k, "***" if k.lower() in _SENSITIVE_PARAMS else v) for k, v in parse_qsl(parts.query, True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse_error(resp: httpx.Response) -> tuple[str, str | int | None, str | None]:
    """Extract ``(message, error_code, reason)`` from Google / Meta / OAuth error bodies."""
    try:
        body = resp.json()
    except ValueError:
        text = (resp.text or "").strip()
        return (text[:300] or f"HTTP {resp.status_code}"), None, None
    if not isinstance(body, dict):
        return f"HTTP {resp.status_code}", None, None
    err = body.get("error")
    if isinstance(err, dict):  # Google APIs and Meta Graph API
        message = str(err.get("message") or err.get("error_user_msg") or f"HTTP {resp.status_code}")
        code: str | int | None = err.get("code")
        reason = None
        errors = err.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = errors[0].get("reason")
        if err.get("error_subcode") is not None:
            reason = reason or str(err.get("error_subcode"))
        if err.get("status") and not reason:
            reason = str(err.get("status"))
        return message, code, reason
    if isinstance(err, str):  # OAuth 2.0 token endpoint
        return str(body.get("error_description") or err), err, None
    if body.get("error_message"):  # Instagram Login token endpoint
        return str(body["error_message"]), body.get("code"), body.get("error_type")
    return f"HTTP {resp.status_code}", None, None


def retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP date)."""
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def error_from_response(provider: str, resp: httpx.Response) -> ProviderError:
    message, code, reason = parse_error(resp)
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return ProviderError(provider, message, status=resp.status_code, error_code=code, reason=reason,
                         payload=payload, retry_after=retry_after_seconds(resp))


def _backoff(attempt: int, base: float, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    return min(30.0, base * (2 ** attempt)) * (0.75 + random.random() * 0.5)


def request(method: str, url: str, *, provider: str, headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None, data: dict[str, Any] | None = None,
            json: Any = None, content: bytes | Callable[[], Any] | None = None,
            timeout: float | httpx.Timeout | None = None, retries: int = 3, backoff: float = 1.0,
            ok_statuses: frozenset[int] | set[int] | None = None) -> httpx.Response:
    """Send a request with retry/backoff. Returns the response for 2xx or ``ok_statuses``.

    ``content`` may be a zero-argument callable returning a fresh body for each attempt
    (needed for streaming bodies). Raises :class:`ProviderError` otherwise."""
    client = get_client()
    safe_url = redact_url(httpx.URL(url, params=params) if params else url)
    attempt = 0
    while True:
        body = content() if callable(content) else content
        try:
            resp = client.request(method, url, headers=headers, params=params, data=data, json=json,
                                  content=body, timeout=timeout or DEFAULT_TIMEOUT)
        except httpx.TransportError as exc:
            if attempt >= retries:
                log.warning("%s %s failed after %s attempts: %s", method, safe_url, attempt + 1,
                            exc.__class__.__name__)
                raise ProviderError(provider, f"Network error contacting {provider}: "
                                    f"{exc.__class__.__name__}") from exc
            delay = _backoff(attempt, backoff, None)
            log.info("%s %s transport error (%s); retrying in %.1fs", method, safe_url,
                     exc.__class__.__name__, delay)
            attempt += 1
            _sleep(delay)
            continue
        if 200 <= resp.status_code < 300 or (ok_statuses and resp.status_code in ok_statuses):
            return resp
        err = error_from_response(provider, resp)
        retry_after = err.retry_after
        can_wait = retry_after is None or retry_after <= MAX_RETRY_AFTER_SECONDS
        if resp.status_code in RETRY_STATUSES and attempt < retries and can_wait:
            delay = _backoff(attempt, backoff, retry_after)
            log.info("%s %s -> HTTP %s; retrying in %.1fs", method, safe_url, resp.status_code, delay)
            attempt += 1
            _sleep(delay)
            continue
        log.warning("%s %s -> HTTP %s (%s)", method, safe_url, resp.status_code, err.provider_message[:200])
        raise err


def request_json(method: str, url: str, *, provider: str, **kwargs: Any) -> dict[str, Any]:
    """:func:`request` and decode a JSON object body (``{}`` for an empty body)."""
    resp = request(method, url, provider=provider, **kwargs)
    if not resp.content:
        return {}
    try:
        body = resp.json()
    except ValueError as exc:
        raise ProviderError(provider, "Invalid JSON in response", status=resp.status_code) from exc
    if not isinstance(body, dict):
        raise ProviderError(provider, "Unexpected response shape", status=resp.status_code)
    return body


@contextmanager
def stream(method: str, url: str, *, provider: str, headers: dict[str, str] | None = None,
           params: dict[str, Any] | None = None, timeout: float | httpx.Timeout | None = None,
           retries: int = 2, backoff: float = 1.0, allow_redirect_status: bool = False
           ) -> Iterator[httpx.Response]:
    """Open a streaming response (redirects are NOT followed automatically).

    Retries happen only before the body is consumed. 3xx responses are yielded when
    ``allow_redirect_status`` so callers can validate each hop themselves."""
    client = get_client()
    safe_url = redact_url(httpx.URL(url, params=params) if params else url)
    attempt = 0
    while True:
        req = client.build_request(method, url, headers=headers, params=params,
                                   timeout=timeout or httpx.Timeout(60.0, connect=10.0))
        try:
            resp = client.send(req, stream=True)
        except httpx.TransportError as exc:
            if attempt >= retries:
                raise ProviderError(provider, f"Network error contacting {provider}: "
                                    f"{exc.__class__.__name__}") from exc
            attempt += 1
            _sleep(_backoff(attempt - 1, backoff, None))
            continue
        ok = 200 <= resp.status_code < 300 or (allow_redirect_status and 300 <= resp.status_code < 400)
        if ok:
            try:
                yield resp
            finally:
                resp.close()
            return
        resp.read()
        resp.close()
        err = error_from_response(provider, resp)
        if resp.status_code in RETRY_STATUSES and attempt < retries and (
                err.retry_after is None or err.retry_after <= MAX_RETRY_AFTER_SECONDS):
            attempt += 1
            _sleep(_backoff(attempt - 1, backoff, err.retry_after))
            continue
        log.warning("%s %s -> HTTP %s (%s)", method, safe_url, resp.status_code, err.provider_message[:200])
        raise err
