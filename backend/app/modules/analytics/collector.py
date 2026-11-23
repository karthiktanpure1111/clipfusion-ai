"""Fetch real statistics for published clips from the official APIs into analytics_snapshots."""

from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.integrations import quota
from app.integrations.instagram import InstagramClient
from app.integrations.youtube import fetch_video_statistics
from app.models import AnalyticsSnapshot, ConnectedAccount, PublishingQueueItem
from app.models.enums import AccountStatus, Platform, QueueStatus
from app.modules.accounts.tokens import get_access_token

log = logging.getLogger(__name__)

TRACK_DAYS = 90


def _snapshot(item: PublishingQueueItem, stats: dict[str, Any]) -> AnalyticsSnapshot:
    return AnalyticsSnapshot(user_id=item.user_id, clip_id=item.clip_id, queue_item_id=item.id,
                             connected_account_id=item.connected_account_id, platform=item.platform,
                             external_id=item.external_id or "", views=stats.get("views"),
                             likes=stats.get("likes"), comments=stats.get("comments"), shares=stats.get("shares"),
                             saves=stats.get("saves"), reach=stats.get("reach"),
                             raw={k: v for k, v in stats.items() if k != "raw"} | {"api": stats.get("raw") or {}})


def _collect_youtube(db: Session, account: ConnectedAccount, items: list[PublishingQueueItem]) -> int:
    token = get_access_token(db, account)
    stats = fetch_video_statistics(token, [i.external_id for i in items if i.external_id])
    quota.record_usage(db, quota.YOUTUBE, str(account.id), math.ceil(len(items) / 50) * quota.COST_LIST)
    count = 0
    for item in items:
        if item.external_id in stats:
            db.add(_snapshot(item, stats[item.external_id]))
            count += 1
    return count


def _collect_instagram(db: Session, account: ConnectedAccount, items: list[PublishingQueueItem]) -> int:
    token = get_access_token(db, account)
    ig_user_id = (account.account_metadata or {}).get("ig_user_id") or account.external_account_id
    client = InstagramClient(token, str(ig_user_id), flow=account.provider)
    count = 0
    for item in items:
        db.add(_snapshot(item, client.insights(item.external_id or "")))
        count += 1
    return count


def collect_platform_stats(db: Session, *, user_id: uuid.UUID | None = None,
                           track_days: int = TRACK_DAYS) -> dict[str, Any]:
    """Snapshot statistics of publications from the last ``track_days`` days. Commits per account."""
    since = utcnow() - timedelta(days=track_days)
    stmt = select(PublishingQueueItem).where(
        PublishingQueueItem.status == QueueStatus.PUBLISHED, PublishingQueueItem.external_id.is_not(None),
        PublishingQueueItem.published_at >= since)
    if user_id is not None:
        stmt = stmt.where(PublishingQueueItem.user_id == user_id)
    by_account: dict[uuid.UUID, list[PublishingQueueItem]] = defaultdict(list)
    for item in db.scalars(stmt):
        by_account[item.connected_account_id].append(item)
    snapshots, errors = 0, []
    for account_id, items in by_account.items():
        account = db.get(ConnectedAccount, account_id)
        if account is None or account.status != AccountStatus.ACTIVE:
            errors.append({"account_id": str(account_id), "error": "account not active"})
            continue
        try:
            if account.platform == Platform.YOUTUBE:
                snapshots += _collect_youtube(db, account, items)
            else:
                snapshots += _collect_instagram(db, account, items)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - one account failing must not stop the others
            db.rollback()
            log.warning("Analytics refresh failed for account %s: %s", account_id, exc)
            errors.append({"account_id": str(account_id), "error": str(exc)[:300]})
    return {"snapshots": snapshots, "accounts": len(by_account), "errors": errors}
