"""ClipFusion-managed storage: sizes, disk-space checks, clip media deletion and original deletion.

Sizes come only from ClipFusion's own storage (``users/{uid}/...`` objects) and its temp
directory -- the rest of the disk is never scanned. Media deletion only ever touches one
clip's ``users/{uid}/clips/{clip_id}/`` prefix or one source's ``users/{uid}/sources/{id}/``
prefix."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import AppError, ConflictError
from app.core.storage import get_storage
from app.models import GeneratedClip, ProcessingJob, User, VideoSource
from app.models.enums import CleanupStatus, JobType, NotificationType, UploadStatus

log = logging.getLogger(__name__)

MB = 1024 * 1024
CLIP_JOB_TYPES = (JobType.CLIP_VISION, JobType.RENDER, JobType.TRANSCRIBE_CLIP)
SOURCE_JOB_TYPES = (JobType.IMPORT, JobType.ANALYSIS)


class InsufficientDiskSpace(AppError):
    status_code = 507
    code = "insufficient_disk_space"


# -------------------------------------------------------------- prefixes
def clip_prefix(user_id: uuid.UUID, clip_id: uuid.UUID) -> str:
    return f"users/{user_id}/clips/{clip_id}/"


def source_prefix(user_id: uuid.UUID, source_id: uuid.UUID) -> str:
    return f"users/{user_id}/sources/{source_id}/"


def iter_objects(prefix: str) -> Iterator[tuple[str, int]]:
    """``(key, size)`` of every stored object under ``prefix`` (local walk or S3 listing)."""
    storage = get_storage()
    if getattr(storage, "backend", "") == "local":
        root: Path = storage.root  # type: ignore[attr-defined]
        base = storage._path(prefix.rstrip("/"))  # type: ignore[attr-defined]  # refuses keys escaping the root
        if base.is_file():
            yield prefix.rstrip("/"), base.stat().st_size
            return
        if not base.is_dir():
            return
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                p = Path(dirpath) / name
                try:
                    yield p.relative_to(root).as_posix(), p.stat().st_size
                except OSError:
                    continue
        return
    client = getattr(storage, "client", None)
    bucket = getattr(storage, "bucket", None)
    if client is None or bucket is None:
        return
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield str(obj["Key"]), int(obj.get("Size") or 0)


def prefix_size(prefix: str) -> int:
    return sum(size for _key, size in iter_objects(prefix))


def dir_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


# -------------------------------------------------------------- disk space
def _disk_paths() -> list[Path]:
    s = get_settings()
    paths = [Path(s.temp_dir)]
    if s.storage_backend == "local":
        paths.append(Path(s.local_storage_dir))
    return paths


def disk_free_bytes() -> int:
    """Free bytes on the fullest disk ClipFusion writes to (storage + temp)."""
    frees = []
    for p in _disk_paths():
        try:
            p.mkdir(parents=True, exist_ok=True)
            frees.append(shutil.disk_usage(p).free)
        except OSError:
            continue
    return min(frees) if frees else 0


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def ensure_disk_space(needed_bytes: int, purpose: str) -> None:
    """Raise :class:`InsufficientDiskSpace` (507) unless ``needed_bytes`` fit on every disk ClipFusion
    writes to while keeping ``MIN_FREE_DISK_MB`` free."""
    reserve = max(0, int(get_settings().min_free_disk_mb)) * MB
    free = disk_free_bytes()
    need = max(0, int(needed_bytes)) + reserve
    if free < need:
        raise InsufficientDiskSpace(
            f"Not enough free disk space to {purpose}: {_human(free)} free, about {_human(need)} needed "
            f"(including a {_human(reserve)} safety margin). Free some space -- for example delete originals or "
            "uploaded clips on the Storage page -- and try again.",
            details={"free_bytes": free, "needed_bytes": need, "reserve_bytes": reserve})


def estimated_render_bytes(duration: float) -> int:
    """Generous size estimate of a 1080x1920 render plus its temp files (~3 MB per second)."""
    return int(max(duration, 1.0) * 3 * MB) + 50 * MB


# -------------------------------------------------------------- clip media
def delete_clip_media(db: Session, clip: GeneratedClip) -> int:
    """Delete everything under the clip's own prefix (render MP4, proxy, thumbnail) and its temp dir.
    Clears the media keys and returns the bytes freed. Never commits; raises when deletion failed."""
    storage = get_storage()
    prefix = clip_prefix(clip.user_id, clip.id)
    size = prefix_size(prefix)
    storage.delete_prefix(prefix)
    if any(True for _ in iter_objects(prefix)):
        raise OSError("some media files of the clip could not be deleted")
    shutil.rmtree(Path(get_settings().temp_dir) / "clips" / str(clip.id), ignore_errors=True)
    clip.output_key = clip.proxy_key = clip.thumbnail_key = None
    clip.media_bytes = 0
    db.flush()
    return size


def refresh_media_bytes(db: Session, clip: GeneratedClip) -> int:
    """Re-measure the clip's stored media (no commit)."""
    try:
        clip.media_bytes = prefix_size(clip_prefix(clip.user_id, clip.id))
    except Exception:  # noqa: BLE001 - a size is cosmetic
        log.warning("could not measure the media of clip %s", clip.id)
    return int(clip.media_bytes or 0)


# ---------------------------------------------------------------- originals
def _busy(db: Session, source: VideoSource) -> bool:
    from app.workers.jobs import ACTIVE_STATUSES

    return db.scalar(select(func.count(ProcessingJob.id)).where(
        ProcessingJob.video_source_id == source.id, ProcessingJob.status.in_(ACTIVE_STATUSES),
        ProcessingJob.job_type.in_((*SOURCE_JOB_TYPES, *CLIP_JOB_TYPES)))) > 0


def delete_original(db: Session, source: VideoSource, *, reason: str = "user") -> int:
    """Delete only the original long video of ``source`` (and its thumbnail/proxies under the source
    prefix). Clip rows, their media and YouTube links stay. Returns bytes freed; commits."""
    if source.storage_key is None:
        if source.original_deleted_at is not None:
            return 0  # idempotent
        raise ConflictError("This video has no stored original file.", code="no_file")
    if _busy(db, source):
        raise ConflictError("The video is still being processed; delete the original once processing has "
                            "finished.", code="source_busy")
    prefix = source_prefix(source.user_id, source.id)
    freed = prefix_size(prefix)
    get_storage().delete_prefix(prefix)
    source.storage_key = None
    source.thumbnail_key = None
    source.original_deleted_at = utcnow()
    db.commit()
    log.info("Original of source %s deleted (%s, %d bytes freed)", source.id, reason, freed)
    return freed


def source_all_done(db: Session, source: VideoSource) -> bool:
    """True when the source has clips and every clip is removed, or uploaded with its upload verified
    and its media cleaned up (so nothing needs the original any more)."""
    rows = db.execute(select(GeneratedClip.removed_at, GeneratedClip.upload_status, GeneratedClip.cleanup_status)
                      .where(GeneratedClip.video_source_id == source.id)).all()
    if not rows:
        return False
    return all(removed is not None or (upload == UploadStatus.UPLOADED and cleanup == CleanupStatus.CLEANED)
               for removed, upload, cleanup in rows)


def maybe_auto_delete_original(db: Session, source: VideoSource | None) -> bool:
    """Delete the original when the user enabled ``auto_delete_original`` (default off) and every clip
    of the source is uploaded (verified) or removed. Never raises."""
    from app.modules.notifications.service import notify
    from app.modules.users.service import read_settings

    if source is None or source.storage_key is None:
        return False
    try:
        user = db.get(User, source.user_id)
        if user is None or not read_settings(user).auto_delete_original:
            return False
        if not source_all_done(db, source) or _busy(db, source):
            return False
        freed = delete_original(db, source, reason="auto")
        notify(db, source.user_id, NotificationType.STORAGE, f"Original deleted: {source.title or 'video'}"[:200],
               f"Every clip was uploaded or removed, so the original video was deleted automatically "
               f"({_human(freed)} freed). You can turn this off in Settings.",
               link=f"/app/videos/{source.id}", data={"video_source_id": str(source.id), "freed_bytes": freed})
        db.commit()
        return True
    except Exception:  # noqa: BLE001 - retried by the next cleanup pass
        db.rollback()
        log.exception("Automatic deletion of the original of source %s failed", source.id)
        return False


# ------------------------------------------------------------------ summary
def _classify(key: str) -> tuple[str, str | None, str | None]:
    """``(kind, source_id, clip_id)`` of a stored key below ``users/{uid}/``."""
    parts = key.split("/")
    if len(parts) >= 4 and parts[2] == "sources":
        name = parts[-1].lower()
        if name.startswith("original"):
            return "originals", parts[3], None
        if name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return "thumbnails", parts[3], None
        return "proxies", parts[3], None
    if len(parts) >= 4 and parts[2] == "clips":
        name = parts[-1].lower()
        if name.startswith("proxy"):
            return "proxies", None, parts[3]
        if name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return "thumbnails", None, parts[3]
        return "renders", None, parts[3]
    return "other", None, None


def storage_summary(db: Session, user: User) -> dict[str, Any]:
    """GET /storage: sizes of the user's ClipFusion-managed files, disk free space and per-source
    progress. Also refreshes each clip's ``media_bytes``."""
    from app.modules.clips.service import derive_statuses

    totals = {"originals": 0, "renders": 0, "proxies": 0, "thumbnails": 0, "other": 0}
    by_source: dict[str, int] = {}
    by_clip: dict[str, int] = {}
    for key, size in iter_objects(f"users/{user.id}/"):
        kind, sid, cid = _classify(key)
        totals[kind] += size
        if sid is not None and kind == "originals":
            by_source[sid] = by_source.get(sid, 0) + size
        if cid is not None:
            by_clip[cid] = by_clip.get(cid, 0) + size
    temp_bytes = dir_size(Path(get_settings().temp_dir))

    clips = list(db.scalars(select(GeneratedClip).where(GeneratedClip.user_id == user.id)))
    statuses = derive_statuses(db, clips)
    changed = False
    for c in clips:
        measured = by_clip.get(str(c.id), 0)
        if (c.media_bytes or 0) != measured:
            c.media_bytes, changed = measured, True
    if changed:
        db.commit()
    counts = {"total": len(clips), "generated": 0, "uploaded": 0, "removed": 0, "cleaned": 0}
    per_source: dict[uuid.UUID, dict[str, int]] = {}
    for c in clips:
        st = statuses.get(c.id)
        if st == "generated":
            counts["generated"] += 1
        if c.upload_status == UploadStatus.UPLOADED:
            counts["uploaded"] += 1
        if c.removed_at is not None:
            counts["removed"] += 1
        if c.cleanup_status == CleanupStatus.CLEANED:
            counts["cleaned"] += 1
        agg = per_source.setdefault(c.video_source_id, {"total": 0, "done": 0, "bytes": 0})
        agg["total"] += 1
        agg["bytes"] += by_clip.get(str(c.id), 0)
        if c.removed_at is not None or c.upload_status == UploadStatus.UPLOADED:
            agg["done"] += 1
    sources = list(db.scalars(select(VideoSource).where(VideoSource.user_id == user.id)
                              .order_by(VideoSource.created_at.desc())))
    freed_total = int(db.scalar(select(func.coalesce(func.sum(GeneratedClip.freed_bytes), 0))
                                .where(GeneratedClip.user_id == user.id)) or 0)
    managed = sum(totals.values()) + temp_bytes
    return {
        "managed_bytes": managed, "originals_bytes": totals["originals"], "renders_bytes": totals["renders"],
        "proxies_bytes": totals["proxies"], "thumbnails_bytes": totals["thumbnails"], "temp_bytes": temp_bytes,
        "freed_bytes_total": freed_total, "disk_free_bytes": disk_free_bytes(), "clips": counts,
        "sources": [{
            "id": s.id, "title": s.title, "original_bytes": by_source.get(str(s.id), 0),
            "clip_bytes": per_source.get(s.id, {}).get("bytes", 0),
            "clips_total": per_source.get(s.id, {}).get("total", 0),
            "clips_done": per_source.get(s.id, {}).get("done", 0),
            "all_done": bool(per_source.get(s.id)) and per_source[s.id]["done"] == per_source[s.id]["total"],
            "original_available": bool(s.storage_key),
        } for s in sources],
    }
