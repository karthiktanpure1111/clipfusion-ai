"""Factories/fixtures shared by the A1b test modules (clips, framing, subtitles, render, tasks).

Imported as ``from tests.test_api_clips_factories import ...``. Contains one sanity test."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.core.storage import get_storage, source_key
from app.models import GeneratedClip, Transcript, User, VideoAnalysis, VideoSource
from app.schemas.media import MediaInfo

ROUTERS = ("clips", "framing", "rendering", "sources", "upload_runs", "storage")


def api_client() -> TestClient:
    """A minimal app with the A1b routers under /api/v1 (independent of app.main)."""
    import importlib

    app = FastAPI()
    register_exception_handlers(app)
    for name in ROUTERS:
        app.include_router(importlib.import_module(f"app.modules.{name}.router").router, prefix="/api/v1")
    return TestClient(app)


def headers_for(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def make_source(db, user: User, *, video: Path | None = None, media: MediaInfo | None = None,
                duration: float = 600.0, width: int = 1920, height: int = 1080, title: str = "Long stream",
                silences: list[tuple[float, float]] | None = None) -> VideoSource:
    """A ready source; ``video`` is stored as the original file (its media info is probed)."""
    if video is not None and media is None:
        from app.modules.analysis.probe import probe_media

        media = probe_media(video)
    media = media or MediaInfo(duration=duration, width=width, height=height, fps=30.0, has_audio=True)
    src = VideoSource(id=uuid.uuid4(), user_id=user.id, source_type="upload", status="ready", title=title,
                      duration=media.duration, width=media.width, height=media.height, fps=media.fps,
                      has_audio=media.has_audio, media_info=media.model_dump(mode="json"))
    if video is not None:
        key = source_key(user.id, src.id, video.suffix)
        get_storage().put_file(video, key, "video/mp4")
        src.storage_key, src.original_filename, src.file_size = key, video.name, video.stat().st_size
    db.add(src)
    if silences is not None:
        db.add(VideoAnalysis(video_source_id=src.id, audio={"sample_hz": 10.0, "silences": silences}))
    db.commit()
    return src


def make_transcript(db, source: VideoSource, words: list[tuple[str, float, float]], language: str = "en") -> None:
    """One segment per sentence-ish group of (word, start, end) source-time words."""
    segments = [{"id": 0, "start": words[0][1], "end": words[-1][2], "text": " ".join(w for w, _, _ in words),
                 "words": [{"word": f" {w}", "start": s, "end": e, "probability": 0.9} for w, s, e in words]}]
    db.add(Transcript(video_source_id=source.id, language=language, model="tiny", segments=segments,
                      text=segments[0]["text"], word_count=len(words), duration=source.duration))
    db.commit()


def spoken(start: float, end: float, text: str = "this is a really great moment we all loved it") -> list:
    """Evenly spaced source-time words between ``start`` and ``end``."""
    toks = text.split()
    step = (end - start) / len(toks)
    return [(t, round(start + i * step, 3), round(start + (i + 0.8) * step, 3)) for i, t in enumerate(toks)]


def make_clip(db, user: User, source: VideoSource, *, start: float = 10.0, end: float = 40.0, score: float = 50.0,
              title: str = "Epic moment", **kw) -> GeneratedClip:
    clip = GeneratedClip(id=uuid.uuid4(), user_id=user.id, video_source_id=source.id, start_time=start, end_time=end,
                         duration=end - start, highlight_score=score, title=title,
                         transcript_text=kw.pop("transcript_text", "this is a really great moment"), **kw)
    db.add(clip)
    db.commit()
    return clip


def record_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record dispatched task names instead of running them in the local executor."""
    calls: list[str] = []
    monkeypatch.setattr("app.workers.jobs.enqueue", lambda name, countdown=None, **kw: calls.append(name) or "t")
    return calls


def test_factories_build_a_clip(db, user) -> None:
    source = make_source(db, user)
    make_transcript(db, source, spoken(10, 20))
    clip = make_clip(db, user, source)
    assert clip.duration == 30 and source.media_info["width"] == 1920
