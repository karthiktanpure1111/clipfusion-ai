"""import_source and analyze_source tasks (real media; downloads and clip_vision dispatch stubbed)."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.storage import get_storage, source_key
from app.models import GeneratedClip, Notification, ProcessingJob, Transcript, VideoAnalysis, VideoSource
from app.models.enums import JobStatus, JobType, NotificationType, SourceStatus, SourceType
from app.workers import tasks_processing
from app.workers.jobs import create_job
from tests.helpers.media import make_speech_video
from tests.test_api_core_helpers import dispatched, make_source, small_video  # noqa: F401 - fixtures

SPEECH = (
    "Welcome back to the stream everyone. Today I want to tell you the craziest thing that happened to me. "
    "I was walking my dog in the park when a squirrel jumped on my head. No way, right? "
    "Everyone around me started laughing so hard. Haha, it was hilarious! "
    "So here is the lesson. Always look up when you walk under big trees. "
    "Thank you for watching, and let me know in the chat what you think."
)


def _job(db, source: VideoSource, job_type: str, params: dict, **kw) -> ProcessingJob:
    job, _ = create_job(db, user_id=source.user_id, job_type=job_type, video_source_id=source.id, params=params,
                        idempotency_key=f"{job_type}:{source.id}", **kw)
    db.commit()
    return job


def _notes(db, user_id: uuid.UUID) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.user_id == user_id)))


def _importing(db, user) -> VideoSource:
    return make_source(db, user, status=SourceStatus.IMPORTING, title="", source_type=SourceType.CLOUD, rights=True)


def test_import_source_downloads_validates_and_stores(db, user, monkeypatch, small_video: Path, dispatched) -> None:  # noqa: F811
    def fake_download(url, dest_dir, *, max_bytes, progress=None):  # noqa: ANN001, ANN202
        assert url == "https://files.example.com/talk.mp4" and max_bytes == get_settings().max_upload_bytes
        dest = Path(dest_dir) / "dl.mp4"
        shutil.copyfile(small_video, dest)
        if progress:
            progress(1.0, "done")
        return dest, "Team Talk.mp4", "video/mp4"

    monkeypatch.setattr("app.integrations.cloud_import.download_url", fake_download)
    source = _importing(db, user)
    job = _job(db, source, JobType.IMPORT, {"provider": "url", "url": "https://files.example.com/talk.mp4"})
    tasks_processing.import_source(job_id=str(job.id))
    db.expire_all()
    assert db.get(ProcessingJob, job.id).status == JobStatus.SUCCEEDED
    src = db.get(VideoSource, source.id)
    assert src.status == SourceStatus.UPLOADED and src.title == "Team Talk" and src.checksum_sha256
    assert src.storage_key == source_key(user.id, src.id, ".mp4") and get_storage().exists(src.storage_key)
    assert abs(src.duration - 2.0) < 0.3 and src.media_info["width"] == 320
    assert any(n.type == NotificationType.SYSTEM for n in _notes(db, user.id))
    # importing the same file again -> permanent failure naming the existing video
    dup = _importing(db, user)
    job2 = _job(db, dup, JobType.IMPORT, {"provider": "url", "url": "https://files.example.com/talk.mp4"})
    tasks_processing.import_source(job_id=str(job2.id))
    db.expire_all()
    assert db.get(ProcessingJob, job2.id).status == JobStatus.FAILED
    assert db.get(VideoSource, dup.id).status == SourceStatus.FAILED and "already added" in db.get(VideoSource, dup.id).error


def test_import_source_rejects_non_video(db, user, monkeypatch, dispatched) -> None:  # noqa: F811
    def fake_download(url, dest_dir, *, max_bytes, progress=None):  # noqa: ANN001, ANN202
        dest = Path(dest_dir) / "x.mp4"
        dest.write_bytes(b"<html>not a video</html>" * 100)
        return dest, "x.mp4", "video/mp4"

    monkeypatch.setattr("app.integrations.cloud_import.download_url", fake_download)
    source = _importing(db, user)
    job = _job(db, source, JobType.IMPORT, {"provider": "url", "url": "https://files.example.com/x.mp4"})
    tasks_processing.import_source(job_id=str(job.id))
    db.expire_all()
    j = db.get(ProcessingJob, job.id)
    assert j.status == JobStatus.FAILED and j.attempts == 1 and dispatched == []  # permanent: no retry
    src = db.get(VideoSource, source.id)
    assert src.status == SourceStatus.FAILED and "Unsupported file type" in src.error
    assert any(n.type == NotificationType.PROCESSING_FAILED for n in _notes(db, user.id))


def test_analyze_failure_marks_source_failed(db, user, dispatched) -> None:  # noqa: F811
    source = make_source(db, user, rights=True)
    key = source_key(user.id, source.id, ".mp4")
    get_storage().put_bytes(b"\x00" * 2048, key)
    source.storage_key = key
    db.commit()
    job = _job(db, source, JobType.ANALYSIS, {"options": {"clip_durations": [15.0], "use_llm": False}},
               max_attempts=1)
    tasks_processing.analyze_source_task(job_id=str(job.id))
    db.expire_all()
    assert db.get(ProcessingJob, job.id).status == JobStatus.FAILED
    assert db.get(VideoSource, source.id).status == SourceStatus.FAILED
    assert any(n.type == NotificationType.PROCESSING_FAILED for n in _notes(db, user.id))


@pytest.mark.slow
def test_analyze_source_end_to_end(db, user, tmp_path: Path, monkeypatch, dispatched) -> None:  # noqa: F811
    video = make_speech_video(tmp_path / "talk.mp4", SPEECH, width=640, height=360)
    if video is None:
        pytest.skip("No text-to-speech engine available on this machine")
    monkeypatch.setattr(get_settings(), "whisper_model", "tiny")
    source = make_source(db, user, video=video, rights=True, title="Park story")
    opts = {"options": {"clip_durations": [15.0], "max_clips": 3, "use_llm": False, "whisper_model": "tiny",
                        "min_duration": 5.0, "max_duration": 60.0}}
    job = _job(db, source, JobType.ANALYSIS, opts)
    tasks_processing.analyze_source_task(job_id=str(job.id))
    db.expire_all()
    j = db.get(ProcessingJob, job.id)
    assert j.status == JobStatus.SUCCEEDED, j.error
    assert j.progress == 1.0 and j.result["clips_created"] >= 1
    src = db.get(VideoSource, source.id)
    assert src.status == SourceStatus.READY and src.processed_at is not None
    tr = db.scalar(select(Transcript).where(Transcript.video_source_id == source.id))
    assert tr.model == "tiny" and tr.word_count > 10 and ("squirrel" in tr.text.lower() or "park" in tr.text.lower())
    assert tr.segments and tr.segments[0]["words"]
    va = db.scalar(select(VideoAnalysis).where(VideoAnalysis.video_source_id == source.id))
    assert va.audio["rms_db"] and isinstance(va.scenes, list) and va.visual["motion"]
    assert isinstance(va.highlight_debug["warnings"], list)
    clips = list(db.scalars(select(GeneratedClip).where(GeneratedClip.video_source_id == source.id)))
    assert clips and all(c.detection_method == "signals" and c.social_metadata["source"] == "extractive"
                         and c.title and c.upload_status == "none" for c in clips)
    # per-stage pipeline records for GET /sources/{id}/pipeline
    for key in ("audio", "transcription", "video_analysis", "highlights", "metadata"):
        assert j.stages[key]["status"] in ("done", "skipped"), (key, j.stages[key])
    assert j.stages["transcription"]["status"] == "done" and j.stages["transcription"]["seconds"] is not None
    assert j.stages["_device"]["model"] == "tiny" and src.processing_mode == "balanced"
    vision_jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.video_source_id == source.id,
                                                              ProcessingJob.job_type == JobType.CLIP_VISION)))
    assert {vj.clip_id for vj in vision_jobs} == {c.id for c in clips}
    assert sorted(k["job_id"] for n, k in dispatched if n == "clip_vision") == sorted(str(v.id) for v in vision_jobs)
    types = {n.type: n for n in _notes(db, user.id)}
    assert types[NotificationType.PROCESSING_COMPLETE].link == f"/app/videos/{source.id}"
    assert NotificationType.CLIPS_AWAITING_APPROVAL not in types

    # Re-analysis reuses the stored transcript and does not duplicate overlapping clips.
    def no_transcribe(*_a, **_k):  # noqa: ANN202
        raise AssertionError("the existing transcript should be reused")

    monkeypatch.setattr("app.modules.transcription.service.transcribe", no_transcribe)
    job2 = _job(db, src, JobType.ANALYSIS, opts)
    tasks_processing.analyze_source_task(job_id=str(job2.id))
    db.expire_all()
    j2 = db.get(ProcessingJob, job2.id)
    assert j2.status == JobStatus.SUCCEEDED, j2.error
    assert j2.result["clips_created"] == 0 and j2.stages["transcription"]["status"] == "skipped"
    assert len(db.scalars(select(GeneratedClip).where(GeneratedClip.video_source_id == source.id)).all()) == len(clips)
