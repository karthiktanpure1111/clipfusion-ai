"""End-to-end YouTube publishing through the publish job (real FFmpeg clip; Google APIs mocked)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import utcnow
from app.integrations import quota
from app.integrations.youtube_upload import UPLOAD_URL, VIDEOS_URL
from app.models import Notification, PublishingHistory
from app.models.enums import JobType, NotificationType, QueueStatus
from app.modules.publishing import dispatcher
from app.modules.scheduling import service
from app.workers.jobs import create_job
from app.workers.tasks_publishing import poll_item, publish_queue_item
from tests.helpers.media import make_test_video
from tests.test_publishing_factories import make_account, make_clip

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=flow-1"


@pytest.fixture(autouse=True)
def _fast(monkeypatch) -> None:
    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)
    monkeypatch.setattr(get_settings(), "youtube_upload_chunk_bytes", 256 * 1024)
    monkeypatch.setattr(get_settings(), "youtube_daily_quota", 10_000_000)


@pytest.fixture(scope="module")
def short_file(tmp_media_dir):
    return make_test_video(tmp_media_dir / "short.mp4", duration=3.0, width=720, height=1280)


def _claimed_job(db, user, path):
    acc = make_account(db, user, token="yt-token")
    clip = make_clip(db, user, output_file=path)
    item = service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    item.scheduled_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert dispatcher.claim(db, item.id) is not None
    job, _ = create_job(db, user_id=user.id, job_type=JobType.PUBLISH, queue_item_id=item.id, clip_id=clip.id,
                        idempotency_key=f"publish:{item.id}", max_attempts=1)
    db.commit()
    return acc, item, job


@respx.mock
def test_upload_then_poll_until_processed(db, user, short_file) -> None:
    acc, item, job = _claimed_job(db, user, short_file)
    init = respx.post(url__startswith=UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"Location": SESSION}))
    size = short_file.stat().st_size
    chunks: list[str] = []

    def put(request: httpx.Request) -> httpx.Response:
        cr = request.headers["Content-Range"]
        chunks.append(cr)
        end = int(cr.split("-")[1].split("/")[0])
        if end + 1 < size:
            return httpx.Response(308, headers={"Range": f"bytes=0-{end}"})
        return httpx.Response(200, json={"id": "vidABCDEFGH", "status": {"uploadStatus": "uploaded",
                                                                         "privacyStatus": "private"}})

    respx.put(SESSION).mock(side_effect=put)
    publish_queue_item(str(job.id))
    db.expire_all()
    assert item.status == QueueStatus.PROCESSING_REMOTE, item.last_error
    assert item.external_id == "vidABCDEFGH" and item.external_url == "https://www.youtube.com/watch?v=vidABCDEFGH"
    assert item.upload_session_url is None and init.call_count == 1
    assert chunks[0] == f"bytes 0-{min(size, 256 * 1024) - 1}/{size}"
    # one videos.insert = 1 unit in the uploads bucket; nothing charged to the 10,000-unit bucket by the upload
    assert quota.get_usage(db, quota.YOUTUBE_UPLOADS, str(acc.id)) == 1
    assert quota.get_usage(db, quota.YOUTUBE, str(acc.id)) == 0
    msgs = db.scalars(select(PublishingHistory.message).where(PublishingHistory.queue_item_id == item.id)).all()
    assert any("locked to private" in (m or "") for m in msgs)

    respx.get(VIDEOS_URL).mock(return_value=httpx.Response(200, json={"items": [{
        "status": {"uploadStatus": "processed", "privacyStatus": "private"},
        "processingDetails": {"processingStatus": "succeeded"}}]}))
    assert poll_item(db, item.id) == QueueStatus.PUBLISHED
    assert item.published_at is not None
    assert db.scalar(select(Notification).where(Notification.user_id == user.id,
                                                Notification.type == NotificationType.PUBLISH_SUCCESS))
    assert service.already_published(db, item.clip_id, acc.id)


@respx.mock
def test_interrupted_upload_resumes_on_item_retry(db, user, short_file) -> None:
    acc, item, job = _claimed_job(db, user, short_file)
    init = respx.post(url__startswith=UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"Location": SESSION}))
    session = respx.put(SESSION).mock(return_value=httpx.Response(503, json={"error": {"message": "unavailable"}}))
    publish_queue_item(str(job.id))
    db.expire_all()
    assert item.status == QueueStatus.SCHEDULED and item.attempts == 1
    assert item.upload_session_url == SESSION and "unavailable" in item.last_error

    size = short_file.stat().st_size

    def put(request: httpx.Request) -> httpx.Response:
        if request.headers["Content-Range"] == f"bytes */{size}":
            return httpx.Response(308)  # nothing persisted yet -> restart from byte 0 in the same session
        return httpx.Response(201, json={"id": "vidRESUMED1", "status": {"uploadStatus": "processed"}})

    session.mock(side_effect=put)
    item.scheduled_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert dispatcher.claim(db, item.id) is not None
    job2, _ = create_job(db, user_id=user.id, job_type=JobType.PUBLISH, queue_item_id=item.id,
                         idempotency_key=f"publish:{item.id}", max_attempts=1)
    db.commit()
    publish_queue_item(str(job2.id))
    db.expire_all()
    assert item.status == QueueStatus.PUBLISHED and item.external_id == "vidRESUMED1"
    assert init.call_count == 1  # the persisted session was resumed, not re-created


@respx.mock
def test_daily_quota_reschedules_to_pacific_midnight(db, user, short_file, monkeypatch) -> None:
    # today's upload bucket is full (whatever the other tests already used)
    monkeypatch.setattr(get_settings(), "youtube_daily_upload_limit", quota.get_usage(db, quota.YOUTUBE_UPLOADS))
    acc, item, job = _claimed_job(db, user, short_file)
    init = respx.post(url__startswith=UPLOAD_URL)
    publish_queue_item(str(job.id))
    db.expire_all()
    assert not init.called
    assert item.status == QueueStatus.SCHEDULED and item.attempts == 0
    assert item.scheduled_at == quota.next_reset(quota.YOUTUBE)
    assert "quota" in item.last_error.lower()


@respx.mock
def test_revoked_account_fails_permanently(db, user, short_file) -> None:
    acc, item, job = _claimed_job(db, user, short_file)
    acc.scopes = []
    db.commit()
    publish_queue_item(str(job.id))
    db.expire_all()
    assert item.status == QueueStatus.FAILED and "upload permission" in item.last_error
    assert db.scalar(select(Notification).where(Notification.user_id == user.id,
                                                Notification.type == NotificationType.PUBLISH_FAILED))


@respx.mock
def test_full_10000_unit_bucket_does_not_block_uploads(db, user, short_file, monkeypatch) -> None:
    # the old rule (1,600 units per upload checked against 10,000 combined units) would refuse this upload
    monkeypatch.setattr(get_settings(), "youtube_daily_quota", quota.get_usage(db, quota.YOUTUBE))
    acc, item, job = _claimed_job(db, user, short_file)
    init = respx.post(url__startswith=UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"Location": SESSION}))
    respx.put(SESSION).mock(return_value=httpx.Response(200, json={"id": "vidQUOTA001", "status": {
        "uploadStatus": "uploaded", "privacyStatus": "private"}}))
    publish_queue_item(str(job.id))
    db.expire_all()
    assert init.call_count == 1 and item.external_id == "vidQUOTA001", item.last_error


@respx.mock
def test_youtube_quota_error_marks_the_upload_bucket_used_up(db, user, short_file) -> None:
    from app.models import ApiQuotaUsage

    acc, item, job = _claimed_job(db, user, short_file)
    respx.post(url__startswith=UPLOAD_URL).mock(return_value=httpx.Response(403, json={"error": {
        "code": 403, "message": "The request cannot be completed because you have exceeded your quota.",
        "errors": [{"reason": "quotaExceeded"}]}}))
    try:
        publish_queue_item(str(job.id))
        db.expire_all()
        assert item.status == QueueStatus.SCHEDULED and item.attempts == 0  # waits, never counted as failed
        assert item.scheduled_at == quota.next_reset(quota.YOUTUBE) and "midnight pacific" in item.last_error.lower()
        assert item.external_id is None  # nothing assumed uploaded
        assert quota.youtube_status(db)["uploads"]["remaining"] == 0  # YouTube's answer wins over our count
    finally:  # the test database is shared: give the bucket back to the other tests
        db.query(ApiQuotaUsage).filter(ApiQuotaUsage.provider == quota.YOUTUBE_UPLOADS,
                                       ApiQuotaUsage.scope_key == str(acc.id)).delete()
        db.commit()


@respx.mock
def test_retry_after_youtube_accepted_the_video_never_uploads_twice(db, user, short_file) -> None:
    acc, item, job = _claimed_job(db, user, short_file)
    item.external_id, item.remote_status = "vidALREADY1", "uploaded"  # accepted by YouTube on an earlier try
    db.commit()
    before = quota.get_usage(db, quota.YOUTUBE_UPLOADS, str(acc.id))
    init = respx.post(url__startswith=UPLOAD_URL)
    respx.get(VIDEOS_URL).mock(return_value=httpx.Response(200, json={"items": [{
        "id": "vidALREADY1", "status": {"uploadStatus": "processed", "privacyStatus": "private"}}]}))
    publish_queue_item(str(job.id))
    db.expire_all()
    assert not init.called and item.status == QueueStatus.PUBLISHED and item.external_id == "vidALREADY1"
    assert quota.get_usage(db, quota.YOUTUBE_UPLOADS, str(acc.id)) == before  # no second videos.insert
