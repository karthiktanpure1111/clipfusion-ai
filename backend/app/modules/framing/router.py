"""Smart Framing Editor API (no prefix): /clips/{id}/framing..., /framing/apply, /framing-presets."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response, status

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import GeneratedClip
from app.modules.clips.media import job_out, job_out_model
from app.modules.framing import service
from app.modules.framing.schemas import (
    FramingApplyIn,
    FramingApplyOut,
    FramingCheckOut,
    FramingConfigIn,
    FramingOut,
    FramingPresetIn,
    FramingPresetOut,
    FramingPresetPatch,
)

router = APIRouter(tags=["framing"])
JobOut = job_out_model()


def _clip(db: DbSession, user: CurrentUser, clip_id: uuid.UUID) -> GeneratedClip:
    return get_owned(db, GeneratedClip, clip_id, user, name="Clip")


@router.get("/clips/{clip_id}/framing", response_model=FramingOut)
def get_framing(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.framing_out(db, _clip(db, user, clip_id))


@router.put("/clips/{clip_id}/framing", response_model=FramingOut)
def put_framing(clip_id: uuid.UUID, body: FramingConfigIn, user: CurrentUser, db: DbSession,
                apply_to_video: bool = False) -> dict[str, Any]:
    """Save the framing: smart keyframes and warnings are recomputed server-side. ``apply_to_video=true``
    also makes it the framing of the whole video (its other clips and clips created later)."""
    return service.update(db, _clip(db, user, clip_id), body.config, apply_to_video=apply_to_video)


@router.post("/clips/{clip_id}/framing/check", response_model=FramingCheckOut)
def check_framing(clip_id: uuid.UUID, body: FramingConfigIn, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Dry run used live by the editor (nothing is saved)."""
    return service.check(db, _clip(db, user, clip_id), body.config)


@router.post("/clips/{clip_id}/framing/suggest", response_model=FramingCheckOut)
def suggest_framing(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.suggest(db, _clip(db, user, clip_id))


@router.post("/clips/{clip_id}/framing/analyze", response_model=JobOut)
def analyze(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Any:
    """Re-run face detection/tracking (``clip_vision`` job)."""
    return job_out(db, service.request_analysis(db, _clip(db, user, clip_id)))


@router.post("/clips/{clip_id}/framing/acknowledge", response_model=FramingOut)
def acknowledge(clip_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.acknowledge(db, _clip(db, user, clip_id))


@router.post("/framing/apply", response_model=FramingApplyOut)
def apply(body: FramingApplyIn, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Apply a config or a saved preset to several clips (re-fitted to each source)."""
    return {"results": service.apply_to_clips(db, user, body.clip_ids, config=body.config,
                                              preset_id=body.preset_id)}


@router.get("/framing-presets", response_model=list[FramingPresetOut])
def list_presets(user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    return service.list_presets(db, user)


@router.post("/framing-presets", response_model=FramingPresetOut, status_code=status.HTTP_201_CREATED)
def create_preset(body: FramingPresetIn, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.create_preset(db, user, body.name, body.config, body.is_default)


@router.patch("/framing-presets/{preset_id}", response_model=FramingPresetOut)
def update_preset(preset_id: uuid.UUID, body: FramingPresetPatch, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.update_preset(db, user, preset_id, name=body.name, config=body.config,
                                 is_default=body.is_default)


@router.delete("/framing-presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preset(preset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    service.delete_preset(db, user, preset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
