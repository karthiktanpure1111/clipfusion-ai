from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.models.user import TimestampMixin


class PublishingSchedule(TimestampMixin, Base):
    """Per-account publishing cadence. Slots are computed in the schedule's timezone."""

    __tablename__ = "publishing_schedules"
    __table_args__ = (Index("ix_publishing_schedules_user_active", "user_id", "is_active"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    connected_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # False = paused
    mode: Mapped[str] = mapped_column(String(20), default="review", nullable=False)  # PublishMode
    frequency_type: Mapped[str] = mapped_column(String(20), default="interval", nullable=False)  # FrequencyType
    interval_minutes: Mapped[int | None] = mapped_column(Integer)  # 60, 120, 180, 360 or custom
    times_of_day: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # ["09:00", "18:30"]
    days_of_week: Mapped[list] = mapped_column(JSONType, default=lambda: [0, 1, 2, 3, 4, 5, 6], nullable=False)  # Mon=0
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    daily_limit: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    window_start: Mapped[str | None] = mapped_column(String(5))  # optional active window for interval mode, "08:00"
    window_end: Mapped[str | None] = mapped_column(String(5))    # "23:00"
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Automatic mode rules: minimum AI score and whether rendered clips are queued without review
    auto_min_score: Mapped[float] = mapped_column(default=0.0, nullable=False)
    default_privacy: Mapped[str] = mapped_column(String(20), default="public", nullable=False)  # YouTube privacy
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PublishingQueueItem(TimestampMixin, Base):
    """One clip -> one account publication. ``idempotency_key`` (clip:account) prevents duplicates."""

    __tablename__ = "publishing_queue"
    __table_args__ = (
        Index("ix_publishing_queue_due", "status", "scheduled_at"),
        Index("ix_publishing_queue_user_sched", "user_id", "scheduled_at"),
        Index("ix_publishing_queue_account_sched", "connected_account_id", "scheduled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generated_clips.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    connected_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="CASCADE"), nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publishing_schedules.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # QueueStatus
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Snapshot of what will be published: {title, description, tags, caption, hashtags, privacy, made_for_kids}
    payload: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255))        # YouTube video id / IG media id
    external_container_id: Mapped[str | None] = mapped_column(String(255))  # IG container id
    upload_session_url: Mapped[str | None] = mapped_column(Text)         # YouTube resumable session URI
    external_url: Mapped[str | None] = mapped_column(String(1000))
    remote_status: Mapped[str | None] = mapped_column(String(60))
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    locked_by: Mapped[str | None] = mapped_column(String(100))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    rights_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # "timeline" = spaced out from the video page or by auto-schedule (missed slots shift, order kept)
    origin: Mapped[str | None] = mapped_column(String(20))


class PublishingHistory(Base):
    """Append-only audit log of publishing actions and their outcomes."""

    __tablename__ = "publishing_history"
    __table_args__ = (Index("ix_publishing_history_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("publishing_queue.id", ondelete="SET NULL"))
    clip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generated_clips.id", ondelete="SET NULL"),
                                                      index=True)
    connected_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # upload|publish|status|retry|cancel|failed
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(String(1000))
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)  # sanitized API response
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class AnalyticsSnapshot(Base):
    """Real platform statistics fetched from official APIs for published clips."""

    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        Index("ix_analytics_snapshots_clip_platform_time", "clip_id", "platform", "fetched_at"),
        Index("ix_analytics_snapshots_user_time", "user_id", "fetched_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    clip_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generated_clips.id", ondelete="SET NULL"))
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("publishing_queue.id", ondelete="SET NULL"))
    connected_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connected_accounts.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    saves: Mapped[int | None] = mapped_column(Integer)
    reach: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)  # NotificationType
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    link: Mapped[str | None] = mapped_column(String(500))  # frontend path, e.g. /app/clips/{id}
    data: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)
