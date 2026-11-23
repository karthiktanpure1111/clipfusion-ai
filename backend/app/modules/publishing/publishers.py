"""Platform publishing flows for one queue item (YouTube resumable upload, Instagram Reels).

Flows persist resumable state on the queue item as soon as it exists (YouTube session
URI, Instagram container id) and commit it, so a retry resumes instead of re-uploading.
They raise exceptions on failure; the caller classifies them (``errors.classify``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import QuotaExceeded
from app.core.storage import materialize
from app.integrations import quota
from app.integrations.google_oauth import SCOPE_YOUTUBE_UPLOAD
from app.integrations.instagram import InstagramClient, build_caption
from app.integrations.youtube_upload import YouTubeUploader, build_video_resource
from app.models import ConnectedAccount, GeneratedClip, PublishingQueueItem
from app.models.enums import NotificationType, Platform, QueueStatus
from app.modules.accounts.tokens import get_access_token
from app.modules.publishing import history
from app.modules.publishing.errors import IG_RATE_RETRY, PERMANENT, PublishError
from app.modules.scheduling.service import refresh_item_schedule

log = logging.getLogger(__name__)

Progress = Callable[[float, str | None], None]
_sleep: Callable[[float], None] = time.sleep  # tests may override
IG_INLINE_POLL_SECONDS = 60.0
IG_POLL_INTERVAL = 5.0


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def mark_published(db: Session, item: PublishingQueueItem, message: str) -> None:
    """Final success bookkeeping: status, timestamps, history and notification."""
    from app.modules.notifications.service import notify

    item.status = QueueStatus.PUBLISHED
    item.published_at = item.published_at or utcnow()
    item.last_error = None
    item.locked_at = item.locked_by = None
    history.record(db, item, "publish", QueueStatus.PUBLISHED, message)
    refresh_item_schedule(db, item)
    platform = "YouTube" if item.platform == Platform.YOUTUBE else "Instagram"
    notify(db, item.user_id, NotificationType.PUBLISH_SUCCESS, f"Published to {platform}", message,
           link=f"/app/clips/{item.clip_id}",
           data={"queue_item_id": str(item.id), "external_url": item.external_url, "platform": item.platform})


def _require_render(clip: GeneratedClip) -> str:
    if not clip.output_key:
        raise PublishError(PERMANENT, "The clip has not been rendered yet.")
    return clip.output_key


# ------------------------------------------------------------------ YouTube
def publish_youtube(db: Session, item: PublishingQueueItem, account: ConnectedAccount, clip: GeneratedClip,
                    progress: Progress | None = None) -> None:
    """Resumable upload of the rendered clip; leaves the item processing_remote or published."""
    settings = get_settings()
    if SCOPE_YOUTUBE_UPLOAD not in (account.scopes or []):
        raise PublishError(PERMANENT, "The YouTube connection lacks the upload permission; reconnect it.")
    key = _require_render(clip)
    token = get_access_token(db, account)
    uploader = YouTubeUploader(token)
    resource = build_video_resource(item.payload or {}, default_title=clip.title)
    if not item.upload_session_url:
        quota.ensure_youtube_quota(db, settings.youtube_upload_quota_cost)

    def on_session(url: str) -> None:
        item.upload_session_url = url
        quota.record_usage(db, quota.YOUTUBE, str(account.id), settings.youtube_upload_quota_cost)
        history.record(db, item, "upload", QueueStatus.PUBLISHING, "YouTube resumable upload session started")
        db.commit()

    with materialize(key, ".mp4") as path:
        video = uploader.upload(path, resource, session_url=item.upload_session_url, on_session=on_session,
                                progress=progress)
    video_id = str(video.get("id") or "")
    if not video_id:
        raise PublishError(PERMANENT, "YouTube accepted the upload but returned no video id.")
    status = video.get("status") or {}
    item.external_id = video_id
    item.external_url = youtube_url(video_id)
    item.upload_session_url = None
    item.remote_status = status.get("uploadStatus") or "uploaded"
    requested = resource["status"]["privacyStatus"]
    note = ""
    if requested != "private" and status.get("privacyStatus") == "private":
        note = (" YouTube set the video to private: uploads from unverified API projects are locked to "
                "private until the project passes the YouTube API Services audit.")
    history.record(db, item, "upload", QueueStatus.PROCESSING_REMOTE, f"Uploaded as {video_id}.{note}",
                   details={"privacy_status": status.get("privacyStatus"), "upload_status": item.remote_status})
    _apply_youtube_status(db, item, item.remote_status, None, note)


def _apply_youtube_status(db: Session, item: PublishingQueueItem, upload_status: str | None,
                          reason: str | None, note: str = "") -> None:
    if upload_status == "processed":
        mark_published(db, item, f"Live on YouTube: {item.external_url}.{note}")
    elif upload_status in ("rejected", "failed", "deleted"):
        raise PublishError(PERMANENT, f"YouTube {upload_status} the video" + (f": {reason}" if reason else ""))
    else:
        item.status = QueueStatus.PROCESSING_REMOTE


def poll_youtube(db: Session, item: PublishingQueueItem, account: ConnectedAccount) -> None:
    """Check processing status of an uploaded video (1 quota unit)."""
    token = get_access_token(db, account)
    st = YouTubeUploader(token).get_status(item.external_id or "")
    quota.record_usage(db, quota.YOUTUBE, str(account.id), quota.COST_LIST)
    if st is None:
        raise PublishError(PERMANENT, "The uploaded video no longer exists on YouTube.")
    item.remote_status = st["upload_status"]
    reason = st.get("rejection_reason") or st.get("failure_reason") or st.get("processing_failure_reason")
    _apply_youtube_status(db, item, st["upload_status"], reason)


# ---------------------------------------------------------------- Instagram
def _ig_client(db: Session, account: ConnectedAccount) -> InstagramClient:
    token = get_access_token(db, account)
    ig_user_id = (account.account_metadata or {}).get("ig_user_id") or account.external_account_id
    return InstagramClient(token, str(ig_user_id), flow=account.provider)


def _ig_check_limits(db: Session, client: InstagramClient, account: ConnectedAccount) -> None:
    quota.ensure_instagram_daily_limit(db, str(account.id))
    limit = client.publishing_limit()
    total, used = limit.get("quota_total"), limit.get("quota_usage") or 0
    if total is not None and used >= total:
        retry = utcnow() + IG_RATE_RETRY
        raise QuotaExceeded(f"Instagram's publishing limit for this account is used up ({used}/{total} in the "
                            "last 24 hours).", details={"retry_at": retry.isoformat(), "provider": "instagram"})


def _ig_publish(db: Session, item: PublishingQueueItem, account: ConnectedAccount, client: InstagramClient) -> None:
    _ig_check_limits(db, client, account)
    media_id = client.publish(item.external_container_id or "")
    quota.record_usage(db, quota.INSTAGRAM, str(account.id), 1)
    item.external_id = media_id
    item.remote_status = "PUBLISHED"
    try:
        info = client.media_info(media_id)
        item.external_url = info.get("permalink")
    except Exception as exc:  # noqa: BLE001 - permalink is cosmetic; the Reel is live
        log.warning("Could not fetch permalink for IG media %s: %s", media_id, exc)
    mark_published(db, item, f"Live on Instagram: {item.external_url or media_id}")


def _ig_wait(client: InstagramClient, container_id: str, budget: float) -> tuple[str, str | None]:
    deadline = time.monotonic() + budget
    while True:
        code, status = client.container_status(container_id)
        if code != "IN_PROGRESS" or time.monotonic() >= deadline:
            return code, status
        _sleep(IG_POLL_INTERVAL)


def publish_instagram(db: Session, item: PublishingQueueItem, account: ConnectedAccount, clip: GeneratedClip,
                      progress: Progress | None = None, *, inline_poll_seconds: float = IG_INLINE_POLL_SECONDS) -> None:
    """Create a REELS container, upload via rupload, publish when FINISHED."""
    client = _ig_client(db, account)
    if item.external_container_id:
        code, status = client.container_status(item.external_container_id)
        if code == "FINISHED":
            return _ig_publish(db, item, account, client)
        if code == "IN_PROGRESS":
            item.status = QueueStatus.PROCESSING_REMOTE
            return None
        if code == "PUBLISHED":
            return mark_published(db, item, "Instagram reports the Reel as published.")
        history.record(db, item, "status", code, f"Previous container {code}: {status or ''}; re-uploading")
        item.external_container_id = None
    key = _require_render(clip)
    _ig_check_limits(db, client, account)
    payload = item.payload or {}
    caption = build_caption(payload.get("caption") or "", payload.get("hashtags") or [])
    container = client.create_reel_container(caption, share_to_feed=bool(payload.get("share_to_feed", True)))
    item.external_container_id = container
    item.remote_status = "CONTAINER_CREATED"
    history.record(db, item, "upload", QueueStatus.PUBLISHING, f"Instagram container {container} created")
    db.commit()
    with materialize(key, ".mp4") as path:
        client.upload_video(container, path)
    if progress:
        progress(0.8, "Uploaded to Instagram; waiting for processing")
    item.remote_status = "UPLOADED"
    history.record(db, item, "upload", QueueStatus.PROCESSING_REMOTE, "Video uploaded to Instagram")
    db.commit()
    code, status = _ig_wait(client, container, inline_poll_seconds)
    _apply_container_status(db, item, account, client, code, status)
    return None


def _apply_container_status(db: Session, item: PublishingQueueItem, account: ConnectedAccount,
                            client: InstagramClient, code: str, status: str | None) -> None:
    item.remote_status = code
    if code == "FINISHED":
        _ig_publish(db, item, account, client)
    elif code == "IN_PROGRESS":
        item.status = QueueStatus.PROCESSING_REMOTE
    elif code == "PUBLISHED":
        mark_published(db, item, "Instagram reports the Reel as published.")
    else:
        raise PublishError(PERMANENT, f"Instagram could not process the video ({code}): {status or 'no details'}")


def poll_instagram(db: Session, item: PublishingQueueItem, account: ConnectedAccount) -> None:
    """Poll a container; publish once FINISHED. EXPIRED containers are re-queued for a fresh upload."""
    client = _ig_client(db, account)
    code, status = client.container_status(item.external_container_id or "")
    if code == "EXPIRED":
        item.external_container_id = None
        item.status = QueueStatus.SCHEDULED
        item.scheduled_at = utcnow() + timedelta(minutes=1)
        history.record(db, item, "status", "EXPIRED", "Instagram container expired; re-uploading")
        return
    _apply_container_status(db, item, account, client, code, status)


def publish(db: Session, item: PublishingQueueItem, account: ConnectedAccount, clip: GeneratedClip,
            progress: Progress | None = None) -> None:
    """Dispatch to the platform flow."""
    if item.platform == Platform.YOUTUBE:
        publish_youtube(db, item, account, clip, progress)
    elif item.platform == Platform.INSTAGRAM:
        publish_instagram(db, item, account, clip, progress)
    else:
        raise PublishError(PERMANENT, f"Unsupported platform {item.platform}")


def poll(db: Session, item: PublishingQueueItem, account: ConnectedAccount) -> None:
    if item.platform == Platform.YOUTUBE:
        poll_youtube(db, item, account)
    else:
        poll_instagram(db, item, account)
