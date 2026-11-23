"""Classification of publishing failures into transient / permanent / quota."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.database import utcnow
from app.core.errors import (
    AppError,
    ConflictError,
    DependencyMissing,
    IntegrationNotConfigured,
    NotFoundError,
    PermissionDenied,
    QuotaExceeded,
    ValidationFailed,
)
from app.integrations import quota
from app.integrations.http import ProviderError

TRANSIENT = "transient"
PERMANENT = "permanent"
QUOTA = "quota"

YT_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
YT_UPLOAD_LIMIT_REASONS = {"uploadLimitExceeded"}
YT_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError"}
META_RATE_CODES = {4, 17, 32, 341, 613}
META_PUBLISH_LIMIT_SUBCODES = {"2207042"}
IG_RATE_RETRY = timedelta(hours=2)


@dataclass(eq=False)
class PublishError(Exception):
    """A classified publishing failure."""

    kind: str
    message: str
    retry_at: datetime | None = None

    def __str__(self) -> str:
        return self.message


def _retry_at(details: object) -> datetime | None:
    raw = details.get("retry_at") if isinstance(details, dict) else None
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _classify_provider(exc: ProviderError) -> PublishError:
    msg = f"{exc.provider}: {exc.provider_message}"
    reason = str(exc.reason or "")
    if exc.provider.startswith("youtube"):
        if reason in YT_QUOTA_REASONS:
            return PublishError(QUOTA, f"YouTube API quota exhausted ({exc.provider_message})",
                                quota.next_reset(quota.YOUTUBE))
        if reason in YT_UPLOAD_LIMIT_REASONS:
            return PublishError(QUOTA, f"YouTube channel upload limit reached ({exc.provider_message})",
                                utcnow() + timedelta(days=1))
        if reason in YT_RATE_REASONS:
            return PublishError(TRANSIENT, msg)
        if exc.status == 401:
            return PublishError(PERMANENT, f"YouTube rejected the access token ({exc.provider_message}); "
                                "reconnect the account.")
    if exc.provider in ("instagram", "facebook"):
        code = exc.error_code if isinstance(exc.error_code, int) else None
        if str(exc.reason or "") in META_PUBLISH_LIMIT_SUBCODES:
            return PublishError(QUOTA, f"Instagram publishing limit reached ({exc.provider_message})",
                                utcnow() + IG_RATE_RETRY)
        if code in META_RATE_CODES:
            return PublishError(TRANSIENT, msg)
        if code == 190:
            return PublishError(PERMANENT, f"Instagram rejected the access token ({exc.provider_message}); "
                                "reconnect the account.")
        payload_err = (exc.payload or {}).get("error") if isinstance(exc.payload, dict) else None
        if isinstance(payload_err, dict) and payload_err.get("is_transient"):
            return PublishError(TRANSIENT, msg)
    return PublishError(TRANSIENT if exc.transient else PERMANENT, msg)


def classify(exc: BaseException) -> PublishError:
    """Map any exception raised while publishing to a :class:`PublishError`."""
    if isinstance(exc, PublishError):
        return exc
    if isinstance(exc, QuotaExceeded):
        return PublishError(QUOTA, exc.message, _retry_at(exc.details) or utcnow() + timedelta(hours=24))
    if isinstance(exc, ProviderError):
        return _classify_provider(exc)
    if isinstance(exc, (PermissionDenied, ValidationFailed, NotFoundError, ConflictError, DependencyMissing,
                        IntegrationNotConfigured)):
        return PublishError(PERMANENT, exc.message)
    if isinstance(exc, AppError):
        return PublishError(PERMANENT if exc.status_code < 500 else TRANSIENT, exc.message)
    if isinstance(exc, OSError):
        return PublishError(TRANSIENT, f"Storage/network error: {exc}")
    return PublishError(TRANSIENT, f"{exc.__class__.__name__}: {exc}")
