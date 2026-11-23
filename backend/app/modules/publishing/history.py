"""Append-only publishing history (audit log) helper. Never commits."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import PublishingHistory, PublishingQueueItem


def record(db: Session, item: PublishingQueueItem, action: str, status: str, message: str | None = None, *,
           details: dict[str, Any] | None = None) -> PublishingHistory:
    """Add a history row for ``item`` (action: scheduled|upload|publish|status|retry|cancel|failed|...)."""
    if item in db.new:  # the queue row must exist before its history (FK; no ORM relationship)
        db.flush()
    entry = PublishingHistory(
        user_id=item.user_id, queue_item_id=item.id, clip_id=item.clip_id,
        connected_account_id=item.connected_account_id, platform=item.platform, action=action[:30],
        status=str(status)[:30], external_id=item.external_id, external_url=item.external_url,
        message=(message or None) and message[:4000], details=details or {})
    db.add(entry)
    return entry
