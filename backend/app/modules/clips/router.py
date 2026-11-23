"""/clips — list (sequence order), detail, trim/edit, Remove Clip, bulk actions, metadata regeneration."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import GeneratedClip
from app.modules.clips import service
from app.modules.clips.media import job_out, job_out_model
from app.modules.clips.schemas import (
    BulkIn,
    BulkOut,
    ClipDetail,
    ClipOut,
    ClipPatch,
    MetadataRegenerateIn,
    RemoveOut,
)
from app.modules.common.pagination import Page, PageParams, page_params

router = APIRouter(prefix="/clips", tags=["clips"])
JobOut = job_out_model()


def _clip(db: DbSession, user: CurrentUser, clip_id: uuid.UUID) -> GeneratedClip:
    return get_owned(db, GeneratedClip, clip_id, user, name="Clip")


@router.get("", response_model=Page[ClipOut])
def list_clips(user: CurrentUser, db: DbSession, params: PageParams = Depends(page_params),
               video_source_id: uuid.UUID | None = None, status: str | None = None,
               render_status: str | None = None, min_score: float | None = Query(None, ge=0, le=100),
               q: str | None = Query(None, max_length=200),
               sort: Literal["sequence", "score", "created", "start"] | None = None,
               include_removed: bool = False) -> Page[ClipOut]:
    """The user's clips. ``status`` accepts a comma-separated list of derived statuses. Default sort:
    ``sequence`` (source timestamp order) when ``video_source_id`` is given, else ``created``."""
    return service.list_clips(db, user, params, video_source_id=video_source_id, status=status,
                              render_status=render_status, min_score=min_score, q=q, sort=sort,
                              include_removed=include_removed)


@router.post("/bulk", response_model=BulkOut)
def bulk(body: BulkIn, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return {"results": service.bulk(db, user, body.ids, body.action, acknowledge_warnings=body.acknowledge_warnings)}


@router.get("/{clip_id}", response_model=ClipDetail)
def get_clip(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ClipDetail:
    return service.clip_detail(db, _clip(db, user, clip_id))


@router.patch("/{clip_id}", response_model=ClipDetail)
def update_clip(clip_id: uuid.UUID, body: ClipPatch, user: CurrentUser, db: DbSession) -> ClipDetail:
    """Trim (3-180 s inside the source), retitle, edit metadata or render options."""
    return service.update_clip(db, user, _clip(db, user, clip_id), body)


@router.delete("/{clip_id}", response_model=RemoveOut,
               responses={409: {"description": "clip_uploading: the clip is being uploaded"}})
def remove_clip(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Remove Clip: deletes only this clip's media and keeps a small record."""
    return service.remove_clip(db, user, _clip(db, user, clip_id))


@router.post("/{clip_id}/metadata/regenerate", response_model=JobOut)
def regenerate_metadata(clip_id: uuid.UUID, user: CurrentUser, db: DbSession,
                        body: MetadataRegenerateIn | None = None) -> Any:
    job = service.regenerate_metadata(db, user, _clip(db, user, clip_id), body.use_llm if body else None)
    return job_out(db, job)
