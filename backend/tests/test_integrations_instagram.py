"""Instagram Reels publishing (container -> rupload -> poll -> publish) via the real job handler."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import utcnow
from app.integrations import quota
from app.integrations.http import ProviderError
from app.integrations.instagram import InstagramClient, build_caption
from app.models import Notification, ProcessingJob, PublishingHistory
from app.models.enums import JobStatus, JobType, NotificationType, Platform, QueueStatus
from app.modules.publishing import dispatcher
from app.modules.scheduling import service
from app.workers.jobs import create_job
from app.workers.tasks_publishing import poll_item, publish_queue_item
from tests.helpers.media import make_test_video
from tests.test_publishing_factories import make_account, make_clip

V = get_settings().meta_graph_version
GRAPH = f"https://graph.instagram.com/{V}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{V}"


@pytest.fixture(autouse=True)
def _fast(monkeypatch) -> None:
    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)
    monkeypatch.setattr("app.modules.publishing.publishers._sleep", lambda s: None)


@pytest.fixture(scope="module")
def reel_file(tmp_media_dir):
    return make_test_video(tmp_media_dir / "reel.mp4", duration=2.0, width=540, height=960)


def _claimed_job(db, user, reel_file, *, caption_overrides=None):
    acc = make_account(db, user, Platform.INSTAGRAM, token="ig-token")
    clip = make_clip(db, user, output_file=reel_file)
    item = service.enqueue_clip(db, clip, acc, rights_confirmed=True, payload_overrides=caption_overrides)
    item.scheduled_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert dispatcher.claim(db, item.id) is not None
    job, _ = create_job(db, user_id=user.id, job_type=JobType.PUBLISH, queue_item_id=item.id, clip_id=clip.id,
                        idempotency_key=f"publish:{item.id}", max_attempts=1)
    db.commit()
    return acc, clip, item, job


def _mock_limits(ig_id: str, used: int = 3, total: int = 50) -> respx.Route:
    return respx.get(f"{GRAPH}/{ig_id}/content_publishing_limit").mock(return_value=httpx.Response(
        200, json={"data": [{"config": {"quota_total": total, "quota_duration": 86400}, "quota_usage": used}]}))


def test_build_caption_limits() -> None:
    caption = build_caption("Great play #gaming", [f"tag{i}" for i in range(40)] + ["gaming"])
    tags = [w for w in caption.split() if w.startswith("#")]
    assert len(tags) == 30 and caption.count("#gaming") == 1
    long = build_caption("x" * 2300, ["a", "b"])
    assert len(long) <= 2200


@respx.mock
def test_reel_publish_flow(db, user, reel_file) -> None:
    acc, clip, item, job = _claimed_job(db, user, reel_file)
    ig = acc.external_account_id
    limits = _mock_limits(ig)
    create = respx.post(f"{GRAPH}/{ig}/media").mock(return_value=httpx.Response(200, json={"id": "C17"}))
    upload = respx.post(f"{RUPLOAD}/C17").mock(return_value=httpx.Response(200, json={"success": True}))
    statuses = iter([{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED", "status": "Finished: ok"}])
    respx.get(f"{GRAPH}/C17").mock(side_effect=lambda req: httpx.Response(200, json=next(statuses)))
    publish = respx.post(f"{GRAPH}/{ig}/media_publish").mock(return_value=httpx.Response(200, json={"id": "M99"}))
    respx.get(f"{GRAPH}/M99").mock(return_value=httpx.Response(200, json={
        "permalink": "https://www.instagram.com/reel/abc/", "shortcode": "abc"}))

    publish_queue_item(str(job.id))

    db.expire_all()
    assert item.status == QueueStatus.PUBLISHED, item.last_error
    assert item.external_id == "M99" and item.external_url == "https://www.instagram.com/reel/abc/"
    assert item.external_container_id == "C17" and item.published_at is not None
    form = dict(httpx.QueryParams(create.calls[0].request.content.decode()))
    assert form["media_type"] == "REELS" and form["upload_type"] == "resumable"
    assert form["caption"] == "Epic moment\n\n#gaming #reels" and form["share_to_feed"] == "true"
    up = upload.calls[0].request
    size = reel_file.stat().st_size
    assert up.headers["Authorization"] == "OAuth ig-token" and up.headers["offset"] == "0"
    assert up.headers["file_size"] == str(size) and len(up.content) == size
    assert dict(httpx.QueryParams(publish.calls[0].request.content.decode()))["creation_id"] == "C17"
    assert limits.called
    assert quota.get_usage(db, quota.INSTAGRAM, str(acc.id)) == 1
    assert db.get(ProcessingJob, job.id).status == JobStatus.SUCCEEDED
    actions = db.scalars(select(PublishingHistory.action).where(PublishingHistory.queue_item_id == item.id)).all()
    assert "publish" in actions and "upload" in actions
    assert db.scalar(select(Notification).where(Notification.user_id == user.id,
                                                Notification.type == NotificationType.PUBLISH_SUCCESS))


@respx.mock
def test_container_error_fails_item_permanently(db, user, reel_file) -> None:
    acc, clip, item, job = _claimed_job(db, user, reel_file)
    ig = acc.external_account_id
    _mock_limits(ig)
    respx.post(f"{GRAPH}/{ig}/media").mock(return_value=httpx.Response(200, json={"id": "C18"}))
    respx.post(f"{RUPLOAD}/C18").mock(return_value=httpx.Response(200, json={"success": True}))
    respx.get(f"{GRAPH}/C18").mock(return_value=httpx.Response(200, json={
        "status_code": "ERROR", "status": "Error: unsupported video codec"}))
    publish_queue_item(str(job.id))
    db.expire_all()
    assert item.status == QueueStatus.FAILED and "unsupported video codec" in item.last_error
    assert db.get(ProcessingJob, job.id).status == JobStatus.FAILED


@respx.mock
def test_publishing_limit_reschedules_without_counting_attempt(db, user, reel_file) -> None:
    acc, clip, item, job = _claimed_job(db, user, reel_file)
    _mock_limits(acc.external_account_id, used=50, total=50)
    create = respx.post(f"{GRAPH}/{acc.external_account_id}/media")
    publish_queue_item(str(job.id))
    db.expire_all()
    assert item.status == QueueStatus.SCHEDULED and item.attempts == 0
    assert item.scheduled_at > utcnow() + timedelta(minutes=90) and not create.called


@respx.mock
def test_transient_upload_error_keeps_container_for_retry(db, user, reel_file) -> None:
    acc, clip, item, job = _claimed_job(db, user, reel_file)
    ig = acc.external_account_id
    _mock_limits(ig)
    respx.post(f"{GRAPH}/{ig}/media").mock(return_value=httpx.Response(200, json={"id": "C19"}))
    respx.post(f"{RUPLOAD}/C19").mock(return_value=httpx.Response(503, json={"error": {"message": "busy"}}))
    publish_queue_item(str(job.id))
    db.expire_all()
    assert item.status == QueueStatus.SCHEDULED and item.attempts == 1
    assert item.external_container_id == "C19" and item.scheduled_at > utcnow() + timedelta(minutes=1)


@respx.mock
def test_poll_publishes_finished_container(db, user, reel_file) -> None:
    acc, clip, item, _job = _claimed_job(db, user, reel_file)
    ig = acc.external_account_id
    item.status = QueueStatus.PROCESSING_REMOTE
    item.external_container_id = "C20"
    db.commit()
    _mock_limits(ig)
    respx.get(f"{GRAPH}/C20").mock(return_value=httpx.Response(200, json={"status_code": "FINISHED"}))
    respx.post(f"{GRAPH}/{ig}/media_publish").mock(return_value=httpx.Response(200, json={"id": "M20"}))
    respx.get(f"{GRAPH}/M20").mock(return_value=httpx.Response(500, json={"error": {"message": "x"}}))
    assert poll_item(db, item.id) == QueueStatus.PUBLISHED
    assert item.external_id == "M20" and item.external_url is None


@respx.mock
def test_insights_fall_back_per_metric() -> None:
    client = InstagramClient("tok", "17841400000000000")

    def insights(request: httpx.Request) -> httpx.Response:
        metric = request.url.params["metric"]
        if "," in metric or metric == "saved":
            return httpx.Response(400, json={"error": {"message": f"metric {metric} unsupported", "code": 100}})
        return httpx.Response(200, json={"data": [{"name": metric, "values": [{"value": 7}]}]})

    respx.get(f"{GRAPH}/M1/insights").mock(side_effect=insights)
    respx.get(f"{GRAPH}/M1").mock(return_value=httpx.Response(200, json={"like_count": 5, "comments_count": 2}))
    out = client.insights("M1")
    assert out["views"] == 7 and out["reach"] == 7 and out["saves"] is None
    assert "saved" in out["unsupported"]
    respx.get(f"{GRAPH}/M2/insights").mock(return_value=httpx.Response(500, json={"error": {"message": "x"}}))
    with pytest.raises(ProviderError):
        client.insights("M2")
