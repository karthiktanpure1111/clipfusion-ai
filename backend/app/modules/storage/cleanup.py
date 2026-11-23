"""Clip media cleanup after a *verified* YouTube upload.

A clip's media (render MP4, proxy, thumbnail, temp) is deleted only when

1. YouTube returned a video id for the upload (``clip.upload_status == "uploaded"`` and
   ``clip.youtube_video_id``), and
2. a follow-up ``videos.list`` confirms the video exists and its ``uploadStatus`` is
   ``processed`` (``uploaded`` = YouTube is still processing it -> checked again later;
   ``failed``/``rejected``/``deleted`` -> the clip is marked ``upload_failed`` and nothing is
   deleted), and
3. no other publication of the clip still needs the file.

Anything uncertain (network error, timeout, quota, missing account, video not listed yet)
never deletes; the maintenance worker retries. Cleanup is idempotent and only touches
``users/{uid}/clips/{clip_id}/``. The row (title, description, hashtags, timestamps, YouTube
id/URL, upload history) is kept."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.models import ConnectedAccount, GeneratedClip, PublishingQueueItem, VideoSource
from app.models.enums import (
    AccountStatus,
    CleanupStatus,
    NotificationType,
    Platform,
    QueueStatus,
    UploadStatus,
)
from app.modules.storage.service import delete_clip_media, maybe_auto_delete_original

log = logging.getLogger(__name__)

Verdict = Literal["verified", "processing", "failed", "unknown"]
VERIFIED = {"processed"}
PROCESSING = {"uploaded"}
BAD = {"failed", "rejected", "deleted"}
NEEDS_FILE = (QueueStatus.PENDING_APPROVAL, QueueStatus.SCHEDULED, QueueStatus.RENDERING, QueueStatus.PUBLISHING)
MISSING_GRACE = timedelta(hours=6)  # a just-uploaded video may not be listed yet


def youtube_thumbnail(video_id: str | None) -> str | None:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None


def _account_for(db: Session, clip: GeneratedClip) -> ConnectedAccount | None:
    item = db.scalar(select(PublishingQueueItem).where(
        PublishingQueueItem.clip_id == clip.id, PublishingQueueItem.platform == Platform.YOUTUBE,
        PublishingQueueItem.external_id == clip.youtube_video_id).limit(1))
    if item is not None:
        acc = db.get(ConnectedAccount, item.connected_account_id)
        if acc is not None:
            return acc
    return db.scalar(select(ConnectedAccount).where(
        ConnectedAccount.user_id == clip.user_id, ConnectedAccount.platform == Platform.YOUTUBE,
        ConnectedAccount.status == AccountStatus.ACTIVE).order_by(ConnectedAccount.created_at).limit(1))


def mark_upload_failed(db: Session, clip: GeneratedClip, message: str) -> None:
    """The upload turned out to be unusable (rejected/failed/deleted on YouTube). Never deletes; no commit."""
    clip.upload_status = UploadStatus.FAILED
    clip.upload_error = message[:2000]


def verify_upload(db: Session, clip: GeneratedClip) -> tuple[Verdict, str | None]:
    """``videos.list`` check of the clip's YouTube video. Never raises."""
    from app.integrations import quota
    from app.integrations.youtube_upload import YouTubeUploader
    from app.modules.accounts.tokens import get_access_token

    video_id = clip.youtube_video_id
    if not video_id:
        return "unknown", "No YouTube video id was recorded for this clip."
    account = _account_for(db, clip)
    if account is None:
        return "unknown", "No connected YouTube account can confirm the upload."
    try:
        token = get_access_token(db, account)
        status = YouTubeUploader(token).get_status(video_id)
        quota.record_usage(db, quota.YOUTUBE, str(account.id), quota.COST_LIST)
    except Exception as exc:  # noqa: BLE001 - uncertain -> keep the files, retry later
        message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
        log.info("Could not verify YouTube video %s of clip %s: %s", video_id, clip.id, message)
        return "unknown", f"Could not confirm the upload with YouTube yet: {message}"[:500]
    if status is None:
        uploaded = clip.uploaded_at or utcnow()
        if utcnow() - uploaded > MISSING_GRACE:
            return "failed", "The uploaded video no longer exists on YouTube."
        return "unknown", "YouTube does not list the video yet."
    upload_status = str(status.get("upload_status") or "")
    if upload_status in VERIFIED:
        return "verified", None
    if upload_status in BAD:
        reason = status.get("rejection_reason") or status.get("failure_reason") or \
            status.get("processing_failure_reason")
        return "failed", f"YouTube {upload_status} the video" + (f": {reason}" if reason else ".")
    if upload_status in PROCESSING:
        return "processing", "YouTube is still processing the video."
    return "unknown", f"Unexpected YouTube upload status '{upload_status or 'none'}'."


def _other_publications_pending(db: Session, clip: GeneratedClip) -> bool:
    return db.scalar(select(PublishingQueueItem.id).where(
        PublishingQueueItem.clip_id == clip.id, PublishingQueueItem.status.in_(NEEDS_FILE),
        or_(PublishingQueueItem.external_id.is_(None),
            PublishingQueueItem.external_id != clip.youtube_video_id)).limit(1)) is not None


def cleanup_after_upload(db: Session, clip: GeneratedClip) -> str:
    """Verify the clip's upload and, when confirmed, delete only its media. Returns
    ``cleaned`` | ``already`` | ``not_uploaded`` | ``waiting`` | ``upload_failed`` | ``cleanup_failed``.
    Commits. Idempotent; never raises."""
    from app.modules.notifications.service import notify

    if clip.cleanup_status == CleanupStatus.CLEANED:
        return "already"
    if clip.removed_at is not None:
        return "already"  # Remove Clip already deleted the media
    if clip.upload_status != UploadStatus.UPLOADED or not clip.youtube_video_id:
        return "not_uploaded"
    if _other_publications_pending(db, clip):
        return "waiting"
    verdict, message = verify_upload(db, clip)
    clip.cleanup_checked_at = utcnow()
    if verdict == "failed":
        mark_upload_failed(db, clip, message or "The YouTube upload failed.")
        clip.cleanup_error = None
        notify(db, clip.user_id, NotificationType.PUBLISH_FAILED, "YouTube upload failed",
               f"“{clip.title or 'Your clip'}”: {clip.upload_error} The clip's files were kept so you can "
               "upload it again.", link=f"/app/clips/{clip.id}", data={"clip_id": str(clip.id)})
        db.commit()
        return "upload_failed"
    if verdict != "verified":
        clip.cleanup_error = message
        db.commit()
        return "waiting"
    try:
        freed = delete_clip_media(db, clip)
    except Exception as exc:  # noqa: BLE001 - logged, retried by the maintenance worker
        db.rollback()
        clip = db.get(GeneratedClip, clip.id)
        clip.cleanup_status = CleanupStatus.FAILED
        clip.cleanup_error = f"Deleting the clip's files failed: {exc}"[:2000]
        clip.cleanup_checked_at = utcnow()
        db.commit()
        log.warning("Cleanup of clip %s failed: %s", clip.id, exc)
        return "cleanup_failed"
    clip.cleanup_status = CleanupStatus.CLEANED
    clip.cleaned_at = utcnow()
    clip.freed_bytes = int(clip.freed_bytes or 0) + int(freed)
    clip.cleanup_error = None
    db.commit()
    log.info("Clip %s media deleted after verified upload %s (%d bytes)", clip.id, clip.youtube_video_id, freed)
    maybe_auto_delete_original(db, db.get(VideoSource, clip.video_source_id))
    return "cleaned"


def pending_cleanups(db: Session, *, limit: int = 50) -> list[GeneratedClip]:
    """Uploaded clips whose media is still stored (oldest check first)."""
    return list(db.scalars(select(GeneratedClip).where(
        GeneratedClip.upload_status == UploadStatus.UPLOADED, GeneratedClip.youtube_video_id.is_not(None),
        GeneratedClip.removed_at.is_(None), GeneratedClip.cleanup_status != CleanupStatus.CLEANED)
        .order_by(GeneratedClip.cleanup_checked_at.asc().nulls_first(), GeneratedClip.uploaded_at).limit(limit)))
