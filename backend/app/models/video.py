from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.models.user import TimestampMixin


class VideoSource(TimestampMixin, Base):
    """A long-form source video. Processing always uses an authorised original file
    (upload or cloud import); YouTube links provide official metadata/analytics only."""

    __tablename__ = "video_sources"
    __table_args__ = (
        Index("ix_video_sources_user_created", "user_id", "created_at"),
        Index("ix_video_sources_user_status", "user_id", "status"),
        Index("ix_video_sources_user_checksum", "user_id", "checksum_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # SourceType
    status: Mapped[str] = mapped_column(String(30), nullable=False)       # SourceStatus
    title: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # Original file (absent for metadata-only YouTube links until the user attaches the file)
    original_filename: Mapped[str | None] = mapped_column(String(300))
    storage_key: Mapped[str | None] = mapped_column(String(600))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    thumbnail_key: Mapped[str | None] = mapped_column(String(600))

    # Probed media info (app.schemas.media.MediaInfo)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    has_audio: Mapped[bool | None] = mapped_column(Boolean)
    media_info: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    # YouTube (official Data API / Analytics API only)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32), index=True)
    youtube_url: Mapped[str | None] = mapped_column(String(500))
    youtube_metadata: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    # Real audience-retention curve from YouTube Analytics (owner only):
    # {"points": [{"ratio": 0.01, "audience_watch_ratio": 0.93, "relative_performance": 0.6}], "fetched_at": iso,
    #  "connected_account_id": str}
    retention_data: Mapped[dict | None] = mapped_column(JSONType)

    # Cloud import
    cloud_provider: Mapped[str | None] = mapped_column(String(30))  # url | google_drive
    cloud_reference: Mapped[str | None] = mapped_column(String(2000))

    # Rights confirmation (required before processing)
    rights_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    rights_statement: Mapped[str | None] = mapped_column(Text)

    # Analysis options used last (clip durations etc.) and error info
    analysis_options: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # API v2: processing mode used last (fast|balanced|best), credit line for metadata, and when the
    # original long video was deleted to free space (clips' records/links stay).
    processing_mode: Mapped[str | None] = mapped_column(String(20))
    attribution: Mapped[str | None] = mapped_column(Text)
    original_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # The video's framing (FramingConfig without keyframes): saved from one clip, applied to all its clips
    framing_template: Mapped[dict | None] = mapped_column(JSONType)

    transcript: Mapped[Transcript | None] = relationship(back_populates="video_source", uselist=False,
                                                         cascade="all, delete-orphan")
    analysis: Mapped[VideoAnalysis | None] = relationship(back_populates="video_source", uselist=False,
                                                          cascade="all, delete-orphan")


class UploadSession(Base):
    """Resumable chunked upload. Chunks are written to TEMP_DIR/uploads/{id}/{index}.part."""

    __tablename__ = "upload_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    video_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("video_sources.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    total_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    received_chunks: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="uploading", nullable=False)  # uploading|completed|aborted
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class ProcessingJob(TimestampMixin, Base):
    """Persistent background job record. The worker updates stage/progress/heartbeat;
    stale RUNNING jobs are re-queued by the reaper (job persistence across restarts)."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_processing_jobs_user_created", "user_id", "created_at"),
        Index("ix_processing_jobs_status", "status"),
        Index("ix_processing_jobs_source", "video_source_id"),
        Index("ix_processing_jobs_clip", "clip_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    video_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("video_sources.id", ondelete="CASCADE"))
    clip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generated_clips.id", ondelete="CASCADE"))
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publishing_queue.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)   # JobType
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)  # JobStatus
    stage: Mapped[str] = mapped_column(String(60), default="queued", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0..1
    message: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(100))
    params: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    # Per-stage pipeline progress/timings of an analysis job (GET /sources/{id}/pipeline):
    # {"audio": {"status", "started_at", "finished_at", "seconds", "progress", "message", "error"}, ...,
    #  "_device": {"transcription": "cuda"|"cpu", "model": str}}
    stages: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    # Prevents duplicate concurrent jobs, e.g. "analysis:{source_id}" while one is active.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Transcript(Base):
    """Full-source transcript (app.schemas.media.TranscriptData in ``segments``)."""

    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_sources.id", ondelete="CASCADE"), unique=True, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    segments: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # list[TranscriptSegment]
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    video_source: Mapped[VideoSource] = relationship(back_populates="transcript")


class VideoAnalysis(Base):
    """Signal-level analysis artefacts reused by clipping, silence removal and the UI."""

    __tablename__ = "video_analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    video_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video_sources.id", ondelete="CASCADE"), unique=True, nullable=False)
    audio: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)     # AudioAnalysis
    scenes: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)    # list[float] cut times
    visual: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)    # VisualActivity
    highlight_debug: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)

    video_source: Mapped[VideoSource] = relationship(back_populates="analysis")


__all__ = ["VideoSource", "UploadSession", "ProcessingJob", "Transcript", "VideoAnalysis"]
