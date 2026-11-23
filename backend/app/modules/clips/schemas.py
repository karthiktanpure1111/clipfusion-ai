"""HTTP models of the clips API (docs/API.md section Clips + docs/API_V2.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.media import ClipMetadata
from app.schemas.render import RenderOptions

ClipStatusName = Literal["pending", "processing", "generated", "uploading", "uploaded", "upload_failed",
                         "removed", "cleaned"]


class ClipOut(BaseModel):
    id: uuid.UUID
    video_source_id: uuid.UUID
    source_title: str
    sequence: int | None = None  # 1-based position by source start time among non-removed clips
    status: ClipStatusName
    start_time: float
    end_time: float
    duration: float
    target_duration: float | None = None
    highlight_score: float  # AI highlight score (estimate, 0-100) - NOT viewership
    score_breakdown: dict[str, float]
    category: str | None = None
    reasons: list[str]
    transcript_text: str
    hook_text: str | None = None
    detection_method: str
    retention_score: float | None = None  # REAL YouTube audience watch ratio, or null
    title: str
    social_metadata: ClipMetadata  # title/caption, description, hashtags (YouTube + Instagram)
    render_status: str
    render_is_current: bool
    needs_framing_review: bool = False  # blocking framing warnings stop the automatic render
    thumbnail_url: str | None = None
    proxy_url: str | None = None
    proxy_start: float | None = None
    output_url: str | None = None
    output_duration: float | None = None
    rendered_at: datetime | None = None
    framing_mode: str | None = None
    warning_count: int
    blocking_warning_count: int
    warnings_acknowledged: bool
    upload_status: Literal["none", "uploading", "uploaded", "failed"] = "none"
    scheduled_at: datetime | None = None  # next scheduled upload of the clip (timeline / calendar)
    scheduled_item_id: uuid.UUID | None = None  # its publishing-queue item (cancel / publish now)
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    uploaded_at: datetime | None = None
    upload_error: str | None = None
    cleanup_status: Literal["none", "cleaned", "failed"] = "none"
    media_available: bool = False
    media_bytes: int = 0
    freed_bytes: int = 0
    removed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class QueueItemSummary(BaseModel):
    id: uuid.UUID
    platform: str
    status: str
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    external_url: str | None = None
    last_error: str | None = None


class SourceSummary(BaseModel):
    id: uuid.UUID
    title: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    attribution: str | None = None
    original_available: bool = False


class ClipDetail(ClipOut):
    render_options: RenderOptions
    render_error: str | None = None
    active_jobs: list[Any]  # JobOut (app.modules.jobs.schemas)
    queue_items: list[QueueItemSummary]  # upload/publishing history of the clip
    source: SourceSummary


class ClipPatch(BaseModel):
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=300)
    social_metadata: ClipMetadata | None = None
    render_options: RenderOptions | None = None


class RemoveOut(BaseModel):
    id: uuid.UUID
    status: Literal["removed"] = "removed"
    freed_bytes: int


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: Literal["render", "remove"]
    acknowledge_warnings: bool = False


class BulkError(BaseModel):
    code: str
    message: str


class BulkResult(BaseModel):
    id: uuid.UUID
    ok: bool
    error: BulkError | None = None


class BulkOut(BaseModel):
    results: list[BulkResult]


class MetadataRegenerateIn(BaseModel):
    use_llm: bool | None = None
