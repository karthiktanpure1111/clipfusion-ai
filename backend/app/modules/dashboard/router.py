"""/dashboard — summary numbers, charts and lists for the dashboard page."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import CurrentUser, DbSession
from app.modules.dashboard import service
from app.modules.jobs.schemas import JobOut
from app.modules.publishing.schemas import QueueItemOut

try:  # ClipOut is owned by the clips module; used for the OpenAPI response model only.
    from app.modules.clips.schemas import ClipOut as _ClipOut
except ModuleNotFoundError:  # pragma: no cover - clips module not installed yet
    _ClipOut = dict[str, Any]  # type: ignore[misc]

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DayCount(BaseModel):
    date: str
    count: int


class DayPublished(BaseModel):
    date: str
    youtube: int
    instagram: int


class QuotaBucket(BaseModel):
    used: int
    limit: int
    remaining: int


class YouTubeQuota(BaseModel):
    """Today's YouTube Data API usage per bucket (ClipFusion's own count; resets at midnight Pacific Time)."""

    uploads: QuotaBucket  # videos.insert calls (own bucket, default 100/day)
    search: QuotaBucket  # search.list calls (own bucket, default 100/day)
    units: QuotaBucket  # all other methods (default 10,000 units/day)
    resets_at: datetime


class DashboardSummary(BaseModel):
    total_videos: int
    total_clips: int
    processing_videos: int
    ready_to_upload: int  # generated (current render) and not uploaded yet
    uploaded_clips: int
    scheduled_posts: int
    published_clips: int
    failed_uploads: int
    next_publish_at: datetime | None = None
    clips_per_day: list[DayCount]
    published_per_day: list[DayPublished]
    recent_clips: list[_ClipOut]  # type: ignore[valid-type]
    active_jobs: list[JobOut]
    upcoming: list[QueueItemOut]
    youtube_quota: YouTubeQuota | None = None


@router.get("/summary", response_model=DashboardSummary)
def summary(user: CurrentUser, db: DbSession, days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
    """Real counts (videos, clips, queue), per-day series, recent clips, active jobs and upcoming posts."""
    return service.summary(db, user, days=days)
