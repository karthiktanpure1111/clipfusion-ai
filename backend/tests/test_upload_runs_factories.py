"""Factories for the API v2 workflow tests (sequential uploads, cleanup, storage, pipeline).

Imported as ``from tests.test_upload_runs_factories import ...``. Contains one sanity test.
A fake YouTube (resumable upload + ``videos.list``) is mounted on respx."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
import pytest

from app.core.security import create_access_token
from app.core.storage import clip_key, get_storage, source_key, source_thumb_key
from app.integrations.youtube_upload import UPLOAD_URL, VIDEOS_URL
from app.models import GeneratedClip, User, VideoSource
from app.models.enums import RenderStatus
from app.schemas.media import MediaInfo
from tests.test_publishing_factories import make_account

API = "/api/v1"
SESSION_BASE = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable"
RENDER_BYTES = 120_000
PROXY_BYTES = 4_000
THUMB_BYTES = 700
ORIGINAL_BYTES = 50_000


def headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def make_world(db, user: User, starts: list[float] | None = None, *, attribution: str | None = None):
    """A YouTube account and a ready source with an original file and rendered clips (render MP4,
    proxy and thumbnail stored under each clip's prefix). Returns ``(account, source, clips)``
    with clips in creation order (``starts`` order)."""
    from app.modules.rendering.service import compute_render_hash

    starts = starts if starts is not None else [10.0, 60.0, 120.0, 180.0, 240.0]
    account = make_account(db, user, token="yt-token")
    media = MediaInfo(duration=600.0, width=1920, height=1080, fps=30.0, has_audio=True)
    src = VideoSource(id=uuid.uuid4(), user_id=user.id, source_type="upload", status="ready", title="Long stream",
                      duration=600.0, width=1920, height=1080, fps=30.0, has_audio=True,
                      media_info=media.model_dump(mode="json"), rights_confirmed=True,
                      attribution=attribution or "Credit: Some Creator — https://example.com/original")
    storage = get_storage()
    src.storage_key = source_key(user.id, src.id, ".mp4")
    storage.put_bytes(os.urandom(ORIGINAL_BYTES), src.storage_key, "video/mp4")
    src.thumbnail_key = source_thumb_key(user.id, src.id)
    storage.put_bytes(b"t" * THUMB_BYTES, src.thumbnail_key, "image/jpeg")
    src.file_size = ORIGINAL_BYTES
    db.add(src)
    db.commit()
    clips = []
    for start in starts:
        title = f"Clip at {int(start)}"
        clip = GeneratedClip(id=uuid.uuid4(), user_id=user.id, video_source_id=src.id, start_time=start,
                             end_time=start + 30, duration=30.0, highlight_score=50.0, title=title,
                             transcript_text=f"words spoken at {int(start)}",
                             social_metadata={"youtube": {"title": title, "description": f"About {title}",
                                                          "tags": ["clip"], "hashtags": ["shorts"],
                                                          "privacy": "public"}},
                             render_options={})
        db.add(clip)
        db.flush()
        clip.output_key = clip_key(user.id, clip.id, "render-abc.mp4")
        clip.proxy_key = clip_key(user.id, clip.id, "proxy.mp4")
        clip.thumbnail_key = clip_key(user.id, clip.id, "thumb.jpg")
        storage.put_bytes(os.urandom(RENDER_BYTES), clip.output_key, "video/mp4")
        storage.put_bytes(b"p" * PROXY_BYTES, clip.proxy_key, "video/mp4")
        storage.put_bytes(b"j" * THUMB_BYTES, clip.thumbnail_key, "image/jpeg")
        clip.output_size = RENDER_BYTES
        clip.media_bytes = RENDER_BYTES + PROXY_BYTES + THUMB_BYTES
        clip.render_status = RenderStatus.RENDERED
        db.commit()
        clip.render_hash = compute_render_hash(clip)
        db.commit()
        clips.append(clip)
    return account, src, clips


def clip_files_exist(clip: GeneratedClip) -> bool:
    storage = get_storage()
    prefix = f"users/{clip.user_id}/clips/{clip.id}/"
    return any(storage.exists(prefix + n) for n in ("render-abc.mp4", "proxy.mp4", "thumb.jpg"))


class FakeYouTube:
    """Resumable upload sessions + ``videos.list`` statuses (``processed`` by default)."""

    def __init__(self, router: Any, default_status: str = "processed") -> None:
        self.default_status = default_status
        self.list_status: dict[str, str] = {}   # video id -> uploadStatus
        self.title_status: dict[str, str] = {}  # clip title -> uploadStatus
        self.fail_titles: set[str] = set()
        self.sessions: dict[str, str] = {}      # session id -> title
        self.completed: dict[str, str] = {}     # session id -> video id
        self.uploads: list[tuple[str, str]] = []  # (title, video id) in upload order
        self.descriptions: dict[str, str] = {}
        self.active = 0
        self.max_active = 0
        router.post(url__startswith=UPLOAD_URL).mock(side_effect=self._init)
        router.put(url__startswith=SESSION_BASE).mock(side_effect=self._put)
        router.get(url__startswith=VIDEOS_URL).mock(side_effect=self._list)

    def _init(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        title = body["snippet"]["title"]
        if title in self.fail_titles:
            return httpx.Response(400, json={"error": {"code": 400, "message": "The request metadata is invalid.",
                                                       "errors": [{"reason": "invalidVideoMetadata"}]}})
        sid = f"s{len(self.sessions) + 1}"
        self.sessions[sid] = title
        self.descriptions[title] = body["snippet"]["description"]
        return httpx.Response(200, headers={"Location": f"{SESSION_BASE}&upload_id={sid}"})

    def video(self, vid: str) -> dict[str, Any]:
        return {"id": vid, "status": {"uploadStatus": "uploaded", "privacyStatus": "private"}}

    def _put(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.params["upload_id"]
        cr = request.headers["Content-Range"]
        if cr.startswith("bytes */"):  # resume query
            if sid in self.completed:
                return httpx.Response(200, json=self.video(self.completed[sid]))
            return httpx.Response(308)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            total = int(cr.split("/")[1])
            end = int(cr.split("-")[1].split("/")[0])
            if end + 1 < total:
                return httpx.Response(308, headers={"Range": f"bytes=0-{end}"})
            vid = f"v{len(self.uploads) + 1:010d}"
            self.uploads.append((self.sessions[sid], vid))
            self.completed[sid] = vid
            return httpx.Response(200, json=self.video(vid))
        finally:
            self.active -= 1

    def _list(self, request: httpx.Request) -> httpx.Response:
        vid = request.url.params["id"]
        title = next((t for t, v in self.uploads if v == vid), None)
        status = self.list_status.get(vid) or self.title_status.get(title or "") or self.default_status
        if status == "timeout":
            raise httpx.ConnectTimeout("timed out", request=request)
        if status == "missing":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": [{"id": vid, "status": {"uploadStatus": status,
                                                                         "privacyStatus": "private",
                                                                         "rejectionReason": "duplicate"
                                                                         if status == "rejected" else None},
                                                    "processingDetails": {"processingStatus": "succeeded"}}]})

    @property
    def titles(self) -> list[str]:
        return [t for t, _ in self.uploads]


@pytest.fixture()
def tasks(monkeypatch) -> list[tuple[str, dict]]:
    """Capture task dispatches (``[(task_name, kwargs)]``) instead of running them."""
    calls: list[tuple[str, dict]] = []

    def fake_enqueue(name: str, *, countdown: float | None = None, **kwargs) -> str:
        calls.append((name, kwargs))
        return uuid.uuid4().hex

    monkeypatch.setattr("app.workers.jobs.enqueue", fake_enqueue)
    return calls


@pytest.fixture()
def fast_youtube(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)
    monkeypatch.setattr(get_settings(), "youtube_daily_quota", 10_000_000)


def run_publish_jobs(tasks: list[tuple[str, dict]], *, limit: int = 20) -> int:
    """Run dispatched ``publish_queue_item`` jobs one by one (each may dispatch the next)."""
    from app.workers.tasks_publishing import publish_queue_item

    ran = 0
    while ran < limit:
        pending = [(i, kw) for i, (name, kw) in enumerate(tasks) if name == "publish_queue_item"]
        if not pending:
            return ran
        i, kw = pending[0]
        tasks.pop(i)
        publish_queue_item(kw["job_id"])
        ran += 1
    return ran


def test_world_factory(db, user) -> None:
    from app.modules.rendering.service import render_is_current

    _acc, src, clips = make_world(db, user, [30.0, 10.0])
    assert len(clips) == 2 and all(render_is_current(c) and clip_files_exist(c) for c in clips)
    assert get_storage().exists(src.storage_key)
