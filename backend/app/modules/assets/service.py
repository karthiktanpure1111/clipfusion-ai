"""User assets for the renderer: intro/outro videos (<= 20 s) and watermark/background images."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import ValidationFailed
from app.core.security import safe_filename
from app.core.storage import get_storage, temp_workdir, user_asset_key
from app.models import GeneratedClip, User, UserAsset
from app.models.enums import AssetKind

log = logging.getLogger(__name__)

MAX_CLIP_ASSET_SECONDS = 20.0
MAX_IMAGE_BYTES = 20 * 1024 * 1024
VIDEO_KINDS = {AssetKind.INTRO, AssetKind.OUTRO}
IMAGE_FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
_VIDEO_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".mkv": "video/x-matroska",
               ".webm": "video/webm", ".avi": "video/x-msvideo"}
COPY_BLOCK = 1024 * 1024


def _copy_limited(src: BinaryIO, dest: Path, limit: int) -> int:
    written = 0
    with open(dest, "wb") as fh:
        while block := src.read(COPY_BLOCK):
            written += len(block)
            if written > limit:
                raise ValidationFailed(f"The file is too large (limit {limit // (1024 * 1024)} MB).",
                                       code="file_too_large")
            fh.write(block)
    if written == 0:
        raise ValidationFailed("The file is empty.", code="invalid_media")
    return written


def _inspect_image(path: Path) -> tuple[str, str, int, int]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            fmt, (w, h) = im.format, im.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationFailed("The file is not a valid PNG, JPEG or WebP image.", code="invalid_image") from exc
    if fmt not in IMAGE_FORMATS:
        raise ValidationFailed(f"Unsupported image format {fmt}; use PNG, JPEG or WebP.", code="invalid_image")
    ext, mime = IMAGE_FORMATS[fmt]
    return ext, mime, int(w), int(h)


def create_asset(db: Session, user: User, *, kind: str, name: str | None, filename: str,
                 stream: BinaryIO) -> UserAsset:
    """Validate and store an uploaded asset."""
    try:
        akind = AssetKind(kind)
    except ValueError as exc:
        raise ValidationFailed("kind must be one of intro, outro, watermark, background", code="invalid_kind") from exc
    settings = get_settings()
    original = safe_filename(filename or "", default=f"{akind.value}")
    asset_id = uuid.uuid4()
    duration: float | None = None
    with temp_workdir("cf-asset-") as work:
        if akind in VIDEO_KINDS:
            from app.modules.analysis.probe import validate_video_file

            ext = Path(original).suffix.lower()
            if ext not in settings.allowed_video_extensions:
                raise ValidationFailed("Intro/outro must be a video file (" + ", ".join(
                    settings.allowed_video_extensions) + ").", code="unsupported_format")
            tmp = work / f"upload{ext}"
            size = _copy_limited(stream, tmp, settings.max_asset_bytes)
            info = validate_video_file(tmp)
            if info.duration > MAX_CLIP_ASSET_SECONDS:
                raise ValidationFailed(f"Intros and outros can be at most {MAX_CLIP_ASSET_SECONDS:g} seconds "
                                       f"(this one is {info.duration:.1f} s).", code="asset_too_long")
            mime, width, height, duration = _VIDEO_MIME.get(ext, "video/mp4"), info.width, info.height, info.duration
        else:
            tmp = work / "upload.img"
            size = _copy_limited(stream, tmp, MAX_IMAGE_BYTES)
            ext, mime, width, height = _inspect_image(tmp)
        key = user_asset_key(user.id, akind.value, asset_id, ext)
        get_storage().put_file(tmp, key, content_type=mime)
    asset = UserAsset(id=asset_id, user_id=user.id, kind=akind.value,
                      name=(name or Path(original).stem or akind.value).strip()[:200], original_filename=original,
                      storage_key=key, mime_type=mime, file_size=size, duration=duration, width=width,
                      height=height, created_at=utcnow())
    db.add(asset)
    db.commit()
    log.info("Stored %s asset %s for user %s", akind.value, asset.id, user.id)
    return asset


def list_assets(db: Session, user: User, kind: str | None = None) -> list[UserAsset]:
    stmt = select(UserAsset).where(UserAsset.user_id == user.id)
    if kind:
        stmt = stmt.where(UserAsset.kind == kind)
    return list(db.scalars(stmt.order_by(UserAsset.created_at.desc())))


def _clear_references(db: Session, user_id: uuid.UUID, asset_id: uuid.UUID) -> int:
    """Remove the asset from clips' render options so renders don't fail on a missing file."""
    aid, changed = str(asset_id), 0
    for clip in db.scalars(select(GeneratedClip).where(GeneratedClip.user_id == user_id)):
        opts = dict(clip.render_options or {})
        touched = False
        for field in ("intro_asset_id", "outro_asset_id"):
            if str(opts.get(field) or "") == aid:
                opts[field], touched = None, True
        wm = dict(opts.get("watermark") or {})
        if str(wm.get("asset_id") or "") == aid:
            wm["asset_id"], wm["enabled"] = None, bool(wm.get("text"))
            opts["watermark"], touched = wm, True
        if touched:
            clip.render_options = opts
            changed += 1
    return changed


def delete_asset(db: Session, asset: UserAsset) -> None:
    changed = _clear_references(db, asset.user_id, asset.id)
    get_storage().delete(asset.storage_key)
    db.delete(asset)
    db.commit()
    log.info("Deleted asset %s (removed from %d clips)", asset.id, changed)

