"""HTTP schemas for /publishing."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.publishing.payloads import InstagramPayloadPatch, YouTubePayloadPatch


class QueueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: uuid.UUID
    account_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    scheduled_at: datetime | None = None  # else next slot of the account's schedule, else now + 5 min
    youtube: YouTubePayloadPatch | None = None
    instagram: InstagramPayloadPatch | None = None
    rights_confirmed: bool = False


class QueueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_at: datetime | None = None
    payload: dict[str, Any] | None = None  # partial; validated against the item's platform


class QueueItemOut(BaseModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    clip_title: str = ""
    thumbnail_url: str | None = None
    connected_account_id: uuid.UUID
    account_name: str = ""
    schedule_id: uuid.UUID | None = None
    platform: str
    status: str
    scheduled_at: datetime | None = None
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    last_error: str | None = None
    external_id: str | None = None
    external_url: str | None = None
    remote_status: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    queue_item_id: uuid.UUID | None = None
    clip_id: uuid.UUID | None = None
    connected_account_id: uuid.UUID | None = None
    platform: str
    action: str
    status: str
    external_id: str | None = None
    external_url: str | None = None
    message: str | None = None
    created_at: datetime


class CalendarEntry(BaseModel):
    kind: str  # "queue" | "history"
    id: uuid.UUID
    at: datetime
    platform: str
    status: str
    clip_id: uuid.UUID | None = None
    clip_title: str = ""
    thumbnail_url: str | None = None
    account_id: uuid.UUID | None = None
    account_name: str = ""
    external_url: str | None = None


class AccountNext(BaseModel):
    account_id: uuid.UUID
    platform: str
    display_name: str
    next_publish_at: datetime | None = None
    next_free_slot: datetime | None = None


class NextOut(BaseModel):
    paused: bool
    next_publish_at: datetime | None = None
    accounts: list[AccountNext]


class PausedOut(BaseModel):
    paused: bool


class TimelineCreate(BaseModel):
    """Schedule a video's clips one by one, ``gap_minutes`` apart (POST /publishing/timeline)."""

    model_config = ConfigDict(extra="forbid")

    video_source_id: uuid.UUID
    connected_account_id: uuid.UUID
    gap_minutes: int = Field(default=60, ge=15, le=10080)
    start_at: datetime | None = None  # default: one gap after the account's last post, or right away
    privacy: Literal["public", "unlisted", "private"] | None = None  # default: user setting
    clip_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)  # default: every ready clip
    rights_confirmed: bool = False


class TimelineCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_source_id: uuid.UUID


class TimelineOut(BaseModel):
    scheduled: int
    first_at: datetime | None = None
    last_at: datetime | None = None
    items: list[QueueItemOut]


class TimelineNextOut(BaseModel):
    next_at: datetime
    slots: list[datetime] = Field(default_factory=list)


class CancelledOut(BaseModel):
    cancelled: int


class AutoScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    connected_account_id: uuid.UUID | None = None
    gap_minutes: int = Field(default=60, ge=15, le=10080)
    privacy: Literal["public", "unlisted", "private"] | None = None
    rights_confirmed: bool = False


class AutoScheduleOut(BaseModel):
    enabled: bool
    connected_account_id: uuid.UUID | None = None
    account_name: str | None = None
    gap_minutes: int
    privacy: str | None = None
    since: datetime | None = None  # clips created from then on are auto-scheduled
    rights_confirmed_at: datetime | None = None  # unused: the rights confirmed when adding a video apply
    next_slot_at: datetime | None = None
