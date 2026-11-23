"""Video sources: listing/serialisation, YouTube links (official metadata only, + real retention for
owners), cloud import / attach-link, automatic analysis start, transcript, media URL, manual clips
and original deletion.

API v2: YouTube links never download media (YouTube's Terms do not permit it). The creator-provided
file is attached by an upload (``video_source_id``) or a direct HTTPS link (``attach-link``), and
processing then starts automatically. No channel ownership is required -- only the user's
confirmation that they have the creator's permission, a licence or other rights."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import (
    AppError,
    ConflictError,
    ExternalServiceError,
    IntegrationNotConfigured,
    NotFoundError,
    PermissionDenied,
    QuotaExceeded,
    ValidationFailed,
)
from app.core.storage import get_storage
from app.models import (
    ConnectedAccount,
    GeneratedClip,
    ProcessingJob,
    Transcript,
    User,
    VideoAnalysis,
    VideoSource,
)
from app.models.enums import JobStatus, JobType, SourceStatus, SourceType
from app.modules.common.pagination import PageParams, paginate
from app.modules.jobs.service import jobs_out
from app.modules.sources.schemas import (
    AnalysisRequest,
    AttachLinkIn,
    CloudImportIn,
    ManualClipIn,
    SourceOut,
    SourcePatch,
    YouTubeDownloadIn,
    YouTubeSourceIn,
)
from app.modules.users.service import read_settings
from app.schemas.media import AnalysisOptions, AudioAnalysis, HighlightCandidate, RetentionPoint
from app.workers.jobs import ACTIVE_STATUSES, create_job, dispatch_job

log = logging.getLogger(__name__)
SOURCE_JOB_TYPES = (JobType.IMPORT, JobType.ANALYSIS)
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
MAX_MANUAL_CLIP = 180.0
MIN_MANUAL_CLIP = 1.0
RIGHTS_MESSAGE = ("Confirm that you have the creator's permission, a licence, or other rights to reuse and edit "
                  "this video.")
YT_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


# ------------------------------------------------------------- serialisation
def _signed(key: str | None) -> str | None:
    if not key:
        return None
    try:
        return get_storage().url(key)
    except Exception as exc:  # noqa: BLE001 - a URL failure must not break listings
        log.warning("Could not sign storage URL: %s", exc)
        return None


def _retention_points(source: VideoSource) -> list[dict]:
    return list((source.retention_data or {}).get("points") or [])


def _warnings(source: VideoSource, analysis_warnings: list[str]) -> list[str]:
    out = list(analysis_warnings)
    err = (source.retention_data or {}).get("error")
    if err:
        out.append(f"YouTube audience retention unavailable: {err}")
    return out


def sources_out(db: Session, sources: Sequence[VideoSource], *, detail: bool = False) -> list[SourceOut]:
    """Serialise sources with batched clip counts, active jobs and analysis warnings."""
    ids = [s.id for s in sources]
    if not ids:
        return []
    counts = dict(db.execute(select(GeneratedClip.video_source_id, func.count(GeneratedClip.id))
                             .where(GeneratedClip.video_source_id.in_(ids), GeneratedClip.removed_at.is_(None))
                             .group_by(GeneratedClip.video_source_id)).tuples().all())
    jobs = list(db.scalars(select(ProcessingJob).where(
        ProcessingJob.video_source_id.in_(ids), ProcessingJob.job_type.in_(SOURCE_JOB_TYPES),
        ProcessingJob.status.in_(ACTIVE_STATUSES)).order_by(ProcessingJob.created_at.desc())))
    active: dict[uuid.UUID, object] = {}
    for job, out in zip(jobs, jobs_out(db, jobs), strict=True):
        active.setdefault(job.video_source_id, out)
    debug = dict(db.execute(select(VideoAnalysis.video_source_id, VideoAnalysis.highlight_debug)
                            .where(VideoAnalysis.video_source_id.in_(ids))).tuples().all())
    languages = dict(db.execute(select(Transcript.video_source_id, Transcript.language)
                                .where(Transcript.video_source_id.in_(ids))).tuples().all())
    result = []
    for s in sources:
        points = _retention_points(s)
        thumb = _signed(s.thumbnail_key) or (s.youtube_metadata or {}).get("thumbnail_url")
        result.append(SourceOut(
            id=s.id, source_type=s.source_type, status=s.status, title=s.title, description=s.description,
            original_filename=s.original_filename, file_size=s.file_size, mime_type=s.mime_type, duration=s.duration,
            width=s.width, height=s.height, fps=s.fps, has_audio=s.has_audio, has_file=bool(s.storage_key),
            original_available=bool(s.storage_key), original_bytes=int(s.file_size or 0) if s.storage_key else 0,
            original_deleted_at=s.original_deleted_at, processing_mode=s.processing_mode,
            attribution=s.attribution, transcript_language=languages.get(s.id),
            thumbnail_url=thumb, youtube_video_id=s.youtube_video_id, youtube_url=s.youtube_url,
            youtube_metadata=s.youtube_metadata or {}, has_retention=bool(points),
            retention_points=[RetentionPoint.model_validate(p) for p in points] if detail and points else None,
            cloud_provider=s.cloud_provider, rights_confirmed=s.rights_confirmed,
            rights_confirmed_at=s.rights_confirmed_at, analysis_options=s.analysis_options or {},
            warnings=_warnings(s, list((debug.get(s.id) or {}).get("warnings") or [])), error=s.error,
            processed_at=s.processed_at, clip_count=int(counts.get(s.id, 0)), active_job=active.get(s.id),
            created_at=s.created_at, updated_at=s.updated_at))
    return result


def source_out(db: Session, source: VideoSource, *, detail: bool = True) -> SourceOut:
    """Serialise one source (detail includes the retention curve)."""
    return sources_out(db, [source], detail=detail)[0]


# -------------------------------------------------------------------- CRUD
def list_sources(db: Session, user: User, params: PageParams, *, status: str | None = None,
                 q: str | None = None) -> tuple[list[VideoSource], int]:
    stmt = select(VideoSource).where(VideoSource.user_id == user.id)
    if status:
        stmt = stmt.where(VideoSource.status.in_([x.strip() for x in status.split(",") if x.strip()]))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(VideoSource.title).like(like),
                              func.lower(func.coalesce(VideoSource.original_filename, "")).like(like)))
    return paginate(db, stmt.order_by(VideoSource.created_at.desc(), VideoSource.id), params)


def update_source(db: Session, source: VideoSource, data: SourcePatch) -> VideoSource:
    if data.title is not None:
        source.title = data.title.strip()
    if "description" in data.model_fields_set:
        source.description = data.description
    if "attribution" in data.model_fields_set:
        source.attribution = (data.attribution or "").strip()[:500] or None
    db.commit()
    return source


def delete_source(db: Session, source: VideoSource) -> None:
    """Cancel the source's active jobs, delete its stored files (original, thumbnails,
    clip proxies/renders) and the rows (clips, jobs, transcript, upload runs cascade)."""
    clip_ids = list(db.scalars(select(GeneratedClip.id).where(GeneratedClip.video_source_id == source.id)))
    for job in db.scalars(select(ProcessingJob).where(ProcessingJob.video_source_id == source.id,
                                                      ProcessingJob.status.in_(ACTIVE_STATUSES))):
        job.status, job.finished_at, job.idempotency_key = JobStatus.CANCELLED, utcnow(), None
    db.flush()
    storage = get_storage()
    storage.delete_prefix(f"users/{source.user_id}/sources/{source.id}/")
    for cid in clip_ids:
        storage.delete_prefix(f"users/{source.user_id}/clips/{cid}/")
    db.delete(source)
    db.commit()
    log.info("Deleted source %s (%d clips)", source.id, len(clip_ids))


def delete_original(db: Session, source: VideoSource) -> int:
    """POST /sources/{id}/delete-original: delete only the original long video. Returns bytes freed."""
    from app.modules.storage.service import delete_original as _delete

    return _delete(db, source, reason="user")


def _require_rights(confirmed: bool) -> None:
    if not confirmed:
        raise ValidationFailed(RIGHTS_MESSAGE, code="rights_not_confirmed")


def _confirm_rights(source: VideoSource, statement: str | None) -> None:
    source.rights_confirmed, source.rights_confirmed_at = True, utcnow()
    if statement:
        source.rights_statement = statement


# ------------------------------------------------------------------ YouTube
def _record_retention_error(source: VideoSource, message: str, account_id: str | None) -> None:
    source.retention_data = {**(source.retention_data or {}), "error": message[:500],
                             "error_at": utcnow().isoformat(), "connected_account_id": account_id}


def _fetch_retention(db: Session, source: VideoSource, account: ConnectedAccount) -> None:
    from app.integrations.youtube import fetch_retention_curve

    points = fetch_retention_curve(db, account, source.youtube_video_id or "")
    source.retention_data = {"points": [p.model_dump(mode="json") for p in points],
                             "fetched_at": utcnow().isoformat(), "connected_account_id": str(account.id)}


def _owner_account(db: Session, user: User, meta: dict) -> ConnectedAccount | None:
    acc_id = meta.get("owner_account_id")
    if not meta.get("is_owner") or not acc_id:
        return None
    acc = db.get(ConnectedAccount, uuid.UUID(str(acc_id)))
    return acc if acc is not None and acc.user_id == user.id else None


def default_attribution(meta: dict, url: str) -> str:
    channel = (meta.get("channel_title") or "").strip()
    return f"Credit: {channel} — {url}" if channel else f"Credit: {url}"


def _youtube_metadata(db: Session, user: User, video_id: str) -> dict:
    """Official ``videos.list`` metadata with meaningful errors (no ownership needed)."""
    from app.integrations.http import ProviderError
    from app.integrations.youtube import fetch_video_metadata

    try:
        return fetch_video_metadata(db, user, video_id)
    except NotFoundError as exc:
        raise NotFoundError("This YouTube video is unavailable: it does not exist, is private, or was removed.",
                            code="youtube_video_unavailable") from exc
    except ProviderError as exc:
        if str(exc.reason or "") in YT_QUOTA_REASONS:
            raise QuotaExceeded("The YouTube Data API quota for today is used up. Try again after the daily reset "
                                "(midnight Pacific time).", code="quota_exceeded") from exc
        if exc.status == 401:
            raise PermissionDenied("The YouTube authorisation expired. Reconnect your YouTube account under "
                                   "Connected Accounts and try again.", code="youtube_auth_expired") from exc
        if exc.status == 403:
            raise PermissionDenied(f"YouTube refused the metadata request ({exc.provider_message}). Check that the "
                                   "YouTube Data API v3 is enabled for your API key or Google project.",
                                   code="youtube_permission_missing") from exc
        raise ExternalServiceError(f"YouTube could not be reached: {exc.provider_message}",
                                   code="youtube_unavailable") from exc


def _youtube_metadata_or_ytdlp(db: Session, user: User, video_id: str) -> dict:
    """Official metadata; when the Data API is not configured / its login expired and the user turned
    YouTube downloads on, the public page metadata from yt-dlp is used instead."""
    try:
        return _youtube_metadata(db, user, video_id)
    except (IntegrationNotConfigured, PermissionDenied, ExternalServiceError) as exc:
        if not read_settings(user).youtube_download_enabled:
            raise
        from app.integrations import ytdlp

        log.info("YouTube Data API unavailable (%s); using yt-dlp metadata for %s", exc.message, video_id)
        return ytdlp.fetch_info(video_id)


def create_youtube_source(db: Session, user: User, data: YouTubeSourceIn | str) -> VideoSource:
    """Metadata-only source from a YouTube link (official Data API: title, channel, duration,
    thumbnail). Media is never downloaded from YouTube; the creator-provided file is attached later
    (upload or direct link) and processing then starts automatically."""
    from app.integrations.youtube import parse_video_id

    if isinstance(data, str):  # legacy callers
        data = YouTubeSourceIn(url=data, rights_confirmed=True)
    video_id = parse_video_id(data.url)
    if not video_id:
        raise ValidationFailed("That does not look like a YouTube video link (expected youtube.com/watch?v=..., "
                               "youtu.be/... or youtube.com/shorts/...).", code="invalid_youtube_url")
    _require_rights(data.rights_confirmed)
    url = f"https://www.youtube.com/watch?v={video_id}"
    existing = db.scalar(select(VideoSource).where(VideoSource.user_id == user.id,
                                                   VideoSource.youtube_video_id == video_id))
    if existing is not None:
        _confirm_rights(existing, data.rights_statement)
        if data.attribution is not None:
            existing.attribution = data.attribution.strip()[:500] or None
        elif not existing.attribution:
            existing.attribution = default_attribution(existing.youtube_metadata or {}, url)
        db.commit()
        return existing
    meta = _youtube_metadata_or_ytdlp(db, user, video_id)
    attribution = (data.attribution or "").strip()[:500] or default_attribution(meta, url)
    source = VideoSource(user_id=user.id, source_type=SourceType.YOUTUBE, status=SourceStatus.METADATA_ONLY,
                         title=(meta.get("title") or f"YouTube video {video_id}")[:500],
                         description=meta.get("description") or None, youtube_video_id=video_id,
                         youtube_url=url, youtube_metadata=meta, attribution=attribution,
                         duration=meta.get("duration_seconds") or None)
    _confirm_rights(source, data.rights_statement)
    db.add(source)
    db.flush()
    account = _owner_account(db, user, meta)
    if account is not None:  # optional extra: real audience retention for the channel owner
        try:
            _fetch_retention(db, source, account)
        except AppError as exc:
            log.info("Retention fetch failed for source %s: %s", source.id, exc.message)
            _record_retention_error(source, exc.message, str(account.id))
    db.commit()
    return source


def refresh_retention(db: Session, user: User, source: VideoSource) -> VideoSource:
    """Re-fetch the real audience-retention curve (owner only). Failures are recorded and raised."""
    if source.source_type != SourceType.YOUTUBE or not source.youtube_video_id:
        raise ValidationFailed("Audience retention is only available for YouTube-link videos.",
                               code="not_youtube")
    account = _owner_account(db, user, source.youtube_metadata or {})
    if account is None:
        meta = _youtube_metadata(db, user, source.youtube_video_id)
        source.youtube_metadata = meta
        account = _owner_account(db, user, meta)
    if account is None:
        db.commit()
        raise PermissionDenied("Audience retention is only available for videos on a YouTube channel you have "
                               "connected (YouTube Analytics restricts it to the channel owner).", code="not_owner")
    try:
        _fetch_retention(db, source, account)
    except AppError as exc:
        _record_retention_error(source, exc.message, str(account.id))
        db.commit()
        raise
    db.commit()
    return source


# ------------------------------------------------------------- cloud import
def _drive_account(db: Session, user: User) -> ConnectedAccount:
    from app.integrations import google_oauth
    from app.modules.accounts.tokens import find_google_account

    account = find_google_account(db, user, need_scope=DRIVE_SCOPE)
    if account is not None:
        return account
    missing = google_oauth.missing_env()
    hint = (" The server also needs " + ", ".join(missing) + ".") if missing else ""
    raise IntegrationNotConfigured(
        "Google Drive import needs a connected Google account with Drive read access. Go to Connected "
        "Accounts > Connect YouTube and tick 'Also allow Google Drive import' (requests the drive.readonly "
        "permission), then try again." + hint,
        details={"missing_env": missing, "required_scope": DRIVE_SCOPE})


def _import_params(provider: str, auto_analyze: bool, analysis: AnalysisRequest | None) -> dict:
    return {"provider": provider, "auto_analyze": bool(auto_analyze),
            "analysis": analysis.model_dump(mode="json", exclude_none=True) if analysis is not None else None}


def _ensure_import_space() -> None:
    from app.modules.storage.service import ensure_disk_space

    ensure_disk_space(0, "import a video")


def create_cloud_import(db: Session, user: User, data: CloudImportIn) -> tuple[VideoSource, ProcessingJob]:
    """Create a source in ``importing`` state and dispatch an ``import_source`` job (analysis starts
    automatically after the import when ``auto_analyze``)."""
    _require_rights(data.rights_confirmed)
    _ensure_import_space()
    params = _import_params(data.provider, data.auto_analyze, data.analysis)
    if data.provider == "url":
        from app.integrations.cloud_import import validate_import_url

        url = (data.url or "").strip()
        validate_import_url(url)
        params["url"], reference = url, url
    else:
        from app.integrations.google_drive import parse_drive_file_id

        file_id = parse_drive_file_id(data.file_id or data.url or "")
        if not file_id:
            raise ValidationFailed("Invalid Google Drive file id or link.", code="invalid_drive_file")
        account = _drive_account(db, user)
        params.update(file_id=file_id, account_id=str(account.id))
        reference = file_id
    source = VideoSource(user_id=user.id, source_type=SourceType.CLOUD, status=SourceStatus.IMPORTING,
                         title=(data.title or "").strip()[:500], cloud_provider=data.provider,
                         cloud_reference=reference[:2000], attribution=(data.attribution or "").strip()[:500] or None)
    _confirm_rights(source, data.rights_statement)
    db.add(source)
    db.flush()
    job, _ = create_job(db, user_id=user.id, job_type=JobType.IMPORT, video_source_id=source.id, params=params,
                        idempotency_key=f"import:{source.id}", stage="queued")
    dispatch_job(db, job, "import_source")
    return source, job


def youtube_qualities(db: Session, user: User, source: VideoSource) -> list[dict]:
    """Available download qualities, best first (cached on the source for an hour)."""
    from datetime import datetime

    from app.integrations import ytdlp

    if not read_settings(user).youtube_download_enabled:
        raise PermissionDenied("Downloading from YouTube links is switched off in Settings.",
                               code="youtube_download_disabled")
    if source.source_type != SourceType.YOUTUBE or not source.youtube_video_id:
        raise ValidationFailed("Only videos added from a YouTube link have download qualities.", code="not_youtube")
    meta = dict(source.youtube_metadata or {})
    cached, fetched = meta.get("qualities"), meta.get("qualities_fetched_at")
    if cached and fetched and (utcnow() - datetime.fromisoformat(fetched)).total_seconds() < 3600:
        return cached
    qualities = ytdlp.list_qualities(source.youtube_video_id)
    meta["qualities"], meta["qualities_fetched_at"] = qualities, utcnow().isoformat()
    source.youtube_metadata = meta
    db.commit()
    return qualities


def download_from_youtube(db: Session, user: User, source: VideoSource, data: YouTubeDownloadIn) -> VideoSource:
    """Download a YouTube-link video with yt-dlp (only when the user switched it on in Settings),
    then process it automatically. Public videos only."""
    prefs = read_settings(user)
    if not prefs.youtube_download_enabled:
        raise PermissionDenied("Downloading from YouTube links is switched off. Turn on “Download from YouTube "
                               "links” in Settings first.", code="youtube_download_disabled")
    if source.source_type != SourceType.YOUTUBE or not source.youtube_video_id:
        raise ValidationFailed("Only videos added from a YouTube link can be downloaded.", code="not_youtube")
    _require_rights(data.rights_confirmed)
    if source.storage_key:
        raise ConflictError("This video already has its file.", code="file_already_attached")
    active = db.scalar(select(ProcessingJob.id).where(ProcessingJob.video_source_id == source.id,
                                                      ProcessingJob.job_type == JobType.IMPORT,
                                                      ProcessingJob.status.in_(ACTIVE_STATUSES)).limit(1))
    if active is not None:
        raise ConflictError("This video is already being downloaded.", code="importing")
    _ensure_import_space()
    params = _import_params("youtube", data.auto_analyze, data.analysis)
    params.update(video_id=source.youtube_video_id, quality=data.quality or prefs.youtube_download_quality)
    source.status, source.error = SourceStatus.IMPORTING, None
    source.cloud_provider, source.cloud_reference = "youtube", (source.youtube_url or "")[:2000]
    _confirm_rights(source, data.rights_statement)
    job, _ = create_job(db, user_id=user.id, job_type=JobType.IMPORT, video_source_id=source.id, params=params,
                        idempotency_key=f"import:{source.id}", stage="queued")
    dispatch_job(db, job, "import_source")
    return source


def attach_link(db: Session, user: User, source: VideoSource, data: AttachLinkIn) -> VideoSource:
    """Attach the creator-provided original through a direct HTTPS file link (Dropbox, Google Drive
    public file, OneDrive, S3 ...). YouTube and other streaming hosts are refused."""
    from app.integrations.cloud_import import validate_import_url

    _require_rights(data.rights_confirmed)
    if source.storage_key:
        raise ConflictError("This video already has an original file attached.", code="file_already_attached")
    active = db.scalar(select(ProcessingJob.id).where(ProcessingJob.video_source_id == source.id,
                                                      ProcessingJob.job_type == JobType.IMPORT,
                                                      ProcessingJob.status.in_(ACTIVE_STATUSES)).limit(1))
    if active is not None:
        raise ConflictError("A file is already being imported for this video.", code="importing")
    url = data.url.strip()
    validate_import_url(url)  # https only, public hosts, refuses YouTube/streaming platforms
    _ensure_import_space()
    params = _import_params("url", data.auto_analyze, data.analysis)
    params["url"] = url
    source.status, source.error = SourceStatus.IMPORTING, None
    source.cloud_provider, source.cloud_reference = "url", url[:2000]
    _confirm_rights(source, data.rights_statement)
    job, _ = create_job(db, user_id=user.id, job_type=JobType.IMPORT, video_source_id=source.id, params=params,
                        idempotency_key=f"import:{source.id}", stage="queued")
    dispatch_job(db, job, "import_source")
    return source


# ---------------------------------------------------------------- analysis
def _derived_targets(lo: float, hi: float) -> list[float]:
    """Target durations inside ``[lo, hi]`` (a quarter and three quarters of the way)."""
    if hi - lo < 4:
        return [round((lo + hi) / 2, 1)]
    return sorted({round(lo + (hi - lo) * 0.25), round(lo + (hi - lo) * 0.75)})


def build_options(user: User, data: AnalysisRequest | None) -> AnalysisOptions:
    """AnalysisOptions from a request, falling back to the user's settings for omitted values."""
    data = data or AnalysisRequest()
    prefs = read_settings(user)
    lo = float(data.min_duration if data.min_duration is not None else prefs.default_min_duration)
    hi = float(data.max_duration if data.max_duration is not None else prefs.default_max_duration)
    if hi < lo and data.max_duration is None:
        hi = lo  # an explicit minimum above the default maximum raises the maximum
    elif hi < lo and data.min_duration is None:
        lo = hi
    if hi < lo:
        raise ValidationFailed("max_duration must be at least min_duration.", code="invalid_duration_range")
    if data.clip_durations:  # explicit targets widen the limits that were not given explicitly
        if data.min_duration is None:
            lo = min(lo, min(data.clip_durations))
        if data.max_duration is None:
            hi = max(hi, max(data.clip_durations))
    targets = [d for d in (data.clip_durations or []) if lo <= d <= hi] or _derived_targets(lo, hi)
    values = {"mode": data.mode or prefs.default_processing_mode, "min_duration": lo, "max_duration": hi,
              "clip_durations": targets, "max_clips": data.max_clips or prefs.default_max_clips,
              "min_score": data.min_score or 0.0, "language": data.language, "whisper_model": data.whisper_model,
              "use_llm": prefs.default_use_llm if data.use_llm is None else data.use_llm}
    fields = AnalysisOptions.model_fields
    return AnalysisOptions(**{k: v for k, v in values.items() if k in fields})


def start_analysis(db: Session, user: User, source: VideoSource, data: AnalysisRequest | None, *,
                   rights_confirmed: bool | None = None) -> ProcessingJob:
    """Queue the full analysis (idempotent while one is active). Commits."""
    from app.modules.storage.service import ensure_disk_space

    confirmed = rights_confirmed if rights_confirmed is not None else bool(getattr(data, "rights_confirmed", False))
    if not confirmed:
        raise ValidationFailed(RIGHTS_MESSAGE, code="rights_not_confirmed")
    if not source.storage_key:
        if source.original_deleted_at is not None:
            raise ConflictError("The original video was deleted to free space. Attach it again (upload or direct "
                                "link) to process it again.", code="original_deleted")
        raise ConflictError("Attach the original video file before processing (upload it or add a direct link).",
                            code="no_file")
    if source.status == SourceStatus.IMPORTING:
        raise ConflictError("The video is still being imported.", code="importing")
    ensure_disk_space(int((source.duration or 3600) * 64_000), "analyse this video")
    options = build_options(user, data)
    stored = options.model_dump(mode="json", exclude={"retention"})
    job, created = create_job(db, user_id=user.id, job_type=JobType.ANALYSIS, video_source_id=source.id,
                              params={"options": stored}, idempotency_key=f"analysis:{source.id}", stage="queued")
    if not created:
        return job
    source.rights_confirmed, source.rights_confirmed_at = True, source.rights_confirmed_at or utcnow()
    source.analysis_options = stored
    source.processing_mode = str(stored.get("mode") or "balanced")
    source.status, source.error = SourceStatus.QUEUED, None
    dispatch_job(db, job, "analyze_source")
    return job


def auto_start_analysis(db: Session, user: User | None, source: VideoSource,
                        analysis: AnalysisRequest | dict | None) -> ProcessingJob | None:
    """Start processing automatically after a file was attached. Problems are recorded on the source
    (``error``) instead of raised. Commits."""
    if user is None:
        return None
    try:
        request = AnalysisRequest.model_validate(analysis) if isinstance(analysis, dict) else analysis
        return start_analysis(db, user, source, request, rights_confirmed=True)
    except AppError as exc:
        db.rollback()
        fresh = db.get(VideoSource, source.id)
        if fresh is not None:
            fresh.error = f"Processing could not start: {exc.message}"[:2000]
            db.commit()
        log.warning("Automatic analysis of source %s not started: %s", source.id, exc.message)
        return None


def get_transcript(db: Session, source: VideoSource) -> Transcript:
    row = db.scalar(select(Transcript).where(Transcript.video_source_id == source.id))
    if row is None:
        raise NotFoundError("No transcript yet; analyse the video first.")
    return row


def media_url(source: VideoSource) -> tuple[str, bool, int]:
    if not source.storage_key:
        raise ConflictError("This video has no stored file.", code="no_file")
    s = get_settings()
    ttl = s.s3_presign_ttl_seconds if s.storage_backend == "s3" else s.media_url_ttl_seconds
    playable = bool((source.media_info or {}).get("browser_playable", False))
    return get_storage().url(source.storage_key, ttl_seconds=ttl), playable, ttl


# -------------------------------------------------------------- manual clip
def create_manual_clip(db: Session, user: User, source: VideoSource, data: ManualClipIn) -> GeneratedClip:
    """Create a clip for an exact range (optionally sentence-refined), with metadata and a clip_vision job."""
    from app.modules.clipping.boundaries import refine_boundaries
    from app.modules.sources import clip_factory

    if not source.storage_key or not source.duration:
        raise ConflictError("Attach the original video file before creating clips.", code="no_file")
    start, end = max(0.0, data.start), min(float(source.duration), data.end)
    if end - start < MIN_MANUAL_CLIP or end - start > MAX_MANUAL_CLIP:
        raise ValidationFailed(f"Clips must be between {MIN_MANUAL_CLIP:g} and {MAX_MANUAL_CLIP:g} seconds long "
                               "and inside the video.", code="invalid_clip_range")
    transcript = clip_factory.load_transcript(db, source)
    if data.refine and transcript is not None:
        analysis = db.scalar(select(VideoAnalysis).where(VideoAnalysis.video_source_id == source.id))
        audio = AudioAnalysis.model_validate(analysis.audio) if analysis and analysis.audio else AudioAnalysis()
        start, end = refine_boundaries(start, end, transcript=transcript, audio=audio,
                                       media_duration=float(source.duration), target=end - start,
                                       min_len=min(8.0, end - start), max_len=MAX_MANUAL_CLIP)
    cand = HighlightCandidate(start=start, end=end, score=0.0, category="other", reasons=["Created manually"],
                              method="manual", transcript=clip_factory.range_text(transcript, start, end),
                              target_duration=round(end - start, 3))
    clips = clip_factory.create_clips(db, source, [cand], use_llm=read_settings(user).default_use_llm)
    if not clips:
        overlap = db.scalars(select(GeneratedClip).where(GeneratedClip.video_source_id == source.id)
                             .order_by(GeneratedClip.created_at.desc())).all()
        from app.modules.clipping.boundaries import temporal_iou

        hit = next((c for c in overlap if temporal_iou(start, end, c.start_time, c.end_time) > 0.6), None)
        raise ConflictError("A clip covering almost the same range already exists.", code="clip_exists",
                            details={"clip_id": str(hit.id) if hit else None})
    clip = clips[0]
    if data.title and data.title.strip():
        clip.title = data.title.strip()[:300]
        db.commit()
    return clip
