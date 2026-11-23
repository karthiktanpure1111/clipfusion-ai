"""Scheduling service: enqueue + duplicate prevention, contract hooks, pause, dispatcher claiming."""

from __future__ import annotations

import sys
import types
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.database import utcnow
from app.core.errors import ConflictError, ValidationFailed
from app.integrations.http import ProviderError
from app.models import Notification, ProcessingJob, PublishingHistory, PublishingQueueItem
from app.models.enums import NotificationType, Platform, QueueStatus, RenderStatus, ReviewStatus
from app.modules.publishing import dispatcher, history
from app.modules.publishing.errors import PERMANENT, PublishError
from app.modules.scheduling import service
from tests.test_publishing_factories import make_account, make_clip, make_schedule


@pytest.fixture()
def enqueued(monkeypatch):
    """Capture task dispatches instead of running them."""
    calls: list[tuple[str, dict]] = []

    def fake_enqueue(name, countdown=None, **kwargs):  # noqa: ANN001, ANN202
        calls.append((name, kwargs))
        return "task-1"

    monkeypatch.setattr("app.workers.jobs.enqueue", fake_enqueue)
    return calls


def _items(db, clip) -> list[PublishingQueueItem]:
    return list(db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clip.id)))


def test_enqueue_requires_rights_and_prevents_duplicates(db, user) -> None:
    acc = make_account(db, user)
    clip = make_clip(db, user)
    with pytest.raises(ValidationFailed):
        service.enqueue_clip(db, clip, acc, rights_confirmed=False)
    item = service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    db.commit()
    assert item.idempotency_key == f"{clip.id}:{acc.id}"
    assert item.payload["title"] == "Epic moment #shorts" and item.payload["tags"] == ["gaming", "clip"]
    assert item.scheduled_at > utcnow() + timedelta(minutes=4)
    with pytest.raises(ConflictError) as exc:
        service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    assert exc.value.code == "already_queued"
    # A recorded successful publication blocks re-queueing even after cancellation.
    item.status = QueueStatus.CANCELLED
    history.record(db, item, "publish", QueueStatus.PUBLISHED, "done")
    db.commit()
    with pytest.raises(ConflictError) as exc:
        service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    assert exc.value.code == "already_published"


def test_failed_item_can_be_requeued_with_same_key(db, user) -> None:
    acc = make_account(db, user, Platform.INSTAGRAM)
    clip = make_clip(db, user)
    item = service.enqueue_clip(db, clip, acc, rights_confirmed=True, payload_overrides={"caption": "Custom"})
    assert item.payload == {"caption": "Custom", "hashtags": ["gaming", "reels"], "share_to_feed": True}
    item.status = QueueStatus.FAILED
    db.commit()
    again = service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    db.commit()
    assert again.id == item.id and again.status == QueueStatus.SCHEDULED and again.attempts == 0


def test_approval_hook_is_gone() -> None:
    assert not hasattr(service, "on_clip_approved")  # API v2: no approve/reject


def test_on_clips_generated_automatic_mode_only(db, user) -> None:
    auto_acc, review_acc = make_account(db, user), make_account(db, user, Platform.INSTAGRAM)
    make_schedule(db, user, auto_acc, mode="automatic", auto_min_score=60)
    make_schedule(db, user, review_acc, mode="review")
    good, weak = make_clip(db, user, score=82), make_clip(db, user, score=40)
    items = service.on_clips_generated(db, user, [weak, good])
    db.commit()
    assert [i.clip_id for i in items] == [good.id]
    assert items[0].connected_account_id == auto_acc.id
    assert good.review_status == ReviewStatus.PENDING  # no approval step any more (column unused)
    good.removed_at = utcnow()
    assert service.on_clips_generated(db, user, [good]) == []  # removed clips are never queued


def test_automatic_schedule_requires_rights_confirmation(db, user) -> None:
    acc = make_account(db, user)
    with pytest.raises(ValidationFailed):
        service.create_schedule(db, user, {"connected_account_id": acc.id, "mode": "automatic",
                                           "interval_minutes": 120})
    s = service.create_schedule(db, user, {"connected_account_id": acc.id, "mode": "automatic",
                                           "interval_minutes": 120, "rights_confirmed": True})
    assert s.rights_confirmed_at is not None and s.platform == Platform.YOUTUBE


def test_render_finished_hook(db, user) -> None:
    acc = make_account(db, user)
    ok_clip, bad_clip = make_clip(db, user), make_clip(db, user)
    a = service.enqueue_clip(db, ok_clip, acc, rights_confirmed=True)
    b = service.enqueue_clip(db, bad_clip, acc, rights_confirmed=True)
    a.status = b.status = QueueStatus.RENDERING
    db.commit()
    service.on_clip_render_finished(db, ok_clip, True)
    bad_clip.render_error = "encoder crashed"
    service.on_clip_render_finished(db, bad_clip, False)
    db.commit()
    assert a.status == QueueStatus.SCHEDULED
    assert b.status == QueueStatus.FAILED and "encoder crashed" in b.last_error
    notes = db.scalars(select(Notification).where(Notification.user_id == user.id,
                                                  Notification.type == NotificationType.PUBLISH_FAILED)).all()
    assert len(notes) == 1


def test_next_publish_time_and_pause(db, user) -> None:
    acc = make_account(db, user)
    clip = make_clip(db, user)
    when = utcnow() + timedelta(hours=3)
    service.enqueue_clip(db, clip, acc, scheduled_at=when, rights_confirmed=True)
    db.commit()
    assert service.next_publish_time(db, user) == when
    service.set_user_paused(db, user, True)
    db.commit()
    assert service.next_publish_time(db, user) is None
    service.set_user_paused(db, user, False)
    assert service.next_publish_time(db, user) == when


def test_claim_is_exclusive_on_sqlite(db, user) -> None:
    acc = make_account(db, user)
    item = service.enqueue_clip(db, make_clip(db, user), acc, rights_confirmed=True)
    item.scheduled_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert any(c.id == item.id for c in dispatcher.due_candidates(db, utcnow()))
    first = dispatcher.claim(db, item.id)
    second = dispatcher.claim(db, item.id)
    assert first is not None and first.status == QueueStatus.PUBLISHING and first.locked_by
    assert second is None


def test_due_candidates_skip_paused_schedule_and_user(db, user, other_user) -> None:
    acc = make_account(db, user)
    sched = make_schedule(db, user, acc, is_active=False)
    item = service.enqueue_clip(db, make_clip(db, user), acc, schedule=None, rights_confirmed=True)
    item.schedule_id = sched.id
    other_acc = make_account(db, other_user)
    other_item = service.enqueue_clip(db, make_clip(db, other_user), other_acc, rights_confirmed=True)
    other_user.settings = {"publishing_paused": True}
    item.scheduled_at = other_item.scheduled_at = utcnow() - timedelta(minutes=1)
    db.commit()
    ids = {c.id for c in dispatcher.due_candidates(db, utcnow())}
    assert item.id not in ids and other_item.id not in ids


def test_dispatch_creates_publish_job_when_render_is_current(db, user, enqueued, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "app.modules.rendering.service", types.SimpleNamespace(
        render_is_current=lambda clip: True, request_render=None))
    acc = make_account(db, user)
    clip = make_clip(db, user)
    item = service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    item.scheduled_at = utcnow() - timedelta(seconds=5)
    db.commit()
    dispatcher.dispatch_due(db)
    db.refresh(item)
    assert item.status == QueueStatus.PUBLISHING
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.queue_item_id == item.id))
    assert job.idempotency_key == f"publish:{item.id}" and job.max_attempts == 1
    assert ("publish_queue_item", {"job_id": str(job.id)}) in enqueued


def test_dispatch_requests_render_or_fails_on_framing_warnings(db, user, enqueued, monkeypatch) -> None:
    requested = []

    def request_render(db_, clip, *, acknowledge_warnings=False, reason="user"):  # noqa: ANN001, ANN202
        if clip.title == "warn":
            raise ConflictError("Face cropped", code="framing_warnings_unacknowledged")
        requested.append((clip.id, acknowledge_warnings, reason))

    monkeypatch.setitem(sys.modules, "app.modules.rendering.service", types.SimpleNamespace(
        render_is_current=lambda clip: False, request_render=request_render))
    acc = make_account(db, user)
    clip_ok, clip_warn = make_clip(db, user), make_clip(db, user)
    clip_warn.title = "warn"
    a = service.enqueue_clip(db, clip_ok, acc, rights_confirmed=True)
    b = service.enqueue_clip(db, clip_warn, acc, rights_confirmed=True)
    a.scheduled_at = b.scheduled_at = utcnow() - timedelta(seconds=5)
    db.commit()
    dispatcher.dispatch_due(db)
    db.refresh(a)
    db.refresh(b)
    assert a.status == QueueStatus.RENDERING and requested == [(clip_ok.id, False, "publish")]
    assert b.status == QueueStatus.FAILED and "framing" in b.last_error
    # Render finishing releases the item; safety net also handles it.
    clip_ok.render_status = RenderStatus.RENDERED
    db.commit()
    dispatcher.release_rendered(db)
    db.refresh(a)
    assert a.status == QueueStatus.SCHEDULED


def test_item_level_retry_policy(db, user) -> None:
    acc = make_account(db, user)
    item = service.enqueue_clip(db, make_clip(db, user), acc, rights_confirmed=True)
    db.commit()
    now = utcnow()
    for expected in (2, 10, 30, 120):
        status = dispatcher.handle_failure(db, item, ProviderError("youtube", "backend down", status=503), now=now)
        assert status == QueueStatus.SCHEDULED
        assert item.scheduled_at == now + timedelta(minutes=expected)
    status = dispatcher.handle_failure(db, item, ProviderError("youtube", "backend down", status=503), now=now)
    assert status == QueueStatus.FAILED and item.attempts == 5 and "Gave up" in item.last_error


def test_quota_and_permanent_failures(db, user) -> None:
    acc = make_account(db, user)
    item = service.enqueue_clip(db, make_clip(db, user), acc, rights_confirmed=True)
    db.commit()
    quota_err = ProviderError("youtube", "quota", status=403, reason="quotaExceeded")
    assert dispatcher.handle_failure(db, item, quota_err) == QueueStatus.SCHEDULED
    assert item.attempts == 0 and item.scheduled_at > utcnow() + timedelta(minutes=1)
    assert dispatcher.handle_failure(db, item, PublishError(PERMANENT, "Invalid media")) == QueueStatus.FAILED
    db.commit()
    rows = db.scalars(select(PublishingHistory.action).where(PublishingHistory.queue_item_id == item.id)).all()
    assert {"scheduled", "retry", "failed"} <= set(rows)
