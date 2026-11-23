"""Sequential YouTube upload runs (docs/API_V2.md, "Sequential YouTube uploads")."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class UploadRunCreate(BaseModel):
    video_source_id: uuid.UUID
    connected_account_id: uuid.UUID
    mode: Literal["single", "next", "remaining"]
    clip_id: uuid.UUID | None = None
    privacy: Literal["public", "unlisted", "private"] | None = None  # default: user setting (private)
    rights_confirmed: bool = False

    @model_validator(mode="after")
    def _single(self) -> UploadRunCreate:
        if self.mode == "single" and self.clip_id is None:
            raise ValueError("clip_id is required for mode 'single'")
        return self


class UploadRunItemOut(BaseModel):
    clip_id: uuid.UUID
    sequence: int | None = None
    title: str
    status: Literal["pending", "uploading", "uploaded", "failed", "cancelled"]
    progress: float | None = None
    youtube_url: str | None = None
    error: str | None = None


class UploadRunOut(BaseModel):
    id: uuid.UUID
    video_source_id: uuid.UUID
    connected_account_id: uuid.UUID
    mode: Literal["single", "next", "remaining"]
    privacy: str
    status: Literal["running", "completed", "failed", "cancelled"]
    cancel_requested: bool = False
    total: int
    uploaded: int
    remaining: int
    current_clip_id: uuid.UUID | None = None
    failed_clip_id: uuid.UUID | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[UploadRunItemOut]
