"""Timeline scheduling: clips scheduled one by one a gap apart (the first right away), auto-schedule of a
video's clips once they are rendered, missed slots."""

from __future__ import annotations

from datetime import datetime, timedelta

import respx
from sqlalchemy import select

from app.core.database import utcnow
from app.models import GeneratedClip, Notification, PublishingQueueItem
from app.models.enums import QueueStatus
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


def test_each_rendered_clip_uploads_now_or_one_hour_after_the_previous(client, db, user, tasks, monkeypatch) -> None:  # noqa: F811,E501
    from app.core.config import get_settings
    from app.modules.scheduling.timeline import schedule_rendered_clip

    monkeypatch.setattr(get_settings(), "auto_schedule_default", True)  # the app default (off in the test env)
    acc, src, clips = make_world(db, user, [200.0, 100.0, 20.0])
    h = headers(user)
    cfg = client.get(f"{API}/publishing/auto-schedule", headers=h).json()
    assert cfg["enabled"] is True and cfg["gap_minutes"] == 60 and cfg["connected_account_id"] == str(acc.id)

    # the user renders clips one by one, in any order: each joins the upload line in render order
    before = utcnow()
    first = schedule_rendered_clip(db, clips[1])  # the clip at 100 s, rendered first: uploads right away
    second = schedule_rendered_clip(db, clips[2])  # the clip at 20 s: one hour later
    third = schedule_rendered_clip(db, clips[0])  # the clip at 200 s: one hour after that
    db.expire_all()
    t1, t2, t3 = (ensure_utc(db.get(PublishingQueueItem, i.id).scheduled_at) for i in (first, second, third))
    assert before - timedelta(seconds=1) <= t1 <= utcnow() and t2 - t1 == HOUR and t3 - t2 == HOUR
    assert db.get(PublishingQueueItem, first.id).status == QueueStatus.PUBLISHING  # started immediately
    assert db.get(PublishingQueueItem, second.id).status == QueueStatus.SCHEDULED
    assert all(db.get(PublishingQueueItem, i.id).origin == "timeline" for i in (first, second, third))
    assert schedule_rendered_clip(db, db.get(GeneratedClip, clips[1].id)) is None  # a re-render: never twice

    # a clip the user unscheduled is not put back by rendering it again
    client.post(f"{API}/publishing/queue/{third.id}/cancel", headers=h)
    db.expire_all()
    assert schedule_rendered_clip(db, db.get(GeneratedClip, clips[0].id)) is None

    # videos added without confirming rights are never uploaded automatically
    _a2, src2, c2 = make_world(db, user, [5.0])
    src2.rights_confirmed = False
    db.commit()
    assert schedule_rendered_clip(db, c2[0]) is None

    # switched off: rendering only renders
    client.put(f"{API}/publishing/auto-schedule", json={"enabled": False, "gap_minutes": 60}, headers=h)
    db.expire_all()
    _a3, _s3, c3 = make_world(db, user, [5.0])
    assert schedule_rendered_clip(db, c3[0]) is None


def test_a_clip_rendered_soon_after_an_upload_waits_an_hour_after_it(client, db, user, tasks, monkeypatch) -> None:  # noqa: F811,E501
    from app.core.config import get_settings
    from app.modules.scheduling.timeline import schedule_rendered_clip

    monkeypatch.setattr(get_settings(), "auto_schedule_default", True)
    _acc, _src, clips = make_world(db, user, [20.0, 100.0, 200.0])
    done = schedule_rendered_clip(db, clips[0])
    uploaded_at = utcnow() - timedelta(minutes=20)
    done.status, done.published_at, done.scheduled_at = QueueStatus.PUBLISHED, uploaded_at, uploaded_at
    db.commit()
    nxt = schedule_rendered_clip(db, clips[1])  # rendered 20 min after the previous upload
    assert ensure_utc(nxt.scheduled_at) == uploaded_at + HOUR
    db.expire_all()
    later = db.get(PublishingQueueItem, nxt.id)
    later.status = QueueStatus.PUBLISHED  # pretend both went out long ago
    later.published_at = later.scheduled_at = utcnow() - 3 * HOUR
    db.get(PublishingQueueItem, done.id).published_at = utcnow() - 4 * HOUR
    db.commit()
    before = utcnow()
    assert before - timedelta(seconds=1) <= ensure_utc(schedule_rendered_clip(db, clips[2]).scheduled_at) <= utcnow()


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
