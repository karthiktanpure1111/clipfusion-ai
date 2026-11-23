"""Timeline scheduling: clips scheduled one by one a gap apart (the first right away), auto-schedule of a
video's clips once they are rendered, missed slots."""

from __future__ import annotations

from datetime import datetime, timedelta

import respx
from sqlalchemy import select

from app.core.database import utcnow
from app.models import GeneratedClip, Notification, PublishingQueueItem
from app.models.enums import JobType, QueueStatus
from app.modules.scheduling.slots import ensure_utc
from tests.test_upload_runs_factories import (  # noqa: F401 - fixtures
    API,
    FakeYouTube,
    clip_files_exist,
    fast_youtube,
    headers,
    make_world,
    run_publish_jobs,
    tasks,
)

HOUR = timedelta(hours=1)


def _schedule(client, user, acc, src, **extra):  # noqa: ANN001, ANN003, ANN202
    body = {"video_source_id": str(src.id), "connected_account_id": str(acc.id), "rights_confirmed": True, **extra}
    return client.post(f"{API}/publishing/timeline", json=body, headers=headers(user))


def _items(db, acc) -> list[PublishingQueueItem]:  # noqa: ANN001
    db.expire_all()
    return list(db.scalars(select(PublishingQueueItem).where(PublishingQueueItem.connected_account_id == acc.id)
                           .order_by(PublishingQueueItem.scheduled_at)))


def _clip_title(db, item) -> str:  # noqa: ANN001
    return db.get(GeneratedClip, item.clip_id).title


def test_first_clip_goes_now_then_one_hour_apart_in_timeline_order(client, db, user, tasks) -> None:  # noqa: F811
    acc, src, clips = make_world(db, user, [200.0, 20.0, 100.0])
    assert _schedule(client, user, acc, src, rights_confirmed=False).json()["error"]["code"] == "rights_not_confirmed"
    before = utcnow()
    r = _schedule(client, user, acc, src, privacy="unlisted")
    assert r.status_code == 201, r.text
    assert r.json()["scheduled"] == 3
    items = _items(db, acc)
    assert [_clip_title(db, i) for i in items] == ["Clip at 20", "Clip at 100", "Clip at 200"]
    times = [ensure_utc(i.scheduled_at) for i in items]
    assert before - timedelta(seconds=1) <= times[0] <= utcnow()  # clip 1: right now
    assert times[1] - times[0] == HOUR and times[2] - times[1] == HOUR
    assert items[0].status == QueueStatus.PUBLISHING  # started immediately
    assert [name for name, _ in tasks].count("publish_queue_item") == 1
    assert all(i.origin == "timeline" and i.payload["privacy"] == "unlisted" for i in items)
    assert "Some Creator" in items[0].payload["description"]  # source credit appended
    page = client.get(f"{API}/clips", params={"video_source_id": str(src.id)}, headers=headers(user)).json()
    assert [i["scheduled_at"] is not None for i in page["items"]] == [False, True, True]  # 1st is uploading
    assert page["items"][1]["scheduled_item_id"] == str(items[1].id)
    # scheduled (or uploading) clips are not picked by "Upload Remaining" and can't be scheduled twice
    run = client.post(f"{API}/upload-runs", headers=headers(user), json={
        "video_source_id": str(src.id), "connected_account_id": str(acc.id), "mode": "remaining",
        "rights_confirmed": True})
    assert run.status_code == 409 and run.json()["error"]["code"] == "nothing_to_upload"
    again = _schedule(client, user, acc, src)
    assert again.status_code == 409 and again.json()["error"]["code"] == "nothing_to_schedule"
    nxt = client.get(f"{API}/publishing/timeline/next", params={"account_id": str(acc.id), "gap_minutes": 60},
                     headers=headers(user)).json()
    assert ensure_utc(datetime.fromisoformat(nxt["next_at"])) == times[2] + HOUR
    # clearing the schedule frees the waiting clips (the uploading one keeps going)
    assert client.post(f"{API}/publishing/timeline/cancel", json={"video_source_id": str(src.id)},
                       headers=headers(user)).json() == {"cancelled": 2}
    assert _schedule(client, user, acc, src, gap_minutes=120).json()["scheduled"] == 2  # cancelled ones reopen
    t = [ensure_utc(i.scheduled_at) for i in _items(db, acc)]
    assert t[1] - t[0] == 2 * HOUR and t[2] - t[1] == 2 * HOUR  # after the post that is uploading


def test_custom_start_time_and_past_start_refused(client, db, user, tasks) -> None:  # noqa: F811
    acc, src, _clips = make_world(db, user, [10.0, 70.0])
    start = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    assert _schedule(client, user, acc, src, start_at=(utcnow() - HOUR).isoformat()).json()["error"]["code"] == \
        "start_in_past"
    assert _schedule(client, user, acc, src, start_at=start.isoformat(), gap_minutes=30).status_code == 201
    assert [ensure_utc(i.scheduled_at) for i in _items(db, acc)] == [start, start + timedelta(minutes=30)]
    assert not any(name == "publish_queue_item" for name, _ in tasks)  # nothing due yet


@respx.mock
def test_first_post_uploads_now_and_cleans_up(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811
    from app.modules.publishing import dispatcher

    yt = FakeYouTube(respx)
    acc, src, clips = make_world(db, user, [20.0, 100.0])
    assert _schedule(client, user, acc, src).status_code == 201
    first, second = _items(db, acc)
    run_publish_jobs(tasks, limit=1)
    assert yt.titles == ["Clip at 20"]
    db.expire_all()
    assert db.get(GeneratedClip, clips[0].id).upload_status == "uploaded" and not clip_files_exist(clips[0])
    assert db.get(PublishingQueueItem, second.id).status == QueueStatus.SCHEDULED  # waits for its hour
    assert second.id not in {i.id for i in dispatcher.due_candidates(db, utcnow(), limit=1000)}
    assert clip_files_exist(clips[1])


def test_missed_slots_move_together_keeping_gaps(client, db, user, tasks) -> None:  # noqa: F811
    from app.modules.scheduling.timeline import shift_missed

    acc, src, _clips = make_world(db, user, [20.0, 100.0, 200.0])
    assert _schedule(client, user, acc, src, start_at=(utcnow() + HOUR).isoformat()).status_code == 201
    now = utcnow()
    for i, item in enumerate(_items(db, acc)):  # ClipFusion was off: the first two slots passed
        item.scheduled_at = now - 2 * HOUR + i * HOUR
    db.commit()
    assert shift_missed(db, now=now) == 3
    times = [ensure_utc(i.scheduled_at) for i in _items(db, acc)]
    assert times == [now, now + HOUR, now + 2 * HOUR]
    assert db.scalar(select(Notification).where(Notification.user_id == user.id,
                                                Notification.title == "Scheduled uploads moved")) is not None
    assert shift_missed(db, now=now + timedelta(minutes=5)) == 0  # on time now: nothing moves


def test_auto_schedule_is_on_by_default_for_a_rendered_video(client, db, user, tasks, monkeypatch) -> None:  # noqa: F811
    from app.core.config import get_settings
    from app.modules.scheduling.timeline import auto_schedule_source

    monkeypatch.setattr(get_settings(), "auto_schedule_default", True)  # the app default (off in the test env)
    from app.workers.jobs import create_job
    from app.workers.tasks_clips import _auto_schedule

    acc, src, clips = make_world(db, user, [200.0, 100.0, 20.0])
    h = headers(user)
    cfg = client.get(f"{API}/publishing/auto-schedule", headers=h).json()
    assert cfg["enabled"] is True and cfg["gap_minutes"] == 60 and cfg["connected_account_id"] == str(acc.id)

    # while another clip of the video is still rendering nothing is scheduled ...
    busy, _ = create_job(db, user_id=user.id, job_type=JobType.RENDER, video_source_id=src.id, clip_id=clips[0].id,
                         idempotency_key=f"render:{clips[0].id}:test")
    _auto_schedule(db, src.id, exclude_job=None)
    assert _items(db, acc) == []
    # ... once the video's last render is done, its clips are scheduled in timeline order, the first right away
    _auto_schedule(db, src.id, exclude_job=busy.id)
    items = _items(db, acc)
    assert [_clip_title(db, i) for i in items] == ["Clip at 20", "Clip at 100", "Clip at 200"]
    times = [ensure_utc(i.scheduled_at) for i in items]
    assert times[0] <= utcnow() and times[1] - times[0] == HOUR and times[2] - times[1] == HOUR
    assert items[0].status == QueueStatus.PUBLISHING and all(i.origin == "timeline" for i in items)
    assert auto_schedule_source(db, src.id) == []  # never scheduled twice

    # a clip the user unscheduled is not put back
    client.post(f"{API}/publishing/queue/{items[2].id}/cancel", headers=h)
    db.expire_all()
    assert auto_schedule_source(db, src.id) == []
    assert db.get(PublishingQueueItem, items[2].id).status == QueueStatus.CANCELLED

    # videos added without confirming rights are never auto-uploaded
    _acc2, src2, _c2 = make_world(db, user, [5.0])
    src2.rights_confirmed = False
    db.commit()
    assert auto_schedule_source(db, src2.id) == []

    # switched off: nothing is scheduled; switched on again: only clips created from then on
    client.put(f"{API}/publishing/auto-schedule", json={"enabled": False, "gap_minutes": 60}, headers=h)
    db.expire_all()
    _acc3, src3, _c3 = make_world(db, user, [5.0])
    assert auto_schedule_source(db, src3.id) == []
    on = client.put(f"{API}/publishing/auto-schedule", headers=h,
                    json={"enabled": True, "connected_account_id": str(acc.id), "gap_minutes": 60}).json()
    assert on["enabled"] is True and on["since"]
    db.expire_all()
    assert auto_schedule_source(db, src3.id) == []  # created before it was switched on again
    _acc4, src4, _c4 = make_world(db, user, [5.0, 50.0])
    assert len(auto_schedule_source(db, src4.id)) == 2


def test_upload_now_of_a_scheduled_clip(client, db, user, tasks) -> None:  # noqa: F811
    acc, src, clips = make_world(db, user, [20.0, 100.0])
    assert _schedule(client, user, acc, src, start_at=(utcnow() + HOUR).isoformat()).status_code == 201
    r = client.post(f"{API}/upload-runs", headers=headers(user), json={
        "video_source_id": str(src.id), "connected_account_id": str(acc.id), "mode": "single",
        "clip_id": str(clips[1].id), "rights_confirmed": True})
    assert r.status_code == 201, r.text
    db.expire_all()
    item = db.scalar(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clips[1].id))
    assert item.origin is None and item.status == QueueStatus.PUBLISHING


def test_free_time_is_used_before_posts_scheduled_later(client, db, user, tasks) -> None:  # noqa: F811
    acc, src, _clips = make_world(db, user, [10.0, 60.0])
    _acc2, src2, _c2 = make_world(db, user, [10.0, 60.0, 120.0])
    tomorrow = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    # another video is already scheduled for tomorrow on the same channel
    assert _schedule(client, user, acc, src2, start_at=tomorrow.isoformat()).status_code == 201
    before = utcnow()
    assert _schedule(client, user, acc, src).status_code == 201  # today's video still goes right now
    times = sorted(ensure_utc(i.scheduled_at) for i in _items(db, acc))
    assert before - timedelta(seconds=1) <= times[0] <= utcnow() and times[1] - times[0] == HOUR
    assert times[2:] == [tomorrow, tomorrow + HOUR, tomorrow + 2 * HOUR]
    # a plan never lands within an hour of a scheduled post: here it has to jump past tomorrow's three
    nxt = client.get(f"{API}/publishing/timeline/next", headers=headers(user),
                     params={"account_id": str(acc.id), "gap_minutes": 60, "count": 3}).json()
    slots = [ensure_utc(datetime.fromisoformat(x)) for x in nxt["slots"]]
    assert slots[0] == times[1] + HOUR and slots[1] - slots[0] == HOUR
    assert all(abs(s - t) >= HOUR for s in slots for t in times)
