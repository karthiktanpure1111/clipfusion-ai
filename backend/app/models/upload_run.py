from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime
from app.models.user import TimestampMixin


class UploadRun(TimestampMixin, Base):
    """A sequential YouTube upload of a source's clips (API v2, ``/upload-runs``).

    Clips are uploaded one at a time in sequence order through the publishing queue. The run
    stops at the first failure (Retry resumes from that clip) and Cancel stops after the
    current transfer. At most one run is ``running`` per connected account (partial unique
    index), so an account never uploads two clips of a run at once."""

    __tablename__ = "upload_runs"
    __table_args__ = (
        Index("ix_upload_runs_user_created", "user_id", "created_at"),
        Index("ix_upload_runs_source_created", "video_source_id", "created_at"),
        Index("ix_upload_runs_status", "status"),
        Index("uq_upload_runs_account_running", "connected_account_id", unique=True,
              postgresql_where=text("status = 'running'"), sqlite_where=text("status = 'running'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    video_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("video_sources.id", ondelete="CASCADE"),
                                                       nullable=False)
    connected_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)                       # UploadRunMode
    privacy: Mapped[str] = mapped_column(String(20), default="private", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)  # UploadRunStatus
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_clip_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    failed_clip_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_error: Mapped[str | None] = mapped_column(Text)
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    items: Mapped[list[UploadRunItem]] = relationship(back_populates="run", cascade="all, delete-orphan",
                                                      order_by="UploadRunItem.position")


class UploadRunItem(TimestampMixin, Base):
    """One clip of an upload run, in upload order (``position`` = chronological order at creation)."""

    __tablename__ = "upload_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "position", name="uq_upload_run_items_position"),
        UniqueConstraint("run_id", "clip_id", name="uq_upload_run_items_clip"),
        Index("ix_upload_run_items_clip", "clip_id"),
        Index("ix_upload_run_items_queue_item", "queue_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("upload_runs.id", ondelete="CASCADE"), nullable=False)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_clips.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # UploadRunItemStatus
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publishing_queue.id", ondelete="SET NULL"))
    progress: Mapped[float | None] = mapped_column(Float)
    youtube_video_id: Mapped[str | None] = mapped_column(String(32))
    youtube_url: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    run: Mapped[UploadRun] = relationship(back_populates="items")
