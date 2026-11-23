"""Rendering API (no prefix): /clips/{id}/render, /clips/{id}/download, /clips/{id}/preview-frame."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import GeneratedClip
from app.modules.clips.media import job_out, job_out_model
from app.modules.rendering import service
from app.modules.rendering.schemas import DownloadOut, PreviewFrameIn, RenderRequest, RenderStateOut

router = APIRouter(tags=["rendering"])
JobOut = job_out_model()


def _clip(db: DbSession, user: CurrentUser, clip_id: uuid.UUID) -> GeneratedClip:
    return get_owned(db, GeneratedClip, clip_id, user, name="Clip")


@router.post("/clips/{clip_id}/render", response_model=JobOut,
             responses={409: {"description": "framing_warnings_unacknowledged (details: FramingWarning[])"}})
def render(clip_id: uuid.UUID, user: CurrentUser, db: DbSession, body: RenderRequest | None = None) -> Any:
    """Queue the final 9:16 export (refused while blocking framing warnings are unacknowledged)."""
    clip = _clip(db, user, clip_id)
    job = service.request_render(db, clip, acknowledge_warnings=bool(body and body.acknowledge_warnings))
    return job_out(db, job)


@router.get("/clips/{clip_id}/render", response_model=RenderStateOut)
def render_state(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.render_state(db, _clip(db, user, clip_id))


@router.get("/clips/{clip_id}/download", response_model=DownloadOut)
def download(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, str]:
    return service.download_info(_clip(db, user, clip_id))


@router.post("/clips/{clip_id}/preview-frame", response_class=Response,
             responses={200: {"content": {"image/jpeg": {}}}})
def preview_frame(clip_id: uuid.UUID, body: PreviewFrameIn, user: CurrentUser, db: DbSession) -> Response:
    """One frame rendered by the real export renderer (stored or provided framing; no subtitles)."""
    clip = _clip(db, user, clip_id)
    data = service.preview_frame(db, clip, body.t, framing=body.framing)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
