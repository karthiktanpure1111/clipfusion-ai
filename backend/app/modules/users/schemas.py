"""User/profile API models (``UserOut`` and ``UserSettings`` are also used by the auth module)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ProcessingMode = Literal["fast", "balanced", "best"]
Privacy = Literal["public", "unlisted", "private"]


class UserSettings(BaseModel):
    """Per-user preferences stored in ``users.settings`` (unknown keys are preserved in the DB)."""

    model_config = ConfigDict(extra="ignore")

    default_clip_durations: list[float] = Field(default_factory=lambda: [30.0, 60.0], min_length=1, max_length=6)
    default_processing_mode: ProcessingMode = "balanced"
    default_min_duration: float = Field(default=20.0, ge=5, le=180)
    default_max_duration: float = Field(default=60.0, ge=5, le=180)
    default_max_clips: int = Field(default=10, ge=1, le=50)
    default_privacy: Privacy = "private"
    auto_delete_original: bool = False  # delete the original once every clip is uploaded or removed
    # Download public YouTube videos from their link with yt-dlp (off by default: YouTube's Terms of
    # Service don't allow downloading; the user accepts that risk by switching it on).
    youtube_download_enabled: bool = False
    youtube_download_quality: Literal["best", "2160", "1440", "1080", "720"] = "best"
    default_use_llm: bool = True
    default_framing_preset_id: uuid.UUID | None = None
    publishing_paused: bool = False
    notify_email: bool = False

    @field_validator("default_clip_durations")
    @classmethod
    def _durations(cls, v: list[float]) -> list[float]:
        for d in v:
            if not 5 <= d <= 180:
                raise ValueError("clip durations must be between 5 and 180 seconds")
        return sorted(set(v))

    @model_validator(mode="after")
    def _range(self) -> UserSettings:
        if self.default_max_duration < self.default_min_duration:
            raise ValueError("default_max_duration must be at least default_min_duration")
        return self


class UserSettingsPatch(BaseModel):
    """Partial settings update; only the fields that are sent are merged."""

    model_config = ConfigDict(extra="forbid")

    default_clip_durations: list[float] | None = Field(default=None, min_length=1, max_length=6)
    default_processing_mode: ProcessingMode | None = None
    default_min_duration: float | None = Field(default=None, ge=5, le=180)
    default_max_duration: float | None = Field(default=None, ge=5, le=180)
    default_max_clips: int | None = Field(default=None, ge=1, le=50)
    default_privacy: Privacy | None = None
    auto_delete_original: bool | None = None
    youtube_download_enabled: bool | None = None
    youtube_download_quality: Literal["best", "2160", "1440", "1080", "720"] | None = None
    default_use_llm: bool | None = None
    default_framing_preset_id: uuid.UUID | None = None
    publishing_paused: bool | None = None
    notify_email: bool | None = None

    @field_validator("default_clip_durations")
    @classmethod
    def _durations(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        return UserSettings._durations(v)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    avatar_url: str | None = None
    timezone: str
    role: str
    settings: UserSettings
    has_password: bool
    google_linked: bool
    created_at: datetime


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    settings: UserSettingsPatch | None = None


class PasswordChange(BaseModel):
    current_password: str | None = Field(default=None, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)
