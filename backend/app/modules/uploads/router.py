"""/uploads — resumable chunked uploads of original video files."""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession, get_owned
from app.core.errors import ValidationFailed
from app.core.ratelimit import limiter
from app.models import UploadSession
from app.modules.sources.schemas import SourceOut
from app.modules.uploads import service
from app.modules.uploads.schemas import ChunkOut, UploadComplete, UploadInit, UploadOut

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/init", response_model=UploadOut)
def init_upload(body: UploadInit, user: CurrentUser, db: DbSession) -> UploadOut:
    """Start an upload: returns the chunk size and the number of chunks to PUT."""
    return service.upload_out(service.init_upload(db, user, body))


@router.put("/{upload_id}/chunks/{index}", response_model=ChunkOut)
@limiter.limit(get_settings().rate_limit_upload)
async def put_chunk(upload_id: uuid.UUID, index: int, request: Request, user: CurrentUser,
                    db: DbSession) -> ChunkOut:
    """Store chunk ``index`` (raw bytes, exact length; the last chunk may be shorter).
    Re-sending a chunk replaces it (idempotent)."""
    session, expected = await run_in_threadpool(service.open_session_for_chunk, db, user, upload_id, index)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) != expected:
        raise ValidationFailed(f"Chunk {index} must be exactly {expected} bytes (got {declared}).",
                               code="invalid_chunk_length", details={"expected": expected, "received": int(declared)})
    d = service.upload_dir(session.id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{index}.{uuid.uuid4().hex}.tmp"
    received = 0
    try:
        with open(tmp, "wb") as fh:
            async for block in request.stream():
                received += len(block)
                if received > expected:
                    break
                fh.write(block)
        if received != expected:
            raise ValidationFailed(f"Chunk {index} must be exactly {expected} bytes.",
                                   code="invalid_chunk_length",
                                   details={"expected": expected, "received": received})
        os.replace(tmp, d / f"{index}.part")
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return await run_in_threadpool(service.record_chunk, db, session)


@router.get("/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: uuid.UUID, user: CurrentUser, db: DbSession) -> UploadOut:
    """Upload state; clients resume by sending only the chunks missing from ``received_chunks``."""
    return service.upload_out(get_owned(db, UploadSession, upload_id, user, name="Upload"))


@router.post("/{upload_id}/complete", response_model=SourceOut)
def complete_upload(upload_id: uuid.UUID, body: UploadComplete, user: CurrentUser, db: DbSession) -> SourceOut:
    """Assemble + validate the file and create the video (or attach it to a YouTube-link video).
    Returns the existing video with ``duplicate: true`` when the same file was uploaded before."""
    from app.modules.sources.service import source_out

    session = get_owned(db, UploadSession, upload_id, user, name="Upload")
    source, duplicate = service.complete_upload(db, user, session, body)
    out = source_out(db, source, detail=True)
    out.duplicate = duplicate
    return out


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(upload_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Abort an upload and delete its chunks."""
    service.abort_upload(db, get_owned(db, UploadSession, upload_id, user, name="Upload"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
