"""/storage — ClipFusion-managed storage usage (API v2)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import CurrentUser, DbSession
from app.modules.storage import service

router = APIRouter(prefix="/storage", tags=["storage"])


class StorageClips(BaseModel):
    total: int
    generated: int
    uploaded: int
    removed: int
    cleaned: int


class StorageSource(BaseModel):
    id: uuid.UUID
    title: str
    original_bytes: int
    clip_bytes: int
    clips_total: int
    clips_done: int
    all_done: bool
    original_available: bool


class StorageOut(BaseModel):
    managed_bytes: int
    originals_bytes: int
    renders_bytes: int
    proxies_bytes: int
    thumbnails_bytes: int
    temp_bytes: int
    freed_bytes_total: int
    disk_free_bytes: int
    clips: StorageClips
    sources: list[StorageSource]


@router.get("", response_model=StorageOut)
def storage(user: CurrentUser, db: DbSession) -> dict:
    """Sizes of the files ClipFusion stores for you (never scans the rest of the disk)."""
    return service.storage_summary(db, user)
