"""API v2 inputs and pipeline status: GET /sources/{id}/pipeline (real stage records only),
attach-link (direct links only, automatic processing), user settings defaults."""

from __future__ import annotations

from datetime import timedelta

from app.core.database import utcnow
from app.models import ProcessingJob, VideoSource
from app.models.enums import JobStatus, JobType
from tests.test_api_core_helpers import small_video  # noqa: F401 - fixture
from tests.test_upload_runs_factories import API, headers, make_world, tasks  # noqa: F401 - fixture


def _stages(body: dict) -> dict:
    return {s["key"]: s for s in body["stages"]}


def test_pipeline_idle_running_completed(client, db, user, tasks) -> None:  # noqa: F811
    h = headers(user)
    src = VideoSource(user_id=user.id, source_type="youtube", status="metadata_only", title="Link only")
    db.add(src)
    db.commit()
    body = client.get(f"{API}/sources/{src.id}/pipeline", headers=h).json()
    assert body["status"] == "idle" and body["eta_seconds"] is None
    assert [s["key"] for s in body["stages"]] == ["ingestion", "audio", "transcription", "video_analysis",
                                                  "highlights", "clip_generation", "metadata", "completed"]
    assert _stages(body)["ingestion"]["status"] == "pending" and "upload" in _stages(body)["ingestion"]["message"]

    _acc, ready, clips = make_world(db, user, [10.0, 60.0])
    now = utcnow()
    job = ProcessingJob(user_id=user.id, video_source_id=ready.id, job_type=JobType.ANALYSIS,
                        status=JobStatus.RUNNING, stage="transcription", progress=0.4, max_attempts=3,
                        started_at=now - timedelta(seconds=30), created_at=now - timedelta(seconds=31),
                        params={"options": {"mode": "fast"}},
                        stages={"audio": {"status": "done", "started_at": (now - timedelta(seconds=30)).isoformat(),
                                          "finished_at": (now - timedelta(seconds=20)).isoformat(), "seconds": 10.0},
                                "transcription": {"status": "running", "progress": 0.5,
                                                  "started_at": (now - timedelta(seconds=20)).isoformat()},
                                "video_analysis": {"status": "running", "progress": None,
                                                   "started_at": (now - timedelta(seconds=20)).isoformat()},
                                "_device": {"transcription": "cpu", "model": "small"}})
    db.add(job)
    db.commit()
    body = client.get(f"{API}/sources/{ready.id}/pipeline", headers=h).json()
    st = _stages(body)
    assert body["status"] == "running" and body["mode"] == "fast" and body["device"] == {"transcription": "cpu",
                                                                                         "model": "small"}
    assert st["ingestion"]["status"] == "done" and st["audio"]["seconds"] == 10.0
    assert st["transcription"]["status"] == "running" and st["transcription"]["progress"] == 0.5
    assert st["video_analysis"]["progress"] is None and st["highlights"]["status"] == "pending"
    assert 15 <= body["eta_seconds"] <= 25  # 20 s for the first half of the running stage
    assert body["elapsed_seconds"] >= 29

    job.status, job.finished_at = JobStatus.SUCCEEDED, utcnow()
    job.stages = {**job.stages, "transcription": {**job.stages["transcription"], "status": "done",
                                                  "finished_at": utcnow().isoformat(), "seconds": 20.0},
                  "video_analysis": {**job.stages["video_analysis"], "status": "done",
                                     "finished_at": utcnow().isoformat()},
                  "highlights": {"status": "done"}, "metadata": {"status": "done"}}
    db.commit()
    body = client.get(f"{API}/sources/{ready.id}/pipeline", headers=h).json()
    st = _stages(body)
    assert body["status"] == "completed" and body["eta_seconds"] is None
    assert st["clip_generation"]["status"] == "done" and st["clip_generation"]["progress"] == 1.0
    assert st["completed"]["status"] == "done"
    assert body["clips"]["total"] == 2 and body["clips"]["generated"] == 2
    vision = ProcessingJob(user_id=user.id, video_source_id=ready.id, clip_id=clips[0].id,
                           job_type=JobType.CLIP_VISION, status=JobStatus.RUNNING, stage="faces", progress=0.3,
                           max_attempts=3, started_at=utcnow())
    db.add(vision)
    db.commit()
    body = client.get(f"{API}/sources/{ready.id}/pipeline", headers=h).json()
    st = _stages(body)
    assert body["status"] == "running" and st["clip_generation"]["status"] == "running"
    assert st["clip_generation"]["progress"] == 0.5 and st["clip_generation"]["message"] == "1/2 clips finished"

    job.status, job.error = JobStatus.FAILED, "boom"
    vision.status = JobStatus.FAILED
    ready.error = "FFmpeg could not decode the video"
    job.stages = {**job.stages, "highlights": {"status": "running"}}
    db.commit()
    body = client.get(f"{API}/sources/{ready.id}/pipeline", headers=h).json()
    assert body["status"] == "failed" and _stages(body)["highlights"]["status"] == "failed"
    assert _stages(body)["highlights"]["error"] == "FFmpeg could not decode the video"


def test_attach_link_and_automatic_processing(client, db, user, monkeypatch, tasks, small_video) -> None:  # noqa: F811
    import shutil

    from app.workers import tasks_processing

    h = headers(user)
    src = VideoSource(user_id=user.id, source_type="youtube", status="metadata_only", title="Creator video",
                      youtube_video_id="dQw4w9WgXcQ", attribution="Credit: Creator — https://youtu.be/dQw4w9WgXcQ")
    db.add(src)
    db.commit()
    body = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "rights_confirmed": True}
    r = client.post(f"{API}/sources/{src.id}/attach-link", json=body, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "platform_url_refused"  # never from YouTube
    r = client.post(f"{API}/sources/{src.id}/attach-link", json={"url": "https://files.example.com/v.mp4"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "rights_not_confirmed"
    monkeypatch.setattr("app.integrations.cloud_import.validate_import_url", lambda url: None)
    r = client.post(f"{API}/sources/{src.id}/attach-link", headers=h,
                    json={"url": "https://files.example.com/v.mp4", "rights_confirmed": True,
                          "analysis": {"mode": "fast", "min_duration": 10, "max_duration": 30, "max_clips": 3}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "importing" and out["active_job"]["job_type"] == "import"
    assert tasks[-1][0] == "import_source"

    def fake_download(url, work, *, max_bytes, progress):  # noqa: ANN001, ANN202
        dest = work / "v.mp4"
        shutil.copyfile(small_video, dest)
        return dest, "v.mp4", "video/mp4"

    monkeypatch.setattr("app.integrations.cloud_import.download_url", fake_download)
    tasks_processing.import_source(job_id=out["active_job"]["id"])
    assert tasks[-1][0] == "analyze_source"  # processing started automatically
    db.expire_all()
    fresh = db.get(VideoSource, src.id)
    assert fresh.storage_key and fresh.status == "queued" and fresh.processing_mode == "fast"
    assert fresh.analysis_options["min_duration"] == 10 and fresh.analysis_options["max_clips"] == 3
    assert fresh.attribution == "Credit: Creator — https://youtu.be/dQw4w9WgXcQ"
    r = client.post(f"{API}/sources/{src.id}/attach-link", headers=h,
                    json={"url": "https://files.example.com/v2.mp4", "rights_confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "file_already_attached"
    r = client.patch(f"{API}/sources/{src.id}", json={"attribution": "Credit: someone else"}, headers=h)
    assert r.json()["attribution"] == "Credit: someone else"


def test_analysis_request_defaults_from_settings(client, db, user, tasks) -> None:  # noqa: F811
    _acc, src, _clips = make_world(db, user, [10.0])
    h = headers(user)
    r = client.patch(f"{API}/users/me", headers=h, json={"settings": {
        "default_processing_mode": "best", "default_min_duration": 30, "default_max_duration": 90,
        "default_max_clips": 7, "default_privacy": "unlisted"}})
    assert r.status_code == 200, r.text
    bad = client.patch(f"{API}/users/me", headers=h, json={"settings": {"default_max_duration": 10}})
    assert bad.status_code == 422
    r = client.post(f"{API}/sources/{src.id}/analyze", json={"rights_confirmed": True}, headers=h)
    assert r.status_code == 200, r.text
    db.expire_all()
    opts = db.get(VideoSource, src.id).analysis_options
    assert (opts["mode"], opts["min_duration"], opts["max_duration"], opts["max_clips"]) == ("best", 30, 90, 7)
    assert all(30 <= d <= 90 for d in opts["clip_durations"])
    r = client.post(f"{API}/sources/{src.id}/analyze", headers=h,
                    json={"rights_confirmed": True, "min_duration": 50, "max_duration": 20})
    assert r.status_code == 422
