"""/assets — intro/outro videos and watermark/background images used by the renderer."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from pydantic import BaseModel

from app.core.deps import CurrentUser, DbSession, get_owned
from app.core.storage import get_storage
from app.models import UserAsset
from app.modules.assets import service

router = APIRouter(prefix="/assets", tags=["assets"])


class AssetOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    original_filename: str
    mime_type: str
    file_size: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    url: str | None = None
    created_at: datetime


def asset_out(asset: UserAsset) -> AssetOut:
    try:
        url = get_storage().url(asset.storage_key)
    except Exception:  # noqa: BLE001 - listing must not fail on a URL signing problem
        url = None
    return AssetOut(id=asset.id, kind=asset.kind, name=asset.name, original_filename=asset.original_filename,
                    mime_type=asset.mime_type, file_size=asset.file_size, duration=asset.duration,
                    width=asset.width, height=asset.height, url=url, created_at=asset.created_at)


@router.get("", response_model=list[AssetOut])
def list_assets(user: CurrentUser, db: DbSession, kind: str | None = None) -> list[AssetOut]:
    return [asset_out(a) for a in service.list_assets(db, user, kind)]


@router.post("", response_model=AssetOut)
def upload_asset(user: CurrentUser, db: DbSession, file: UploadFile = File(...), kind: str = Form(...),
                 name: str | None = Form(None)) -> AssetOut:
    """Upload an intro/outro (video <= 20 s) or a watermark/background image (PNG/JPEG/WebP <= 20 MB)."""
    try:
        asset = service.create_asset(db, user, kind=kind, name=name, filename=file.filename or "",
                                     stream=file.file)
    finally:
        file.file.close()
    return asset_out(asset)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    service.delete_asset(db, get_owned(db, UserAsset, asset_id, user, name="Asset"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
