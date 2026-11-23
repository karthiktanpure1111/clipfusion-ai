"""Render freshness hash (no subtitles in API v2), downloads (incl. cleaned clips) and the real
server-side preview frame (1080x1920 JPEG from the export renderer)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.core.storage import clip_key, get_storage
from app.modules.rendering.service import compute_render_hash, render_is_current
from tests.helpers.media import make_test_video
from tests.test_api_clips_factories import (
    api_client,
    headers_for,
    make_clip,
    make_source,
    make_transcript,
    record_enqueue,
    spoken,
)

API = "/api/v1"


@pytest.fixture()
def enqueued(monkeypatch) -> list[str]:
    return record_enqueue(monkeypatch)


@pytest.fixture(scope="module")
def a1b_client():
    return api_client()


@pytest.fixture(scope="module")
def short_video(tmp_media_dir: Path) -> Path:
    return make_test_video(tmp_media_dir / "a1b-preview.mp4", duration=4.0, width=1280, height=720)


def test_render_hash_download_and_state(db, user, a1b_client) -> None:
    clip = make_clip(db, user, make_source(db, user), title="Héllo World! Best clip")
    h = headers_for(user)
    r = a1b_client.get(f"{API}/clips/{clip.id}/download", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_rendered"
    key = clip_key(user.id, clip.id, "render-abc.mp4")
    get_storage().put_bytes(b"\x00" * 64, key, "video/mp4")
    clip.output_key, clip.render_status, clip.render_hash = key, "rendered", compute_render_hash(clip)
    db.commit()
    assert render_is_current(clip)
    d = a1b_client.get(f"{API}/clips/{clip.id}/download", headers=h).json()
    assert d["filename"] == f"hello-world-best-clip-{str(clip.id)[:8]}.mp4" and d["url"]
    state = a1b_client.get(f"{API}/clips/{clip.id}/render", headers=h).json()
    assert state["render_is_current"] and state["job"] is None and state["output_url"]
    assert a1b_client.get(f"{API}/clips/{clip.id}", headers=h).json()["status"] == "generated"
    # subtitles never affect the render hash (clips are exported clean)
    from app.models import ClipSubtitleStyle

    db.add(ClipSubtitleStyle(clip_id=clip.id, enabled=True, style={"font_size": 99}, captions=[]))
    db.commit()
    db.refresh(clip)
    assert render_is_current(clip)
    # any real render input change invalidates the render
    a1b_client.patch(f"{API}/clips/{clip.id}", json={"render_options": {"video_crf": 22}}, headers=h)
    db.expire_all()
    out = a1b_client.get(f"{API}/clips/{clip.id}", headers=h).json()
    assert out["render_status"] == "stale" and out["render_is_current"] is False and out["status"] == "pending"
    # a cleaned clip's download explains where it went
    clip = db.get(type(clip), clip.id)
    clip.output_key, clip.cleanup_status = None, "cleaned"
    clip.upload_status, clip.youtube_video_id = "uploaded", "abcdefghijk"
    clip.youtube_url = "https://www.youtube.com/watch?v=abcdefghijk"
    db.commit()
    r = a1b_client.get(f"{API}/clips/{clip.id}/download", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "media_deleted"
    out = a1b_client.get(f"{API}/clips/{clip.id}", headers=h).json()
    assert out["status"] == "cleaned" and out["thumbnail_url"] == "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg"


def test_preview_frame_uses_the_real_renderer(db, user, other_user, a1b_client, short_video) -> None:
    src = make_source(db, user, video=short_video)
    make_transcript(db, src, spoken(0.2, 3.5))
    clip = make_clip(db, user, src, start=0.0, end=3.9)
    h = headers_for(user)
    r = a1b_client.post(f"{API}/clips/{clip.id}/preview-frame", json={"t": 1.0}, headers=h)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"] == "image/jpeg"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (1920, 1080)
    body = {"t": 1.0, "framing": {"mode": "manual", "manual": {"crop": {"x": 0.3, "y": 0, "w": 0.3164, "h": 1}}}}
    r2 = a1b_client.post(f"{API}/clips/{clip.id}/preview-frame", json=body, headers=h)
    img2 = cv2.imdecode(np.frombuffer(r2.content, np.uint8), cv2.IMREAD_COLOR)
    assert img2.shape[:2] == (1920, 1080) and float(np.abs(img2.astype(int) - img.astype(int)).mean()) > 5
    assert a1b_client.post(f"{API}/clips/{clip.id}/preview-frame", json={"t": 1.0},
                           headers=headers_for(other_user)).status_code == 404
