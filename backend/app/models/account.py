from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, JSONType, UTCDateTime, utcnow
from app.models.user import TimestampMixin


class ConnectedAccount(TimestampMixin, Base):
    """A social account connected through official OAuth. Tokens are Fernet-encrypted."""

    __tablename__ = "connected_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", "external_account_id", name="uq_connected_accounts_identity"),
        Index("ix_connected_accounts_user_platform", "user_id", "platform"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)       # Platform enum
    provider: Mapped[str] = mapped_column(String(30), nullable=False)       # google | instagram_login | facebook_login
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)  # channel id / IG user id
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scopes: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # AccountStatus
    # Provider-specific details: {"channel_id", "uploads_playlist_id"} / {"ig_user_id", "page_id", "account_type"}
    account_metadata: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ApiQuotaUsage(Base):
    """Per-day API quota accounting (YouTube units, Instagram publishes, LLM calls)."""

    __tablename__ = "api_quota_usage"
    __table_args__ = (UniqueConstraint("provider", "scope_key", "day", name="uq_api_quota_usage_bucket"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)  # youtube | instagram | anthropic
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)  # "global" or connected account id
    day: Mapped[date] = mapped_column(Date, nullable=False)
    units_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow, nullable=False)
