"""Password hashing, JWTs, signed short-lived tokens (media URLs, OAuth state)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import BACKEND_DIR, get_settings
from app.core.database import utcnow
from app.core.errors import AuthError

log = logging.getLogger(__name__)
_hasher = PasswordHasher()
JWT_ALG = "HS256"


@lru_cache
def get_secret_key() -> str:
    """Return SECRET_KEY. In development, persist an auto-generated key so that the API
    process and worker processes agree. Production refuses to start without one."""
    settings = get_settings()
    if settings.secret_key:
        if settings.is_production and len(settings.secret_key) < 32:
            raise RuntimeError("SECRET_KEY must be at least 32 characters in production")
        return settings.secret_key
    if settings.is_production:
        raise RuntimeError("SECRET_KEY is required in production")
    path = BACKEND_DIR / "data" / ".dev_secret_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        log.warning("SECRET_KEY not set; generated a development key at %s", path)
    return path.read_text(encoding="utf-8").strip()


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise AuthError("Password must be at least 10 characters", code="weak_password", status_code=422)
    classes = sum(bool(any(f(c) for c in password)) for f in (str.islower, str.isupper, str.isdigit))
    if classes < 2:
        raise AuthError("Password must mix letters and numbers or upper/lower case",
                        code="weak_password", status_code=422)


# ----------------------------------------------------------------------- JWTs
def create_access_token(user_id: uuid.UUID, extra: dict[str, Any] | None = None) -> str:
    settings = get_settings()
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, get_secret_key(), algorithm=JWT_ALG)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Access token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid access token", code="invalid_token") from exc
    if payload.get("type") != "access":
        raise AuthError("Invalid token type", code="invalid_token")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Only the hash is stored server-side."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------- generic signed payloads
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_payload(payload: dict[str, Any], ttl_seconds: int, purpose: str) -> str:
    """Compact HMAC-signed token: base64(json).base64(sig). ``purpose`` domain-separates uses."""
    body = dict(payload)
    body["_exp"] = int(time.time()) + int(ttl_seconds)
    body["_p"] = purpose
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(get_secret_key().encode(), f"{purpose}.{raw}".encode(), hashlib.sha256).digest()
    return f"{raw}.{_b64(sig)}"


def verify_signed_payload(token: str, purpose: str) -> dict[str, Any]:
    try:
        raw, sig = token.split(".", 1)
        expected = hmac.new(get_secret_key().encode(), f"{purpose}.{raw}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            raise AuthError("Invalid signature", code="invalid_signature")
        body = json.loads(_unb64(raw))
    except AuthError:
        raise
    except Exception as exc:  # malformed token
        raise AuthError("Malformed token", code="invalid_signature") from exc
    if body.get("_p") != purpose:
        raise AuthError("Token purpose mismatch", code="invalid_signature")
    if int(body.get("_exp", 0)) < int(time.time()):
        raise AuthError("Token expired", code="token_expired")
    return body


def safe_filename(name: str, default: str = "file") -> str:
    """Strip any path components and unsafe characters from a user-supplied filename."""
    base = Path(name.replace("\\", "/")).name
    cleaned = "".join(c for c in base if c.isalnum() or c in "._- ").strip(" .")
    return cleaned[:150] or default
