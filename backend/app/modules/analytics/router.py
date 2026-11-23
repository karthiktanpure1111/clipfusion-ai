"""/analytics -- real internal counts, real platform statistics, AI estimates (labelled)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.modules.analytics import service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(user: CurrentUser, db: DbSession, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """Internal time series + platform totals (latest snapshot per publication) + AI score histogram."""
    return service.overview(db, user, days)


@router.get("/clips/{clip_id}")
def clip_analytics(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Publications of one clip with their real statistics history."""
    return service.clip_analytics(db, user, clip_id)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Queue an analytics job that fetches fresh statistics from YouTube/Instagram."""
    return service.request_refresh(db, user)
