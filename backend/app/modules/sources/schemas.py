"""Video source API models (see docs/API.md, Sources, and docs/API_V2.md)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.jobs.schemas import JobOut
from app.schemas.media import RetentionPoint, TranscriptSegment

# faster-whisper model names accepted from clients (arbitrary names would trigger arbitrary
# Hugging Face downloads).
WHISPER_MODELS = {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
                  "large-v1", "large-v2", "large-v3", "large-v3-turbo", "turbo", "distil-large-v2",
                  "distil-large-v3", "distil-medium.en", "distil-small.en"}
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class SourceOut(BaseModel):
    id: uuid.UUID
    source_type: str
    status: str
    title: str
    description: str | None = None
    original_filename: str | None = None
    file_size: int | None = None
    mime_type: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None
    has_file: bool
    original_available: bool = False      # the original long video is stored (not deleted)
    original_bytes: int = 0
    original_deleted_at: datetime | None = None
    processing_mode: str | None = None    # fast | balanced | best (last analysis)
    attribution: str | None = None        # credit line added to clip descriptions
    transcript_language: str | None = None
    thumbnail_url: str | None = None
    youtube_video_id: str | None = None
    youtube_url: str | None = None
    youtube_metadata: dict[str, Any] = Field(default_factory=dict)
    has_retention: bool = False
    retention_points: list[RetentionPoint] | None = None
    cloud_provider: str | None = None
    rights_confirmed: bool
    rights_confirmed_at: datetime | None = None
    analysis_options: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    processed_at: datetime | None = None
    clip_count: int = 0
    active_job: JobOut | None = None
    duplicate: bool = False
    created_at: datetime
    updated_at: datetime


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    attribution: str | None = Field(default=None, max_length=500)


class AnalysisRequest(BaseModel):
    """Processing options shared by every "start processing" request (API v2). Omitted values fall
    back to the user's settings (``default_processing_mode``, ``default_min_duration`` ...)."""

    mode: Literal["fast", "balanced", "best"] | None = None
    min_duration: float | None = Field(default=None, ge=5, le=180)
    max_duration: float | None = Field(default=None, ge=5, le=180)
    max_clips: int | None = Field(default=None, ge=1, le=50)
    clip_durations: list[float] | None = Field(default=None, min_length=1, max_length=6)
    language: str | None = Field(default=None, max_length=16)
    use_llm: bool | None = None
    whisper_model: str | None = None
    min_score: float | None = Field(default=None, ge=0, le=100)

    @field_validator("clip_durations")
    @classmethod
    def _durations(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and any(not 5 <= d <= 180 for d in v):
            raise ValueError("clip durations must be between 5 and 180 seconds")
        return sorted(set(v)) if v else v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str | None) -> str | None:
        if v in (None, "", "auto"):
            return None
        if not _LANG_RE.match(v):
            raise ValueError("language must be an ISO 639-1 code such as 'en' (or omitted for auto-detect)")
        return v

    @field_validator("whisper_model")
    @classmethod
    def _model(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if v not in WHISPER_MODELS:
            raise ValueError("unknown Whisper model; choose one of: " + ", ".join(sorted(WHISPER_MODELS)))
        return v

    @model_validator(mode="after")
    def _range(self) -> AnalysisRequest:
        if self.min_duration is not None and self.max_duration is not None and self.max_duration < self.min_duration:
            raise ValueError("max_duration must be at least min_duration")
        return self


class AnalyzeIn(AnalysisRequest):
    rights_confirmed: bool = False


class YouTubeSourceIn(BaseModel):
    url: str = Field(min_length=5, max_length=500)
    rights_confirmed: bool = False
    rights_statement: str | None = Field(default=None, max_length=2000)
    attribution: str | None = Field(default=None, max_length=500)


class AttachLinkIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    rights_confirmed: bool = False
    rights_statement: str | None = Field(default=None, max_length=2000)
    auto_analyze: bool = True
    analysis: AnalysisRequest | None = None


class YouTubeDownloadIn(BaseModel):
    quality: Literal["best", "2160", "1440", "1080", "720", "480"] | None = None  # default: the user's setting
    rights_confirmed: bool = False
    rights_statement: str | None = Field(default=None, max_length=2000)
    auto_analyze: bool = True
    analysis: AnalysisRequest | None = None


class CloudImportIn(BaseModel):
    provider: Literal["url", "google_drive"]
    url: str | None = Field(default=None, max_length=2000)
    file_id: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=500)
    rights_confirmed: bool = False
    rights_statement: str | None = Field(default=None, max_length=2000)
    attribution: str | None = Field(default=None, max_length=500)
    auto_analyze: bool = True
    analysis: AnalysisRequest | None = None

    @model_validator(mode="after")
    def _target(self) -> CloudImportIn:
        if self.provider == "url" and not self.url:
            raise ValueError("url is required for provider 'url'")
        if self.provider == "google_drive" and not (self.file_id or self.url):
            raise ValueError("file_id (or a Drive share url) is required for provider 'google_drive'")
        return self


class TranscriptOut(BaseModel):
    language: str | None = None
    model: str
    text: str
    word_count: int
    duration: float | None = None
    segments: list[TranscriptSegment]


class MediaUrlOut(BaseModel):
    url: str
    browser_playable: bool
    expires_in: int


class ManualClipIn(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str | None = Field(default=None, max_length=300)
    refine: bool = False

    @model_validator(mode="after")
    def _range(self) -> ManualClipIn:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class PipelineStage(BaseModel):
    key: Literal["ingestion", "audio", "transcription", "video_analysis", "highlights", "clip_generation",
                 "metadata", "completed"]
    label: str
    status: Literal["pending", "running", "done", "failed", "skipped"]
    progress: float | None = None  # 0..1 measured; null when not measurable
    started_at: datetime | None = None
    finished_at: datetime | None = None
    seconds: float | None = None
    message: str | None = None
    error: str | None = None


class PipelineClips(BaseModel):
    total: int = 0
    processing: int = 0
    generated: int = 0
    upload_failed: int = 0
    uploaded: int = 0
    removed: int = 0
    cleaned: int = 0


class PipelineDevice(BaseModel):
    transcription: Literal["cuda", "cpu"] | None = None
    model: str | None = None


class PipelineOut(BaseModel):
    source_id: uuid.UUID
    status: Literal["idle", "running", "completed", "failed"]
    mode: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float | None = None
    eta_seconds: float | None = None  # time left in the running stage, from its measured progress only
    stages: list[PipelineStage]
    clips: PipelineClips
    device: PipelineDevice


class DeleteOriginalOut(BaseModel):
    id: uuid.UUID
    status: Literal["deleted"] = "deleted"
    freed_bytes: int
    source: SourceOut
