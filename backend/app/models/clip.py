from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, JSONType, UTCDateTime
from app.models.user import TimestampMixin


class GeneratedClip(TimestampMixin, Base):
    __tablename__ = "generated_clips"
    __table_args__ = (
        UniqueConstraint("video_source_id", "start_time", "end_time", name="uq_generated_clips_range"),
        Index("ix_generated_clips_user_created", "user_id", "created_at"),
        Index("ix_generated_clips_user_review", "user_id", "review_status"),
        Index("ix_generated_clips_source_score", "video_source_id", "highlight_score"),
        Index("ix_generated_clips_source_start", "video_source_id", "start_time"),
        Index("ix_generated_clips_user_upload", "user_id", "upload_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    video_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_sources.id", ondelete="CASCADE"),
                                                       nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)  # seconds in source
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    duration: Mapped[float] = mapped_column(Float, nullable=False)
    target_duration: Mapped[float | None] = mapped_column(Float)

    # AI estimate (0-100). NOT viewership. Breakdown = per-signal contributions.
    highlight_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    category: Mapped[str | None] = mapped_column(String(40))
    reasons: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    transcript_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hook_text: Mapped[str | None] = mapped_column(Text)
    detection_method: Mapped[str] = mapped_column(String(30), default="signals", nullable=False)  # signals|signals+llm|manual
    # Mean real YouTube audience-watch-ratio over the clip range, only if real analytics were available.
    retention_score: Mapped[float | None] = mapped_column(Float)

    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # ClipMetadata: {"youtube": {...}, "instagram": {...}, "source": "llm"|"extractive", "generated_at": iso}
    social_metadata: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    # RenderOptions: normalize_audio, remove_silence, intro/outro asset ids, watermark ...
    render_options: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    # Legacy (approve/reject was removed in API v2); kept so existing rows stay valid.
    review_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # ReviewStatus
    render_status: Mapped[str] = mapped_column(String(20), default="not_rendered", nullable=False)  # RenderStatus
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    proxy_key: Mapped[str | None] = mapped_column(String(600))      # landscape preview mp4 (editor)
    proxy_start: Mapped[float | None] = mapped_column(Float)        # source time at proxy t=0 (padding)
    thumbnail_key: Mapped[str | None] = mapped_column(String(600))
    output_key: Mapped[str | None] = mapped_column(String(600))     # rendered 1080x1920 mp4
    output_size: Mapped[int | None] = mapped_column(BigInteger)
    output_duration: Mapped[float | None] = mapped_column(Float)
    render_hash: Mapped[str | None] = mapped_column(String(64))     # hash of inputs used for output_key
    rendered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    render_error: Mapped[str | None] = mapped_column(Text)
    media_bytes: Mapped[int | None] = mapped_column(BigInteger)     # bytes under users/{uid}/clips/{id}/

    # Remove Clip (soft delete): media deleted, a small record is kept; never re-created by re-analysis.
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # YouTube upload (sequential upload runs / publishing queue) -- UploadStatus
    upload_status: Mapped[str] = mapped_column(String(20), default="none", server_default="none", nullable=False)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32))
    youtube_url: Mapped[str | None] = mapped_column(String(500))
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    upload_error: Mapped[str | None] = mapped_column(Text)

    # Media cleanup after a verified upload -- CleanupStatus
    cleanup_status: Mapped[str] = mapped_column(String(20), default="none", server_default="none", nullable=False)
    cleaned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleanup_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleanup_error: Mapped[str | None] = mapped_column(Text)
    freed_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)

    framing: Mapped[ClipFramingSettings | None] = relationship(back_populates="clip", uselist=False,
                                                               cascade="all, delete-orphan")
    subtitles: Mapped[ClipSubtitleStyle | None] = relationship(back_populates="clip", uselist=False,
                                                               cascade="all, delete-orphan")


class ClipFramingSettings(TimestampMixin, Base):
    __tablename__ = "clip_framing_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_clips.id", ondelete="CASCADE"),
                                               unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)  # FramingMode
    config: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)   # app.schemas.framing.FramingConfig
    # app.schemas.framing.ClipVisionAnalysis (face tracks, activity grid, facecam candidates)
    vision: Mapped[dict | None] = mapped_column(JSONType)
    warnings: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # list[FramingWarning]
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("framing_presets.id", ondelete="SET NULL"))

    clip: Mapped[GeneratedClip] = relationship(back_populates="framing")


class ClipSubtitleStyle(TimestampMixin, Base):
    """Per-clip subtitle style + editable captions (table: subtitle_styles)."""

    __tablename__ = "subtitle_styles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_clips.id", ondelete="CASCADE"),
                                               unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    style: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)     # SubtitleStyle
    captions: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # list[Caption]
    language: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)  # auto | edited
    preset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subtitle_presets.id", ondelete="SET NULL"))

    clip: Mapped[GeneratedClip] = relationship(back_populates="subtitles")


class FramingPreset(TimestampMixin, Base):
    __tablename__ = "framing_presets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_framing_presets_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)  # FramingConfig without keyframes
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SubtitlePreset(TimestampMixin, Base):
    """Saved subtitle presets. Built-in templates have user_id NULL and is_template True."""

    __tablename__ = "subtitle_presets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_subtitle_presets_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(40))  # gaming|podcast|minimal|bold_viral|professional|reaction
    style: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CustomFont(Base):
    __tablename__ = "custom_fonts"
    __table_args__ = (UniqueConstraint("user_id", "family_name", "weight", "italic", name="uq_custom_fonts_variant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    family_name: Mapped[str] = mapped_column(String(120), nullable=False)
    weight: Mapped[int] = mapped_column(default=400, nullable=False)
    italic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False)
    license_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class UserAsset(Base):
    """Intro/outro clips, watermark images and background images used by the renderer."""

    __tablename__ = "user_assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                                               index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # AssetKind
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(600), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column()
    height: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
