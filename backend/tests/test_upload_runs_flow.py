"""Sequential YouTube uploads + verified cleanup (API v2), end to end through the publishing pipeline
with a fake YouTube on respx: order, one-at-a-time, stop-on-failure, retry, cancel, restart safety,
cleanup only after verification, Remove Clip, auto-delete-original."""

from __future__ import annotations

import uuid

import respx
from sqlalchemy import func, select

from app.core.storage import get_storage
from app.models import GeneratedClip, ProcessingJob, PublishingQueueItem, UploadRun, VideoSource
from app.models.enums import JobStatus, QueueStatus
from app.modules.storage.service import clip_prefix, prefix_size
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


def _start(client, user, acc, src, mode: str, clip_id=None, privacy: str = "unlisted"):  # noqa: ANN001, ANN202
    body = {"video_source_id": str(src.id), "connected_account_id": str(acc.id), "mode": mode,
            "privacy": privacy, "rights_confirmed": True}
    if clip_id is not None:
        body["clip_id"] = str(clip_id)
    return client.post(f"{API}/upload-runs", json=body, headers=headers(user))


def _publishing(db, acc) -> int:  # noqa: ANN001
    """Transfers in progress on the account (other tests' rows share the database)."""
    return db.scalar(select(func.count(PublishingQueueItem.id)).where(
        PublishingQueueItem.connected_account_id == acc.id, PublishingQueueItem.status == QueueStatus.PUBLISHING))


def test_sequence_is_chronological_regardless_of_completion_order(client, db, user, tasks) -> None:  # noqa: F811
    _acc, src, clips = make_world(db, user, [300.0, 10.0, 150.0, 40.0, 90.0])
    # finish processing in a different order than both creation and time order
    for c in (clips[2], clips[0], clips[4]):
        c.render_hash = "stale"
    db.commit()
    page = client.get(f"{API}/clips", params={"video_source_id": str(src.id)}, headers=headers(user)).json()
    assert [i["start_time"] for i in page["items"]] == [10.0, 40.0, 90.0, 150.0, 300.0]
    assert [i["sequence"] for i in page["items"]] == [1, 2, 3, 4, 5]
    assert {i["start_time"]: i["status"] for i in page["items"]} == {
        10.0: "generated", 40.0: "generated", 90.0: "pending", 150.0: "pending", 300.0: "pending"}
    assert page["items"][0]["social_metadata"]["youtube"]["title"] == "Clip at 10"
    r = client.delete(f"{API}/clips/{clips[3].id}", headers=headers(user))  # the clip at 40 s
    assert r.status_code == 200
    page = client.get(f"{API}/clips", params={"video_source_id": str(src.id)}, headers=headers(user)).json()
    assert [(i["start_time"], i["sequence"]) for i in page["items"]] == [(10.0, 1), (90.0, 2), (150.0, 3), (300.0, 4)]


@respx.mock
def test_test7_upload_clip_1_deletes_only_its_media(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811
    yt = FakeYouTube(respx)
    acc, src, clips = make_world(db, user)
    first = clips[0]
    r = _start(client, user, acc, src, "single", first.id)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "running" and run["total"] == 1 and run["items"][0]["status"] == "uploading"
    assert client.get(f"{API}/clips/{first.id}", headers=headers(user)).json()["status"] == "uploading"
    assert run_publish_jobs(tasks) == 1
    db.expire_all()
    clip = db.get(GeneratedClip, first.id)
    vid = yt.uploads[0][1]
    assert yt.titles == ["Clip at 10"] and "Credit: Some Creator" in yt.descriptions["Clip at 10"]
    assert clip.upload_status == "uploaded" and clip.youtube_video_id == vid
    assert clip.youtube_url == f"https://www.youtube.com/watch?v={vid}" and clip.uploaded_at is not None
    assert clip.cleanup_status == "cleaned" and clip.cleaned_at is not None and clip.freed_bytes > 100_000
    assert not clip_files_exist(clip) and prefix_size(clip_prefix(user.id, clip.id)) == 0
    assert clip.title == "Clip at 10" and clip.social_metadata["youtube"]["hashtags"] == ["shorts"]
    assert all(clip_files_exist(c) for c in clips[1:])
    assert get_storage().exists(src.storage_key)
    out = client.get(f"{API}/clips/{clip.id}", headers=headers(user)).json()
    assert out["status"] == "cleaned" and out["media_available"] is False and out["youtube_url"] == clip.youtube_url
    assert out["thumbnail_url"] == f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    assert out["queue_items"][0]["status"] in ("processing_remote", "published")
    run = client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()
    assert run["status"] == "completed" and run["uploaded"] == 1 and run["items"][0]["youtube_url"] == clip.youtube_url
    # cleanup is idempotent
    from app.modules.storage.cleanup import cleanup_after_upload

    assert cleanup_after_upload(db, clip) == "already" and db.get(GeneratedClip, clip.id).freed_bytes == clip.freed_bytes


@respx.mock
def test_remaining_uploads_in_order_one_at_a_time(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811
    yt = FakeYouTube(respx)
    acc, src, clips = make_world(db, user, [200.0, 20.0, 100.0])
    r = _start(client, user, acc, src, "remaining")
    assert r.status_code == 201, r.text
    run = r.json()
    assert [i["title"] for i in run["items"]] == ["Clip at 20", "Clip at 100", "Clip at 200"]
    assert [i["sequence"] for i in run["items"]] == [1, 2, 3]
    assert [i["status"] for i in run["items"]] == ["uploading", "pending", "pending"]
    assert sum(1 for name, _ in tasks if name == "publish_queue_item") == 1 and _publishing(db, acc) == 1
    # a second run on the same account is refused while this one runs
    again = _start(client, user, acc, src, "next")
    assert again.status_code == 409 and again.json()["error"]["code"] == "upload_in_progress"
    steps = []
    while any(name == "publish_queue_item" for name, _ in tasks):
        assert _publishing(db, acc) <= 1
        run_publish_jobs(tasks, limit=1)
        steps.append(client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()["uploaded"])
    assert yt.titles == ["Clip at 20", "Clip at 100", "Clip at 200"] and yt.max_active == 1
    assert steps == [1, 2, 3]
    run = client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()
    assert run["status"] == "completed" and run["remaining"] == 0 and run["current_clip_id"] is None
    db.expire_all()
    assert all(db.get(GeneratedClip, c.id).cleanup_status == "cleaned" for c in clips)
    assert get_storage().exists(src.storage_key)  # auto_delete_original is off by default
    assert _start(client, user, acc, src, "next").json()["error"]["code"] == "nothing_to_upload"
    listed = client.get(f"{API}/upload-runs", params={"video_source_id": str(src.id)}, headers=headers(user)).json()
    assert [x["id"] for x in listed] == [run["id"]]
    assert client.get(f"{API}/upload-runs", params={"active": True}, headers=headers(user)).json() == []


@respx.mock
def test_test9_failure_stops_the_run_keeps_files_and_retry_resumes(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811,E501
    yt = FakeYouTube(respx)
    yt.fail_titles = {"Clip at 60"}
    acc, src, clips = make_world(db, user, [10.0, 60.0, 120.0])
    run = _start(client, user, acc, src, "remaining").json()
    run_publish_jobs(tasks)
    run = client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()
    assert run["status"] == "failed" and run["failed_clip_id"] == str(clips[1].id) and run["last_error"]
    assert [i["status"] for i in run["items"]] == ["uploaded", "failed", "pending"]  # never skipped
    assert yt.titles == ["Clip at 10"]
    db.expire_all()
    failed = db.get(GeneratedClip, clips[1].id)
    assert failed.upload_status == "failed" and failed.upload_error and failed.cleanup_status == "none"
    assert clip_files_exist(failed) and clip_files_exist(clips[2]) and not clip_files_exist(clips[0])
    out = client.get(f"{API}/clips/{failed.id}", headers=headers(user)).json()
    assert out["status"] == "upload_failed" and out["media_available"] is True
    # Retry resumes from the failed clip, then continues in order
    yt.fail_titles = set()
    r = client.post(f"{API}/upload-runs/{run['id']}/retry", headers=headers(user))
    assert r.status_code == 200 and r.json()["status"] == "running"
    assert [i["status"] for i in r.json()["items"]] == ["uploaded", "uploading", "pending"]
    run_publish_jobs(tasks)
    run = client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()
    assert run["status"] == "completed" and run["uploaded"] == 3
    assert yt.titles == ["Clip at 10", "Clip at 60", "Clip at 120"]


@respx.mock
def test_cancel_stops_after_the_current_transfer(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811
    yt = FakeYouTube(respx)
    acc, src, _clips = make_world(db, user, [10.0, 60.0, 120.0])
    run = _start(client, user, acc, src, "remaining").json()
    r = client.post(f"{API}/upload-runs/{run['id']}/cancel", headers=headers(user)).json()
    assert r["status"] == "running" and r["cancel_requested"] is True  # the first clip is transferring
    run_publish_jobs(tasks)
    run = client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()
    assert run["status"] == "cancelled" and [i["status"] for i in run["items"]] == ["uploaded", "cancelled",
                                                                                    "cancelled"]
    assert yt.titles == ["Clip at 10"]


@respx.mock
def test_restart_mid_upload_resumes_without_a_second_upload(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811
    from app.workers.tasks_publishing import advance_upload_runs

    yt = FakeYouTube(respx)
    acc, src, clips = make_world(db, user, [10.0, 60.0])
    run = _start(client, user, acc, src, "remaining").json()
    item = db.scalar(select(PublishingQueueItem).where(PublishingQueueItem.clip_id == clips[0].id))
    assert item.status == QueueStatus.PUBLISHING
    # The transfer finished on YouTube's side but the worker died before recording it.
    yt.sessions["s99"] = "Clip at 10"
    yt.completed["s99"] = "vCRASHED001"
    yt.uploads.append(("Clip at 10", "vCRASHED001"))
    item.upload_session_url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=s99"
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.queue_item_id == item.id))
    job.status, job.idempotency_key = JobStatus.FAILED, None  # what the stale-job reaper does
    db.commit()
    tasks.clear()
    assert advance_upload_runs() >= 1  # periodic reconciliation resumes the interrupted transfer
    ours = {str(j) for j in db.scalars(select(ProcessingJob.id).join(
        PublishingQueueItem, PublishingQueueItem.id == ProcessingJob.queue_item_id).where(
        PublishingQueueItem.connected_account_id == acc.id))}
    tasks[:] = [t for t in tasks if t[0] == "publish_queue_item" and t[1]["job_id"] in ours]
    assert len(tasks) == 1
    run_publish_jobs(tasks)
    db.expire_all()
    assert yt.titles == ["Clip at 10", "Clip at 60"]  # clip 1 was not uploaded twice
    assert db.get(GeneratedClip, clips[0].id).youtube_video_id == "vCRASHED001"
    assert db.get(UploadRun, uuid.UUID(run["id"])).status == "completed"


@respx.mock
def test_cleanup_only_after_verified_upload(client, db, user, tasks, fast_youtube, monkeypatch) -> None:  # noqa: F811
    from app.core.config import get_settings
    from app.workers.tasks_maintenance import verify_uploads

    monkeypatch.setattr(get_settings(), "upload_verify_interval_seconds", 0)
    yt = FakeYouTube(respx)
    yt.title_status = {"Clip at 10": "timeout", "Clip at 60": "uploaded", "Clip at 120": "missing",
                       "Clip at 180": "rejected"}
    acc, src, clips = make_world(db, user, [10.0, 60.0, 120.0, 180.0])
    run = _start(client, user, acc, src, "remaining").json()
    run_publish_jobs(tasks)
    db.expire_all()
    rows = {c.title: db.get(GeneratedClip, c.id) for c in clips}
    for title in ("Clip at 10", "Clip at 60", "Clip at 120"):  # uncertain / still processing / not listed
        assert rows[title].upload_status == "uploaded" and rows[title].cleanup_status == "none", title
        assert clip_files_exist(rows[title]) and rows[title].cleanup_error, title
    rejected = rows["Clip at 180"]
    assert rejected.upload_status == "failed" and "rejected" in rejected.upload_error and clip_files_exist(rejected)
    run = client.get(f"{API}/upload-runs/{run['id']}", headers=headers(user)).json()
    assert run["status"] == "failed" and run["failed_clip_id"] == str(rejected.id)
    # later: YouTube confirms two of them -> only those are cleaned by the periodic pass
    for title, vid in yt.uploads:
        if title in ("Clip at 10", "Clip at 60"):
            yt.list_status[vid] = "processed"
    stats = verify_uploads(db)
    db.expire_all()
    assert stats.get("cleaned", 0) >= 2
    assert not clip_files_exist(db.get(GeneratedClip, clips[0].id))
    assert not clip_files_exist(db.get(GeneratedClip, clips[1].id))
    assert clip_files_exist(db.get(GeneratedClip, clips[2].id)) and clip_files_exist(db.get(GeneratedClip, clips[3].id))
    assert db.get(GeneratedClip, clips[3].id).upload_status == "failed"  # never cleaned
    assert get_storage().exists(src.storage_key)


def test_test8_remove_clip_3_deletes_only_its_files(client, db, user, tasks) -> None:  # noqa: F811
    from app.modules.sources.clip_factory import create_clips
    from app.schemas.media import HighlightCandidate

    _acc, src, clips = make_world(db, user)
    third = clips[2]
    before = prefix_size(clip_prefix(user.id, third.id))
    r = client.delete(f"{API}/clips/{third.id}", headers=headers(user))
    assert r.status_code == 200 and r.json() == {"id": str(third.id), "status": "removed", "freed_bytes": before}
    db.expire_all()
    row = db.get(GeneratedClip, third.id)
    assert row is not None and row.removed_at is not None and row.title == "Clip at 120" and row.freed_bytes == before
    assert not clip_files_exist(row)
    assert all(clip_files_exist(c) for i, c in enumerate(clips) if i != 2)
    assert get_storage().exists(src.storage_key) and get_storage().exists(src.thumbnail_key)
    # idempotent; re-analysis never recreates the removed clip
    assert client.delete(f"{API}/clips/{third.id}", headers=headers(user)).json()["status"] == "removed"
    cand = HighlightCandidate(start=120.0, end=150.0, score=90.0, category="other", reasons=["x"], method="signals",
                              transcript="words spoken at 120", target_duration=30.0)
    assert create_clips(db, db.get(VideoSource, src.id), [cand], use_llm=False) == []
    # removing is refused while the clip uploads
    other = db.get(GeneratedClip, clips[1].id)
    other.upload_status = "uploading"
    db.commit()
    r = client.delete(f"{API}/clips/{other.id}", headers=headers(user))
    assert r.status_code == 409 and r.json()["error"]["code"] == "clip_uploading"


@respx.mock
def test_auto_delete_original_setting(client, db, user, tasks, fast_youtube) -> None:  # noqa: F811
    FakeYouTube(respx)
    acc, src, clips = make_world(db, user, [10.0, 60.0])
    client.delete(f"{API}/clips/{clips[1].id}", headers=headers(user))
    _start(client, user, acc, src, "next")
    run_publish_jobs(tasks)
    db.expire_all()
    assert get_storage().exists(src.storage_key)  # setting off (default): the original stays

    acc2, src2, clips2 = make_world(db, user, [10.0, 60.0])
    r = client.patch(f"{API}/users/me", json={"settings": {"auto_delete_original": True}}, headers=headers(user))
    assert r.status_code == 200 and r.json()["settings"]["auto_delete_original"] is True
    key = src2.storage_key
    _start(client, user, acc2, src2, "single", clips2[0].id)
    run_publish_jobs(tasks)
    db.expire_all()
    assert get_storage().exists(key)  # clip 2 is neither uploaded nor removed yet
    client.delete(f"{API}/clips/{clips2[1].id}", headers=headers(user))
    db.expire_all()
    fresh = db.get(VideoSource, src2.id)
    assert fresh.storage_key is None and fresh.original_deleted_at is not None and not get_storage().exists(key)
    out = client.get(f"{API}/sources/{src2.id}", headers=headers(user)).json()
    assert out["original_available"] is False and out["original_bytes"] == 0
    assert db.get(GeneratedClip, clips2[0].id).youtube_url  # links stay
