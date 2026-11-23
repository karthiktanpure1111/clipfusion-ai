"""/sources — long-form source videos (uploads, YouTube links, cloud imports / direct links)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response, status

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import VideoSource
from app.modules.common.pagination import Page, PageParams, page_params
from app.modules.jobs.schemas import JobOut
from app.modules.jobs.service import job_out
from app.modules.sources import service
from app.modules.sources.pipeline import pipeline as pipeline_status
from app.modules.sources.schemas import (
    AnalyzeIn,
    AttachLinkIn,
    CloudImportIn,
    DeleteOriginalOut,
    ManualClipIn,
    MediaUrlOut,
    PipelineOut,
    SourceOut,
    SourcePatch,
    TranscriptOut,
    YouTubeDownloadIn,
    YouTubeSourceIn,
)

try:  # ClipDetail is owned by the clips module; only used for the OpenAPI response model here.
    from app.modules.clips.schemas import ClipDetail as _ClipDetail
except ModuleNotFoundError:  # pragma: no cover - clips module not installed yet
    _ClipDetail = None

router = APIRouter(prefix="/sources", tags=["sources"])


def _owned(db: DbSession, user: CurrentUser, source_id: uuid.UUID) -> VideoSource:
    return get_owned(db, VideoSource, source_id, user, name="Video")


@router.get("", response_model=Page[SourceOut])
def list_sources(user: CurrentUser, db: DbSession, params: PageParams = Depends(page_params),
                 status: str | None = None, q: str | None = None) -> Page[SourceOut]:
    """The user's videos, newest first (``status`` accepts a comma-separated list)."""
    rows, total = service.list_sources(db, user, params, status=status, q=q)
    return Page[SourceOut](items=service.sources_out(db, rows), total=total, limit=params.limit,
                           offset=params.offset)


@router.post("/youtube", response_model=SourceOut)
def add_youtube(body: YouTubeSourceIn, user: CurrentUser, db: DbSession) -> SourceOut:
    """Add a YouTube link: official metadata only (no media download). Attach the creator-provided
    file by an upload (``video_source_id``) or ``POST /sources/{id}/attach-link``."""
    return service.source_out(db, service.create_youtube_source(db, user, body))


@router.post("/cloud", response_model=SourceOut)
def add_cloud(body: CloudImportIn, user: CurrentUser, db: DbSession) -> SourceOut:
    """Import an authorised file from a direct HTTPS link or Google Drive (background job; processing
    starts automatically afterwards unless ``auto_analyze`` is false)."""
    source, _job = service.create_cloud_import(db, user, body)
    return service.source_out(db, source)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SourceOut:
    return service.source_out(db, _owned(db, user, source_id), detail=True)


@router.patch("/{source_id}", response_model=SourceOut)
def patch_source(source_id: uuid.UUID, body: SourcePatch, user: CurrentUser, db: DbSession) -> SourceOut:
    return service.source_out(db, service.update_source(db, _owned(db, user, source_id), body))


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Delete the video, its clips, jobs and every stored file."""
    service.delete_source(db, _owned(db, user, source_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{source_id}/youtube-qualities")
def youtube_qualities(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict:
    """Qualities (resolution + frame rate, e.g. 2160p60) this YouTube video can be downloaded in."""
    return {"qualities": service.youtube_qualities(db, user, _owned(db, user, source_id))}


@router.post("/{source_id}/download", response_model=SourceOut)
def download_youtube(source_id: uuid.UUID, body: YouTubeDownloadIn, user: CurrentUser, db: DbSession) -> SourceOut:
    """Download a YouTube-link video with yt-dlp (opt-in in Settings; public videos only), then process
    it automatically. Status becomes ``importing``."""
    return service.source_out(db, service.download_from_youtube(db, user, _owned(db, user, source_id), body))


@router.post("/{source_id}/attach-link", response_model=SourceOut)
def attach_link(source_id: uuid.UUID, body: AttachLinkIn, user: CurrentUser, db: DbSession) -> SourceOut:
    """Attach the original from a direct HTTPS file link shared by the creator (YouTube/streaming hosts
    are refused). Status becomes ``importing``; processing starts automatically after the import."""
    return service.source_out(db, service.attach_link(db, user, _owned(db, user, source_id), body))


@router.post("/{source_id}/delete-original", response_model=DeleteOriginalOut)
def delete_original(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Delete only the original long video (clips' records, media and YouTube links stay)."""
    source = _owned(db, user, source_id)
    freed = service.delete_original(db, source)
    return {"id": source.id, "status": "deleted", "freed_bytes": freed, "source": service.source_out(db, source)}


@router.get("/{source_id}/pipeline", response_model=PipelineOut)
def get_pipeline(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Real per-stage processing progress, timings and errors."""
    return pipeline_status(db, _owned(db, user, source_id))


@router.post("/{source_id}/retention/refresh", response_model=SourceOut)
def refresh_retention(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SourceOut:
    """Re-fetch the real YouTube audience-retention curve (channel owner only)."""
    return service.source_out(db, service.refresh_retention(db, user, _owned(db, user, source_id)))


@router.post("/{source_id}/analyze", response_model=JobOut)
def analyze(source_id: uuid.UUID, body: AnalyzeIn, user: CurrentUser, db: DbSession) -> JobOut:
    """Start (or return the running) analysis job (``AnalysisRequest + {rights_confirmed}``)."""
    return job_out(db, service.start_analysis(db, user, _owned(db, user, source_id), body,
                                              rights_confirmed=body.rights_confirmed))


@router.get("/{source_id}/transcript", response_model=TranscriptOut)
def transcript(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    row = service.get_transcript(db, _owned(db, user, source_id))
    return {"language": row.language, "model": row.model, "text": row.text, "word_count": row.word_count,
            "duration": row.duration, "segments": row.segments}


@router.get("/{source_id}/media-url", response_model=MediaUrlOut)
def media_url(source_id: uuid.UUID, user: CurrentUser, db: DbSession) -> MediaUrlOut:
    """Short-lived signed URL of the original file (``browser_playable`` false -> use clip proxies)."""
    url, playable, ttl = service.media_url(_owned(db, user, source_id))
    return MediaUrlOut(url=url, browser_playable=playable, expires_in=ttl)


@router.post("/{source_id}/clips", response_model=_ClipDetail)
def create_clip(source_id: uuid.UUID, body: ManualClipIn, user: CurrentUser, db: DbSession) -> Any:
    """Create a manual clip (metadata generated, clip_vision job dispatched). Returns ``ClipDetail``."""
    from app.modules.clips.service import clip_detail

    clip = service.create_manual_clip(db, user, _owned(db, user, source_id), body)
    return clip_detail(db, clip)
