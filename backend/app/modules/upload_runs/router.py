"""/upload-runs — sequential YouTube uploads (one clip at a time, in sequence order)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import UploadRun
from app.modules.upload_runs import service
from app.modules.upload_runs.schemas import UploadRunCreate, UploadRunOut

router = APIRouter(prefix="/upload-runs", tags=["upload-runs"])


def _run(db: DbSession, user: CurrentUser, run_id: uuid.UUID) -> UploadRun:
    return get_owned(db, UploadRun, run_id, user, name="Upload run")


@router.post("", response_model=UploadRunOut, status_code=status.HTTP_201_CREATED,
             responses={409: {"description": "upload_in_progress | nothing_to_upload | clip_not_ready | ..."}})
def create(body: UploadRunCreate, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Start uploading: ``single`` (clip_id), ``next`` (first generated, not uploaded clip) or
    ``remaining`` (all of them, in sequence order)."""
    return service.run_out(db, service.create_run(db, user, body))


@router.get("", response_model=list[UploadRunOut])
def list_runs(user: CurrentUser, db: DbSession, video_source_id: uuid.UUID | None = None,
              active: bool | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    return [service.run_out(db, r) for r in service.list_runs(db, user, video_source_id=video_source_id,
                                                              active=active, limit=limit)]


@router.get("/{run_id}", response_model=UploadRunOut)
def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.run_out(db, _run(db, user, run_id))


@router.post("/{run_id}/retry", response_model=UploadRunOut)
def retry(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Re-upload the failed clip, then continue with the rest in order."""
    return service.run_out(db, service.retry_run(db, _run(db, user, run_id)))


@router.post("/{run_id}/cancel", response_model=UploadRunOut)
def cancel(run_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Stop after the current upload (a transfer is never interrupted midway)."""
    return service.run_out(db, service.cancel_run(db, _run(db, user, run_id)))
