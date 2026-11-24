"""Clip workers end-to-end on real media: clip_vision (proxy, thumbnail, real face tracking,
framing, automatic render request), render_clip (real 1080x1920 export, no subtitles), render failure,
transcribe_clip."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.storage import get_storage
from app.models import ClipFramingSettings, GeneratedClip, Notification, ProcessingJob
from app.models.enums import JobType, RenderStatus
from app.modules.analysis.probe import probe_media
from app.modules.framing import service as framing_service
from app.modules.rendering.service import render_is_current, request_render
from app.schemas.framing import FramingConfig
from app.workers import tasks_clips
from app.workers.jobs import create_job
from tests.helpers.media import make_speech_video, make_test_video
from tests.helpers.vision_media import analysed, face_photo
from tests.test_api_clips_factories import (
    make_clip,
    make_source,
    make_transcript,
    record_enqueue,
    spoken,
)


@pytest.fixture()
def enqueued(monkeypatch) -> list[str]:
    return record_enqueue(monkeypatch)


@pytest.fixture(scope="module")
def media_dir(tmp_media_dir: Path) -> Path:
    d = tmp_media_dir / "a1b-tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module", autouse=True)
def _fonts() -> None:
    from app.modules.subtitles.fonts import ensure_builtin_fonts, font_catalogue

    if not any(f["available"] and f["source"] == "google" for f in font_catalogue()):
        ensure_builtin_fonts()


def _job(db, clip: GeneratedClip, job_type: str, **params) -> str:
    job, _ = create_job(db, user_id=clip.user_id, job_type=job_type, video_source_id=clip.video_source_id,
                        clip_id=clip.id, params=params, max_attempts=1)
    db.commit()
    return str(job.id)


def _status(db, job_id: str) -> ProcessingJob:
    db.expire_all()
    return db.get(ProcessingJob, uuid.UUID(job_id))


def test_clip_vision_end_to_end(db, user, enqueued, media_dir) -> None:
    if face_photo() is None:
        pytest.skip("test portrait unavailable (offline)")
    path, media, _ = analysed("podcast", media_dir)
    src = make_source(db, user, video=path, media=media, silences=[(0.0, 1.0)])
    make_transcript(db, src, spoken(1.0, 8.0))
    clip = make_clip(db, user, src, start=0.5, end=9.5)
    job_id = _job(db, clip, JobType.CLIP_VISION)
    tasks_clips.clip_vision_task(job_id)
    job = _status(db, job_id)
    assert job.status == "succeeded", job.error
    clip = db.get(GeneratedClip, clip.id)
    storage = get_storage()
    assert clip.proxy_start == 0.0 and storage.exists(clip.proxy_key) and storage.exists(clip.thumbnail_key)
    assert probe_media(storage.local_path(clip.proxy_key)).height == 720
    framing = clip.framing
    assert framing.vision["tracks"] and framing.auto_generated and job.result["faces"] >= 1
    assert framing.vision["clip_start"] == 0.5 and framing.mode in ("smart", "full_frame", "manual")
    assert clip.subtitles is None  # API v2: no captions are created
    assert clip.media_bytes and clip.media_bytes > 0
    # clips are rendered by the user, one by one: no automatic render after the framing analysis
    assert "auto_render" not in job.result and "render_clip" not in enqueued
    # a user-edited framing is kept (keyframes/warnings recomputed) when the analysis re-runs
    framing.auto_generated = False
    framing.mode, framing.config = "full_frame", {"mode": "full_frame"}
    db.commit()
    job_id = _job(db, clip, JobType.CLIP_VISION)
    tasks_clips.clip_vision_task(job_id)
    assert _status(db, job_id).status == "succeeded"
    assert db.scalar(select(ClipFramingSettings).where(ClipFramingSettings.clip_id == clip.id)).mode == "full_frame"


def test_clip_vision_applies_default_preset(db, user, enqueued, media_dir) -> None:
    video = make_test_video(media_dir / "noface.mp4", duration=4.0, width=640, height=360)
    clip = make_clip(db, user, make_source(db, user, video=video), start=0.0, end=3.5)
    preset = framing_service.create_preset(db, user, "Pip default", FramingConfig(mode="pip"), True)
    job_id = _job(db, clip, JobType.CLIP_VISION)
    tasks_clips.clip_vision_task(job_id)
    assert _status(db, job_id).status == "succeeded"
    framing = db.scalar(select(ClipFramingSettings).where(ClipFramingSettings.clip_id == clip.id))
    assert framing.mode == "pip" and framing.preset_id == preset["id"] and framing.auto_generated
    assert framing.vision["tracks"] == [] and any(w["code"] == "no_faces_detected" for w in framing.warnings)


def test_render_clip_end_to_end(db, user, enqueued, media_dir) -> None:
    video = make_test_video(media_dir / "render-src.mp4", duration=4.0, width=1280, height=720)
    src = make_source(db, user, video=video)
    make_transcript(db, src, spoken(0.3, 3.0))
    clip = make_clip(db, user, src, start=0.5, end=3.5, title="Render me")
    clip.render_options = {"video_preset": "ultrafast", "normalize_audio": False}
    db.commit()
    job_id = _job(db, clip, JobType.RENDER)
    tasks_clips.render_clip_task(job_id)
    job = _status(db, job_id)
    assert job.status == "succeeded", job.error
    clip = db.get(GeneratedClip, clip.id)
    storage = get_storage()
    out = probe_media(storage.local_path(clip.output_key))
    assert (out.width, out.height) == (1080, 1920) and abs(out.duration - 3.0) < 0.2
    assert clip.render_status == RenderStatus.RENDERED and render_is_current(clip) and clip.render_error is None
    assert clip.output_size == storage.size(clip.output_key) and clip.thumbnail_key.endswith(".jpg")
    note = db.scalar(select(Notification).where(Notification.user_id == user.id,
                                                Notification.type == "render_complete"))
    assert note is not None and note.link == f"/app/clips/{clip.id}"
    # inputs change -> new render under a new key; the previous object is deleted
    first = clip.output_key
    clip.render_options = {**clip.render_options, "video_crf": 20}
    db.commit()
    assert not render_is_current(clip)
    job_id = _job(db, clip, JobType.RENDER)
    tasks_clips.render_clip_task(job_id)
    assert _status(db, job_id).status == "succeeded"
    clip = db.get(GeneratedClip, clip.id)
    assert clip.output_key != first and not storage.exists(first) and render_is_current(clip)


def test_render_failure_marks_clip_failed(db, user, enqueued, media_dir) -> None:
    video = make_test_video(media_dir / "gone.mp4", duration=3.0, width=640, height=360)
    src = make_source(db, user, video=video)
    clip = make_clip(db, user, src, start=0.0, end=2.5)
    get_storage().delete(src.storage_key)  # the original disappeared
    job_id = _job(db, clip, JobType.RENDER)
    tasks_clips.render_clip_task(job_id)
    assert _status(db, job_id).status == "failed"
    clip = db.get(GeneratedClip, clip.id)
    assert clip.render_status == RenderStatus.FAILED
    assert "not available" in clip.render_error and "users/" not in clip.render_error  # no storage keys
    assert db.scalar(select(Notification).where(Notification.user_id == user.id,
                                                Notification.type == "render_failed")) is not None


LEFT = {"mode": "manual", "manual": {"crop": {"x": 0.0, "y": 0.0, "w": 0.3164, "h": 1.0}}}


def _podcast_clip(db, user, media_dir) -> GeneratedClip:
    if face_photo() is None:
        pytest.skip("test portrait unavailable (offline)")
    path, media, _ = analysed("podcast", media_dir)
    return make_clip(db, user, make_source(db, user, video=path, media=media), start=0.0, end=round(media.duration, 3))


def test_worker_refuses_framing_that_became_unsafe_after_the_request(db, user, enqueued, media_dir) -> None:
    """The gate is re-checked by the worker: an edit made while the job was queued cannot slip a
    crop that hides the streamer past the acknowledgement."""
    path, media, vision = analysed("podcast", media_dir) if face_photo() else pytest.skip("offline")
    clip = _podcast_clip(db, user, media_dir)
    framing_service.save_framing(db, clip, FramingConfig(mode="full_frame"), media=media, vision=vision)
    db.commit()
    job = request_render(db, clip)  # safe framing: accepted without acknowledgement
    framing_service.update(db, clip, FramingConfig.model_validate(LEFT))
    tasks_clips.render_clip_task(str(job.id))
    assert _status(db, str(job.id)).status == "failed"
    clip = db.get(GeneratedClip, clip.id)
    assert clip.render_status == RenderStatus.FAILED and clip.output_key is None
    assert "acknowledge" in clip.render_error
    with pytest.raises(ConflictError) as err:
        request_render(db, clip)
    assert err.value.code == "framing_warnings_unacknowledged" and err.value.details


def test_worker_analyses_an_unchecked_crop_before_rendering(db, user, enqueued, media_dir) -> None:
    """No face analysis yet (only the informational vision_missing warning): the worker analyses
    the clip first and refuses the crop it can now prove to be unsafe."""
    clip = _podcast_clip(db, user, media_dir)
    out = framing_service.update(db, clip, FramingConfig.model_validate(LEFT))
    assert out["vision"] is None and not [w for w in out["warnings"] if w.severity != "info"]
    job = request_render(db, clip)
    tasks_clips.render_clip_task(str(job.id))
    assert _status(db, str(job.id)).status == "failed"
    clip = db.get(GeneratedClip, clip.id)
    assert clip.framing.vision["tracks"] and clip.framing.mode == "manual" and clip.output_key is None
    assert {w["severity"] for w in clip.framing.warnings} & {"warning", "critical"}


def test_edit_during_render_leaves_the_clip_stale(db, user, enqueued, media_dir, monkeypatch) -> None:
    from app.core.database import SessionLocal
    from app.modules.rendering import renderer

    video = make_test_video(media_dir / "edit-during.mp4", duration=3.5, width=640, height=360)
    clip = make_clip(db, user, make_source(db, user, video=video), start=0.0, end=3.0)
    clip.render_options = {"video_preset": "ultrafast"}
    framing_service.save_framing(db, clip, FramingConfig(mode="full_frame"))
    db.commit()
    real = renderer.render_clip

    def render_while_user_edits(spec, **kw):
        result = real(spec, **kw)
        with SessionLocal() as other:  # another request, committed while the worker is encoding
            row = other.get(GeneratedClip, clip.id)
            framing_service.update(other, row, FramingConfig.model_validate(
                {"mode": "full_frame", "full_frame": {"scale": 0.8}}))
        return result

    monkeypatch.setattr(renderer, "render_clip", render_while_user_edits)
    job_id = _job(db, clip, JobType.RENDER)
    tasks_clips.render_clip_task(job_id)
    assert _status(db, job_id).status == "succeeded"
    clip = db.get(GeneratedClip, clip.id)
    assert clip.framing.config["full_frame"]["scale"] == 0.8  # the edit was not overwritten
    assert clip.render_status == RenderStatus.STALE and not render_is_current(clip)


def test_transcribe_clip_updates_transcript_text(db, user, enqueued, media_dir, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.modules.transcription.service import whisper_available

    if not whisper_available():
        pytest.skip("faster-whisper unavailable")
    video = make_speech_video(media_dir / "speech.mp4", "Hello everyone and welcome back to the stream today.")
    if video is None:
        pytest.skip("No text-to-speech engine available on this machine")
    monkeypatch.setattr(get_settings(), "whisper_model", "tiny")
    src = make_source(db, user, video=video)
    duration = probe_media(video).duration
    clip = make_clip(db, user, src, start=0.0, end=round(duration, 2))
    job_id = _job(db, clip, JobType.TRANSCRIBE_CLIP, language="en")
    tasks_clips.transcribe_clip_task(job_id)
    job = _status(db, job_id)
    assert job.status == "succeeded", job.error
    clip = db.get(GeneratedClip, clip.id)
    assert clip.subtitles is None and job.result["language"] == "en"
    assert "welcome" in clip.transcript_text.lower()
