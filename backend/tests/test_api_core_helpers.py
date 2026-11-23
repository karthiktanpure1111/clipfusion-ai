"""Shared fixtures/factories for the core API tests (imported by test_api_core_* / test_tasks_*)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.database import utcnow
from app.core.storage import get_storage, source_key, source_thumb_key
from app.models import User, VideoSource
from app.models.enums import SourceStatus, SourceType
from tests.helpers.media import make_test_video

CSRF = {"X-Requested-With": "ClipFusion"}


@pytest.fixture()
def dispatched(monkeypatch) -> list[tuple[str, dict]]:
    """Capture task dispatches instead of running them (``[(task_name, kwargs)]``)."""
    calls: list[tuple[str, dict]] = []

    def fake_enqueue(name: str, *, countdown: float | None = None, **kwargs) -> str:
        calls.append((name, kwargs))
        return uuid.uuid4().hex

    monkeypatch.setattr("app.workers.jobs.enqueue", fake_enqueue)
    return calls


@pytest.fixture(scope="session")
def small_video(tmp_media_dir: Path) -> Path:
    """A real 2-second 320x240 H.264/AAC MP4."""
    path = tmp_media_dir / "core-small.mp4"
    if not path.exists():
        make_test_video(path, duration=2.0, width=320, height=240, fps=15)
    return path


def make_source(db, user: User, *, video: Path | None = None, status: str | None = None, title: str = "My video",
                rights: bool = False, source_type: str = SourceType.UPLOAD) -> VideoSource:
    """Insert a source row; with ``video`` the file is stored like a completed upload."""
    sid = uuid.uuid4()
    src = VideoSource(id=sid, user_id=user.id, source_type=source_type, title=title,
                      status=status or (SourceStatus.UPLOADED if video else SourceStatus.PENDING_UPLOAD),
                      rights_confirmed=rights, rights_confirmed_at=utcnow() if rights else None)
    if video is not None:
        key = source_key(user.id, sid, ".mp4")
        get_storage().put_file(video, key)
        get_storage().put_bytes(b"\xff\xd8\xff\xe0fakejpeg", source_thumb_key(user.id, sid))
        src.storage_key, src.thumbnail_key = key, source_thumb_key(user.id, sid)
        src.original_filename, src.file_size, src.mime_type = "video.mp4", video.stat().st_size, "video/mp4"
        src.duration, src.width, src.height, src.fps, src.has_audio = 2.0, 320, 240, 15.0, True
        src.media_info = {"duration": 2.0, "width": 320, "height": 240, "fps": 15.0, "has_audio": True,
                          "browser_playable": True, "container": "mp4"}
    db.add(src)
    db.commit()
    return src


def bearer(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id)}"}
