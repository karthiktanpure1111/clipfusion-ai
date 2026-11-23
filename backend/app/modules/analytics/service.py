"""Analytics: real internal counts, real platform statistics (clearly separated) and AI
highlight-score distribution (labelled as estimates, never as views/retention)."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.deps import get_owned
from app.models import (
    AnalyticsSnapshot,
    GeneratedClip,
    PublishingHistory,
    PublishingQueueItem,
    User,
    VideoSource,
)
from app.models.enums import JobType, Platform, QueueStatus, ReviewStatus
from app.modules.scheduling.slots import get_zone

AI_SCORE_LABEL = "AI highlight score (estimate)"
AI_SCORE_NOTE = ("Estimated by ClipFusion from transcript, audio and visual signals. It is not a view count "
                 "or audience retention.")
PLATFORM_SOURCES = {Platform.YOUTUBE: "YouTube Data API (videos.list statistics)",
                    Platform.INSTAGRAM: "Instagram Graph API (media insights)"}
METRICS = ("views", "likes", "comments", "shares", "saves", "reach")


def _count(db: Session, stmt: Any) -> int:
    return int(db.scalar(stmt) or 0)


def _user_tz(user: User):  # noqa: ANN202
    try:
        return get_zone(user.timezone or "UTC")
    except ValueError:
        return get_zone("UTC")


def latest_snapshots(db: Session, user_id: uuid.UUID, clip_id: uuid.UUID | None = None) -> list[AnalyticsSnapshot]:
    """The most recent snapshot of each publication (platform + external id)."""
    S = AnalyticsSnapshot
    sub = (select(S.platform, S.external_id, func.max(S.fetched_at).label("latest"))
           .where(S.user_id == user_id).group_by(S.platform, S.external_id).subquery())
    stmt = select(S).join(sub, and_(S.platform == sub.c.platform, S.external_id == sub.c.external_id,
                                    S.fetched_at == sub.c.latest)).where(S.user_id == user_id)
    if clip_id is not None:
        stmt = stmt.where(S.clip_id == clip_id)
    return list(db.scalars(stmt))


def _internal_totals(db: Session, user: User, start: datetime) -> dict[str, Any]:
    Q, C = PublishingQueueItem, GeneratedClip
    decided = db.execute(select(C.review_status, func.count()).where(
        C.user_id == user.id, C.reviewed_at >= start,
        C.review_status.in_((ReviewStatus.APPROVED, ReviewStatus.REJECTED))).group_by(C.review_status)).all()
    by = dict(decided)
    approved, rejected = int(by.get(ReviewStatus.APPROVED, 0)), int(by.get(ReviewStatus.REJECTED, 0))
    return {
        "videos": _count(db, select(func.count()).select_from(VideoSource).where(VideoSource.user_id == user.id)),
        "clips_generated": _count(db, select(func.count()).select_from(C).where(C.user_id == user.id)),
        "clips_awaiting_approval": _count(db, select(func.count()).select_from(C).where(
            C.user_id == user.id, C.review_status == ReviewStatus.PENDING)),
        "scheduled": _count(db, select(func.count()).select_from(Q).where(
            Q.user_id == user.id, Q.status.in_((QueueStatus.SCHEDULED, QueueStatus.RENDERING)))),
        "published": _count(db, select(func.count()).select_from(Q).where(
            Q.user_id == user.id, Q.status == QueueStatus.PUBLISHED)),
        "failed": _count(db, select(func.count()).select_from(Q).where(
            Q.user_id == user.id, Q.status == QueueStatus.FAILED)),
        "approved_in_period": approved, "rejected_in_period": rejected,
        "approval_rate": round(approved / (approved + rejected), 4) if approved + rejected else None,
    }


def _series(db: Session, user: User, start: datetime, days: list[date]) -> list[dict[str, Any]]:
    tz = _user_tz(user)
    rows: dict[date, dict[str, int]] = {d: defaultdict(int) for d in days}

    def bump(ts: datetime | None, key: str) -> None:
        if ts is not None and (d := ts.astimezone(tz).date()) in rows:
            rows[d][key] += 1

    for (ts,) in db.execute(select(GeneratedClip.created_at).where(GeneratedClip.user_id == user.id,
                                                                   GeneratedClip.created_at >= start)):
        bump(ts, "clips_generated")
    for ts, platform in db.execute(select(PublishingQueueItem.published_at, PublishingQueueItem.platform).where(
            PublishingQueueItem.user_id == user.id, PublishingQueueItem.status == QueueStatus.PUBLISHED,
            PublishingQueueItem.published_at >= start)):
        bump(ts, f"published_{platform}")
    for (ts,) in db.execute(select(PublishingHistory.created_at).where(
            PublishingHistory.user_id == user.id, PublishingHistory.action == "failed",
            PublishingHistory.created_at >= start)):
        bump(ts, "publish_failures")
    keys = ("clips_generated", "published_youtube", "published_instagram", "publish_failures")
    return [{"date": d.isoformat(), **{k: rows[d][k] for k in keys}} for d in days]


def _platform_section(db: Session, user: User) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    snaps = latest_snapshots(db, user.id)
    out: dict[str, Any] = {}
    for platform in (Platform.YOUTUBE, Platform.INSTAGRAM):
        mine = [s for s in snaps if s.platform == platform]
        totals = {m: sum(getattr(s, m) or 0 for s in mine) if any(getattr(s, m) is not None for s in mine)
                  else None for m in METRICS}
        out[platform] = {"source": PLATFORM_SOURCES[platform], "publications_tracked": len(mine),
                         "last_fetched_at": max((s.fetched_at for s in mine), default=None), "totals": totals}
    clip_ids = {s.clip_id for s in snaps if s.clip_id}
    titles = dict(db.execute(select(GeneratedClip.id, GeneratedClip.title).where(
        GeneratedClip.id.in_(clip_ids))).all()) if clip_ids else {}
    ranked = sorted((s for s in snaps if s.views is not None), key=lambda s: s.views or 0, reverse=True)[:10]
    top = [{"clip_id": s.clip_id, "title": titles.get(s.clip_id, ""), "platform": s.platform,
            "external_id": s.external_id, "views": s.views, "likes": s.likes, "comments": s.comments,
            "fetched_at": s.fetched_at, "source": PLATFORM_SOURCES.get(s.platform, s.platform)} for s in ranked]
    return out, top


def _score_distribution(db: Session, user: User, start: datetime) -> dict[str, Any]:
    buckets = [0] * 10
    for (score,) in db.execute(select(GeneratedClip.highlight_score).where(
            GeneratedClip.user_id == user.id, GeneratedClip.created_at >= start)):
        buckets[min(9, max(0, int((score or 0.0) // 10)))] += 1
    return {"label": AI_SCORE_LABEL, "is_estimate": True, "note": AI_SCORE_NOTE,
            "buckets": [{"range": f"{i * 10}-{i * 10 + 10}", "count": n} for i, n in enumerate(buckets)]}


def overview(db: Session, user: User, days: int = 30) -> dict[str, Any]:
    """Dashboard analytics for the last ``days`` days (bucketed in the user's timezone)."""
    tz = _user_tz(user)
    today = utcnow().astimezone(tz).date()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    start = datetime.combine(day_list[0], time(0, 0), tzinfo=tz)
    platform, top = _platform_section(db, user)
    return {
        "period": {"days": days, "start": day_list[0].isoformat(), "end": today.isoformat(),
                   "timezone": str(tz.key)},
        "internal": {"totals": _internal_totals(db, user, start), "series": _series(db, user, start, day_list),
                     "source": "ClipFusion database (real counts)"},
        "platform": platform,
        "top_published_clips": top,
        "ai_highlight_scores": _score_distribution(db, user, start),
    }


def clip_analytics(db: Session, user: User, clip_id: uuid.UUID) -> dict[str, Any]:
    """Per-clip publications with real statistics history, plus clearly labelled AI estimate."""
    clip = get_owned(db, GeneratedClip, clip_id, user, name="Clip")
    items = list(db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clip.id)
                            .order_by(PublishingQueueItem.created_at)))
    snaps = list(db.scalars(select(AnalyticsSnapshot).where(AnalyticsSnapshot.clip_id == clip.id,
                                                            AnalyticsSnapshot.user_id == user.id)
                            .order_by(AnalyticsSnapshot.fetched_at)))
    by_item: dict[uuid.UUID | None, list[AnalyticsSnapshot]] = defaultdict(list)
    for s in snaps:
        by_item[s.queue_item_id].append(s)

    def snap(s: AnalyticsSnapshot) -> dict[str, Any]:
        return {"fetched_at": s.fetched_at, **{m: getattr(s, m) for m in METRICS}}

    pubs = [{"queue_item_id": i.id, "platform": i.platform, "account_id": i.connected_account_id,
             "status": i.status, "external_id": i.external_id, "external_url": i.external_url,
             "published_at": i.published_at, "source": PLATFORM_SOURCES.get(i.platform, i.platform),
             "latest": snap(by_item[i.id][-1]) if by_item.get(i.id) else None,
             "history": [snap(s) for s in by_item.get(i.id, [])]} for i in items]
    retention = None
    if clip.retention_score is not None:
        retention = {"value": clip.retention_score, "source": "YouTube Analytics API (source video, owner only)",
                     "label": "Mean audience watch ratio over this clip's range in the original video"}
    return {"clip_id": clip.id, "title": clip.title,
            "ai_highlight_score": {"value": clip.highlight_score, "label": AI_SCORE_LABEL, "is_estimate": True},
            "source_video_retention": retention, "publications": pubs}


def request_refresh(db: Session, user: User) -> dict[str, Any]:
    """Create (or reuse) an analytics job for the user and dispatch it. Commits."""
    from app.workers.jobs import create_job, dispatch_job

    job, created = create_job(db, user_id=user.id, job_type=JobType.ANALYTICS,
                              idempotency_key=f"analytics:{user.id}", max_attempts=2)
    if created:
        dispatch_job(db, job, "refresh_platform_analytics")
    else:
        db.commit()
    return {"job_id": job.id, "status": job.status, "created": created}
