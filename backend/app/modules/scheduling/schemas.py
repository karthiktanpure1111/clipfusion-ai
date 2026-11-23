"""HTTP schemas for /schedules."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ScheduleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    mode: Literal["review", "automatic"] | None = None
    frequency_type: Literal["interval", "times"] | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)
    times_of_day: list[str] | None = Field(default=None, max_length=48)
    days_of_week: list[int] | None = Field(default=None, max_length=7)
    timezone: str | None = Field(default=None, max_length=64)
    daily_limit: int | None = Field(default=None, ge=1, le=100)
    window_start: str | None = Field(default=None, pattern=HHMM)
    window_end: str | None = Field(default=None, pattern=HHMM)
    start_at: datetime | None = None
    auto_min_score: float | None = Field(default=None, ge=0, le=100)
    default_privacy: Literal["public", "unlisted", "private"] | None = None
    rights_confirmed: bool | None = None


class ScheduleCreate(ScheduleBase):
    connected_account_id: uuid.UUID


class ScheduleUpdate(ScheduleBase):
    pass


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connected_account_id: uuid.UUID
    platform: str
    name: str
    is_active: bool
    mode: str
    frequency_type: str
    interval_minutes: int | None
    times_of_day: list[str]
    days_of_week: list[int]
    timezone: str
    daily_limit: int
    window_start: str | None
    window_end: str | None
    start_at: datetime | None
    auto_min_score: float
    default_privacy: str
    rights_confirmed_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SlotOut(BaseModel):
    at: datetime
    local: str


class PreviewOut(BaseModel):
    schedule_id: uuid.UUID
    timezone: str
    slots: list[SlotOut]
