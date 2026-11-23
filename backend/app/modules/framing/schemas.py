"""HTTP models of the framing editor API (docs/API.md, section Framing)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.framing import ClipVisionAnalysis, CompiledLayout, FramingConfig, FramingWarning


class SourceSummary(BaseModel):
    width: int
    height: int
    fps: float
    duration: float | None = None


class FramingOut(BaseModel):
    applied_to_clips: int | None = None  # PUT with apply_to_video: other clips of the video that got it
    clip_id: uuid.UUID
    mode: str
    config: FramingConfig
    vision: ClipVisionAnalysis | None = None
    warnings: list[FramingWarning]
    warnings_acknowledged: bool
    auto_generated: bool
    compiled: CompiledLayout
    source: SourceSummary
    clip_start: float
    clip_end: float
    clip_duration: float
    proxy_url: str | None = None
    proxy_start: float | None = None


class FramingConfigIn(BaseModel):
    config: FramingConfig


class FramingCheckOut(BaseModel):
    config: FramingConfig
    warnings: list[FramingWarning]
    compiled: CompiledLayout


class FramingApplyIn(BaseModel):
    clip_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    config: FramingConfig | None = None
    preset_id: uuid.UUID | None = None


class ErrorInfo(BaseModel):
    code: str
    message: str


class FramingApplyResult(BaseModel):
    clip_id: uuid.UUID
    ok: bool
    warnings: list[FramingWarning] = Field(default_factory=list)
    error: ErrorInfo | None = None


class FramingApplyOut(BaseModel):
    results: list[FramingApplyResult]


class FramingPresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: FramingConfig
    is_default: bool = False


class FramingPresetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: FramingConfig | None = None
    is_default: bool | None = None


class FramingPresetOut(BaseModel):
    id: uuid.UUID
    name: str
    mode: str
    config: dict[str, Any]
    is_default: bool
    created_at: datetime
    updated_at: datetime
