"""HTTP schemas for /accounts (tokens are never part of any response)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AccountOut(BaseModel):
    id: uuid.UUID
    platform: str
    provider: str
    external_account_id: str
    display_name: str
    username: str | None = None
    avatar_url: str | None = None
    status: str
    scopes: list[str]
    has_drive_scope: bool
    has_analytics_scope: bool | None = None
    can_publish: bool
    metadata: dict[str, Any]
    token_expires_at: datetime | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConnectUrlOut(BaseModel):
    authorization_url: str
