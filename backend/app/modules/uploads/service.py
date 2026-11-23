"""Resumable chunked uploads and the shared "ingest a local video file" path.

Chunks live in ``TEMP_DIR/uploads/{upload_id}/{index}.part``; the files on disk are the
source of truth for which chunks were received (parallel chunk PUTs cannot lose updates).
:func:`ingest_video_file` (validate -> checksum -> duplicate check -> store -> thumbnail ->
media fields) is shared by upload completion and the ``import_source`` worker task.
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.deps import get_owned
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.security import safe_filename
from app.core.storage import LocalStorage, get_storage, source_key, source_thumb_key
from app.models import UploadSession, User, VideoSource
from app.models.enums import SourceStatus, SourceType
from app.modules.uploads.schemas import ChunkOut, UploadComplete, UploadInit, UploadOut
from app.schemas.media import MediaInfo

log = logging.getLogger(__name__)

UPLOAD_TTL = timedelta(hours=24)
HASH_BLOCK = 4 * 1024 * 1024
CONTAINER_EXT = {"mp4": ".mp4", "mov": ".mov", "m4v": ".m4v", "mkv": ".mkv", "webm": ".webm", "avi": ".avi"}
EXT_MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v", ".mkv": "video/x-matroska",
            ".webm": "video/webm", ".avi": "video/x-msvideo"}
ST_UPLOADING, ST_ASSEMBLING, ST_COMPLETED, ST_ABORTED = "uploading", "assembling", "completed", "aborted"


# ------------------------------------------------------------- file helpers
def upload_dir(upload_id: uuid.UUID) -> Path:
    return get_settings().temp_dir / "uploads" / str(upload_id)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(HASH_BLOCK):
            h.update(block)
    return h.hexdigest()


def storage_extension(filename: str | None, info: MediaInfo) -> str:
    """Server-chosen extension: from the probed container, else an allowed client suffix."""
    ext = CONTAINER_EXT.get((info.container or "").lower())
    if ext:
        return ext
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in get_settings().allowed_video_extensions else ".mp4"


@dataclass
class IngestResult:
    source: VideoSource  # the row holding the file (the pre-existing one when ``duplicate``)
    duplicate: bool
    media: MediaInfo


def find_duplicate(db: Session, user_id: uuid.UUID, checksum: str, exclude_id: uuid.UUID | None) -> VideoSource | None:
    stmt = select(VideoSource).where(VideoSource.user_id == user_id, VideoSource.checksum_sha256 == checksum,
                                     VideoSource.storage_key.is_not(None))
    if exclude_id is not None:
        stmt = stmt.where(VideoSource.id != exclude_id)
    return db.scalars(stmt.order_by(VideoSource.created_at)).first()


def _store_original(path: Path, key: str, mime: str) -> None:
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        storage.move_file(path, key)
    else:
        storage.put_file(path, key, content_type=mime)


def _make_thumbnail(path: Path, info: MediaInfo, source: VideoSource, workdir: Path) -> str | None:
    from app.modules.analysis.probe import extract_frame

    try:
        thumb = extract_frame(path, max(0.0, info.duration * 0.1), workdir / f"thumb-{uuid.uuid4().hex}.jpg")
        key = source_thumb_key(source.user_id, source.id)
        get_storage().put_file(thumb, key, content_type="image/jpeg")
        thumb.unlink(missing_ok=True)
        return key
    except Exception as exc:  # noqa: BLE001 - a thumbnail must never fail an ingest
        log.warning("Thumbnail extraction failed for source %s: %s", source.id, exc)
        return None


def ingest_video_file(db: Session, path: Path, *, source: VideoSource, original_filename: str,
                      checksum: str | None = None) -> IngestResult:
    """Validate ``path`` and move it into storage as ``source``'s original file.

    ``source`` must have ``id`` and ``user_id`` set (it is added to the session when needed).
    When the user already has a stored video with the same SHA-256, nothing is stored and
    the existing row is returned with ``duplicate=True`` (the caller discards ``path``).
    Raises :class:`ValidationFailed` for files that are not valid videos."""
    from app.modules.analysis.probe import validate_video_file

    path = Path(path)
    info = validate_video_file(path)
    checksum = checksum or sha256_file(path)
    dup = find_duplicate(db, source.user_id, checksum, source.id)
    if dup is not None:
        log.info("Upload for user %s duplicates source %s", source.user_id, dup.id)
        return IngestResult(source=dup, duplicate=True, media=info)
    ext = storage_extension(original_filename, info)
    mime = EXT_MIME.get(ext, "video/mp4")
    size = path.stat().st_size
    key = source_key(source.user_id, source.id, ext)
    thumb_key = _make_thumbnail(path, info, source, path.parent)
    _store_original(path, key, mime)
    source.original_filename = safe_filename(original_filename, default=f"video{ext}")
    source.storage_key = key
    source.file_size = size
    source.mime_type = mime
    source.checksum_sha256 = checksum
    source.thumbnail_key = thumb_key or source.thumbnail_key
    source.duration, source.width, source.height = info.duration, info.width, info.height
    source.fps, source.has_audio = info.fps, info.has_audio
    source.media_info = info.model_dump(mode="json")
    source.status = SourceStatus.UPLOADED
    source.error = None
    if source not in db:
        db.add(source)
    db.flush()
    return IngestResult(source=source, duplicate=False, media=info)


# ------------------------------------------------------------------ sessions
def received_on_disk(session: UploadSession) -> list[int]:
    d = upload_dir(session.id)
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("*.part"):
        try:
            idx = int(p.stem)
        except ValueError:
            continue
        if 0 <= idx < session.total_chunks:
            out.append(idx)
    return sorted(out)


def upload_out(session: UploadSession) -> UploadOut:
    received = received_on_disk(session) if session.status == ST_UPLOADING else list(session.received_chunks or [])
    return UploadOut(upload_id=session.id, filename=session.filename, size=session.total_size,
                     chunk_size=session.chunk_size, total_chunks=session.total_chunks, received_chunks=received,
                     status=session.status, expires_at=session.expires_at)


def init_upload(db: Session, user: User, data: UploadInit) -> UploadSession:
    """Start a resumable upload after checking the extension allow-list and size limit."""
    s = get_settings()
    name = safe_filename(data.filename, default="video")
    ext = Path(name).suffix.lower()
    if ext not in s.allowed_video_extensions:
        raise ValidationFailed(f"Unsupported file type '{ext or '(none)'}'. Allowed: "
                               + ", ".join(s.allowed_video_extensions), code="unsupported_format")
    if data.size > s.max_upload_bytes:
        raise ValidationFailed(f"The file is too large ({data.size} bytes; limit {s.max_upload_bytes}).",
                               code="file_too_large")
    from app.modules.storage.service import ensure_disk_space

    ensure_disk_space(data.size * 2, "upload this video")  # chunks + the assembled file
    chunk = s.upload_chunk_bytes
    session = UploadSession(user_id=user.id, filename=name, mime_type=data.mime_type, total_size=data.size,
                            chunk_size=chunk, total_chunks=max(1, math.ceil(data.size / chunk)), received_chunks=[],
                            status=ST_UPLOADING, expires_at=utcnow() + UPLOAD_TTL)
    db.add(session)
    db.commit()
    upload_dir(session.id).mkdir(parents=True, exist_ok=True)
    return session


def expected_chunk_length(session: UploadSession, index: int) -> int:
    if index < 0 or index >= session.total_chunks:
        raise ValidationFailed(f"Chunk index must be between 0 and {session.total_chunks - 1}",
                               code="invalid_chunk_index")
    if index == session.total_chunks - 1:
        return session.total_size - session.chunk_size * (session.total_chunks - 1)
    return session.chunk_size


def open_session_for_chunk(db: Session, user: User, upload_id: uuid.UUID, index: int) -> tuple[UploadSession, int]:
    """Check the session accepts chunk ``index``; returns ``(session, expected_length)``."""
    session = get_owned(db, UploadSession, upload_id, user, name="Upload")
    _require_uploading(session)
    return session, expected_chunk_length(session, index)


def _require_uploading(session: UploadSession) -> None:
    if session.status != ST_UPLOADING:
        raise ConflictError(f"Upload is {session.status}", code="upload_not_active")
    if session.expires_at <= utcnow():
        raise ConflictError("Upload session expired; start a new upload", code="upload_expired")


def record_chunk(db: Session, session: UploadSession) -> ChunkOut:
    """Refresh the cached chunk list from disk and extend the session expiry."""
    db.refresh(session)
    received = received_on_disk(session)
    session.received_chunks = received
    session.expires_at = utcnow() + UPLOAD_TTL
    db.commit()
    return ChunkOut(received_chunks=received, complete=len(received) == session.total_chunks)


def abort_upload(db: Session, session: UploadSession) -> None:
    if session.status == ST_ASSEMBLING:
        raise ConflictError("Upload is being completed", code="upload_busy")
    if session.status == ST_UPLOADING:
        session.status = ST_ABORTED
        db.commit()
    shutil.rmtree(upload_dir(session.id), ignore_errors=True)


def _assemble(session: UploadSession) -> tuple[Path, str]:
    d = upload_dir(session.id)
    out = d / f"assembled{Path(session.filename).suffix.lower()}"
    h = hashlib.sha256()
    with open(out, "wb") as dst:
        for i in range(session.total_chunks):
            with open(d / f"{i}.part", "rb") as src:
                while block := src.read(HASH_BLOCK):
                    h.update(block)
                    dst.write(block)
    if out.stat().st_size != session.total_size:
        out.unlink(missing_ok=True)
        raise ValidationFailed("Assembled file size does not match the declared size", code="size_mismatch")
    return out, h.hexdigest()


def _target_source(db: Session, user: User, source_id: uuid.UUID | None) -> VideoSource | None:
    if source_id is None:
        return None
    target = get_owned(db, VideoSource, source_id, user, name="Video")
    if target.storage_key:
        raise ConflictError("This video already has an original file attached.", code="file_already_attached")
    if target.source_type != SourceType.YOUTUBE and target.original_deleted_at is None:
        raise ConflictError("Files can only be attached to YouTube-link videos or videos whose original was "
                            "deleted.", code="invalid_target")
    return target


def complete_upload(db: Session, user: User, session: UploadSession,
                    data: UploadComplete) -> tuple[VideoSource, bool]:
    """Assemble, validate and store the upload. Returns ``(source, duplicate)``."""
    if session.status == ST_COMPLETED and session.video_source_id:
        source = db.get(VideoSource, session.video_source_id)
        if source is None:
            raise NotFoundError("Video not found")
        return source, False
    from app.modules.storage.service import ensure_disk_space

    _require_uploading(session)
    if not data.rights_confirmed:
        raise ValidationFailed("Confirm that you have the creator's permission, a licence, or other rights to "
                               "reuse and edit this video.", code="rights_not_confirmed")
    target = _target_source(db, user, data.video_source_id)
    ensure_disk_space(session.total_size, "store this video")
    missing = sorted(set(range(session.total_chunks)) - set(received_on_disk(session)))
    if missing:
        raise ConflictError("Upload incomplete", code="upload_incomplete", details={"missing_chunks": missing[:100]})
    claimed = db.execute(update(UploadSession).where(UploadSession.id == session.id,
                                                     UploadSession.status == ST_UPLOADING)
                         .values(status=ST_ASSEMBLING)).rowcount
    db.commit()
    if not claimed:
        raise ConflictError("Upload is already being completed", code="upload_busy")
    db.refresh(session)
    try:
        path, checksum = _assemble(session)
        source = target or VideoSource(id=uuid.uuid4(), user_id=user.id, source_type=SourceType.UPLOAD,
                                       status=SourceStatus.PENDING_UPLOAD, title="")
        result = ingest_video_file(db, path, source=source, original_filename=session.filename, checksum=checksum)
    except ValidationFailed:
        db.rollback()
        session.status = ST_ABORTED
        db.commit()
        shutil.rmtree(upload_dir(session.id), ignore_errors=True)
        raise
    except Exception:
        db.rollback()
        session.status = ST_UPLOADING
        db.commit()
        (upload_dir(session.id) / f"assembled{Path(session.filename).suffix.lower()}").unlink(missing_ok=True)
        raise
    source = result.source
    if not result.duplicate:
        if data.title and data.title.strip():
            source.title = data.title.strip()[:500]
        elif not source.title:
            source.title = Path(session.filename).stem[:500] or "Untitled video"
        source.rights_confirmed = True
        source.rights_confirmed_at = utcnow()
        source.rights_statement = data.rights_statement or source.rights_statement
        if data.attribution is not None:
            source.attribution = data.attribution.strip()[:500] or None
        source.original_deleted_at = None
    session.status = ST_COMPLETED
    session.video_source_id = source.id
    session.received_chunks = list(range(session.total_chunks))
    db.commit()
    shutil.rmtree(upload_dir(session.id), ignore_errors=True)
    log.info("Upload %s completed -> source %s (duplicate=%s)", session.id, source.id, result.duplicate)
    if not result.duplicate and data.auto_analyze:
        from app.modules.sources.service import auto_start_analysis

        auto_start_analysis(db, user, source, data.analysis)
    return source, result.duplicate
