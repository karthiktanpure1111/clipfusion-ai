"""Dashboard summary built from real database counts."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models import GeneratedClip, ProcessingJob, PublishingQueueItem, User, VideoSource
from app.models.enums import QueueStatus, RenderStatus, SourceStatus, UploadStatus
from app.modules.jobs.service import jobs_out
from app.workers.jobs import ACTIVE_STATUSES

log = logging.getLogger(__name__)

PROCESSING_STATUSES = (SourceStatus.IMPORTING, SourceStatus.QUEUED, SourceStatus.PROCESSING)
SCHEDULED_STATUSES = (QueueStatus.SCHEDULED, QueueStatus.RENDERING)
RECENT_CLIPS = 6
ACTIVE_JOBS = 10
UPCOMING = 8


def _count(db: Session, stmt) -> int:  # noqa: ANN001
    return int(db.scalar(stmt) or 0)


def _tz(user: User) -> ZoneInfo:
    try:
        return ZoneInfo(user.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _days(user: User, days: int) -> list[date]:
    today = utcnow().astimezone(_tz(user)).date()
    return [today - timedelta(days=i) for i in range(days - 1, -1, -1)]


def _local_day(ts: datetime, tz: ZoneInfo) -> date:
    return ts.astimezone(tz).date()


def summary(db: Session, user: User, *, days: int = 14) -> dict[str, Any]:
    """All dashboard numbers for ``user`` (per-day series bucketed in the user's timezone)."""
    from app.modules.publishing.service import serialize_items
    from app.modules.scheduling.service import next_publish_time

    uid = user.id
    tz = _tz(user)
    day_list = _days(user, days)
    since = datetime.combine(day_list[0], datetime.min.time(), tzinfo=tz)

    clip_days = {d: 0 for d in day_list}
    for (created,) in db.execute(select(GeneratedClip.created_at).where(GeneratedClip.user_id == uid,
                                                                       GeneratedClip.created_at >= since)):
        d = _local_day(created, tz)
        if d in clip_days:
            clip_days[d] += 1
    pub_days = {d: {"youtube": 0, "instagram": 0} for d in day_list}
    for platform, published in db.execute(select(PublishingQueueItem.platform, PublishingQueueItem.published_at)
                                          .where(PublishingQueueItem.user_id == uid,
                                                 PublishingQueueItem.status == QueueStatus.PUBLISHED,
                                                 PublishingQueueItem.published_at >= since)):
        d = _local_day(published, tz)
        if d in pub_days and platform in pub_days[d]:
            pub_days[d][platform] += 1

    recent = list(db.scalars(select(GeneratedClip).where(GeneratedClip.user_id == uid,
                                                         GeneratedClip.removed_at.is_(None))
                             .order_by(GeneratedClip.created_at.desc()).limit(RECENT_CLIPS)))
    jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.user_id == uid,
                                                       ProcessingJob.status.in_(ACTIVE_STATUSES))
                           .order_by(ProcessingJob.created_at.desc()).limit(ACTIVE_JOBS)))
    upcoming = list(db.scalars(select(PublishingQueueItem).where(
        PublishingQueueItem.user_id == uid, PublishingQueueItem.status.in_(SCHEDULED_STATUSES),
        PublishingQueueItem.scheduled_at.is_not(None)).order_by(PublishingQueueItem.scheduled_at).limit(UPCOMING)))
    return {
        "total_videos": _count(db, select(func.count(VideoSource.id)).where(VideoSource.user_id == uid)),
        "total_clips": _count(db, select(func.count(GeneratedClip.id)).where(GeneratedClip.user_id == uid,
                                                                             GeneratedClip.removed_at.is_(None))),
        "processing_videos": _count(db, select(func.count(VideoSource.id)).where(
            VideoSource.user_id == uid, VideoSource.status.in_(PROCESSING_STATUSES))),
        "ready_to_upload": ready_to_upload(db, uid),
        "uploaded_clips": _count(db, select(func.count(GeneratedClip.id)).where(
            GeneratedClip.user_id == uid, GeneratedClip.upload_status == UploadStatus.UPLOADED)),
        "scheduled_posts": _count(db, select(func.count(PublishingQueueItem.id)).where(
            PublishingQueueItem.user_id == uid, PublishingQueueItem.status.in_(SCHEDULED_STATUSES))),
        "published_clips": _count(db, select(func.count(PublishingQueueItem.id)).where(
            PublishingQueueItem.user_id == uid, PublishingQueueItem.status == QueueStatus.PUBLISHED)),
        "failed_uploads": _count(db, select(func.count(PublishingQueueItem.id)).where(
            PublishingQueueItem.user_id == uid, PublishingQueueItem.status == QueueStatus.FAILED)),
        "next_publish_at": next_publish_time(db, user),
        "clips_per_day": [{"date": d.isoformat(), "count": n} for d, n in clip_days.items()],
        "published_per_day": [{"date": d.isoformat(), **v} for d, v in pub_days.items()],
        "recent_clips": _clips_out(db, recent),
        "active_jobs": jobs_out(db, jobs),
        "upcoming": serialize_items(db, upcoming),
    }


def ready_to_upload(db: Session, user_id) -> int:  # noqa: ANN001
    """Clips that are generated (current render) and not uploaded yet."""
    from app.modules.rendering.service import render_is_current

    rows = db.scalars(select(GeneratedClip).where(
        GeneratedClip.user_id == user_id, GeneratedClip.removed_at.is_(None),
        GeneratedClip.upload_status.in_((UploadStatus.NONE, UploadStatus.FAILED)),
        GeneratedClip.render_status == RenderStatus.RENDERED, GeneratedClip.output_key.is_not(None)))
    return sum(1 for c in rows if render_is_current(c))


def _clips_out(db: Session, clips: list[GeneratedClip]) -> list[Any]:
    from app.modules.clips.service import clip_outs

    return list(clip_outs(db, clips))
