"""Encryption at rest for OAuth tokens (Fernet / AES-128-CBC + HMAC-SHA256).

``TOKEN_ENCRYPTION_KEYS`` holds one or more comma-separated Fernet keys. The first key
encrypts; every key can decrypt, which allows key rotation (prepend the new key, then
call :func:`rotate` on stored values).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def _fernet() -> MultiFernet:
    settings = get_settings()
    keys = [k.strip() for k in settings.token_encryption_keys.split(",") if k.strip()]
    if not keys:
        if settings.is_production:
            raise RuntimeError("TOKEN_ENCRYPTION_KEYS is required in production")
        # Development fallback: derive a stable key from SECRET_KEY so tokens survive restarts.
        from app.core.security import get_secret_key

        derived = base64.urlsafe_b64encode(hashlib.sha256(("fernet:" + get_secret_key()).encode()).digest())
        log.warning("TOKEN_ENCRYPTION_KEYS not set; using a key derived from SECRET_KEY (development only)")
        keys = [derived.decode()]
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


def encrypt(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt stored credential (wrong TOKEN_ENCRYPTION_KEYS?)") from exc


def rotate(ciphertext: str | None) -> str | None:
    if ciphertext is None:
        return None
    return _fernet().rotate(ciphertext.encode("ascii")).decode("ascii")
