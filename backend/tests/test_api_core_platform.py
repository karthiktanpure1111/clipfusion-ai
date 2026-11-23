"""Media Range serving, dashboard counts, notifications, health/capabilities and assets."""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path

from PIL import Image

from app.core.database import utcnow
from app.core.storage import get_storage
from app.models import GeneratedClip, PublishingQueueItem
from app.models.enums import QueueStatus, ReviewStatus, SourceStatus
from app.modules.notifications.service import notify
from tests.helpers.media import make_test_video
from tests.test_api_core_helpers import bearer, make_source, small_video  # noqa: F401 - fixture

API = "/api/v1"


def test_media_range_requests(client, db, user, small_video: Path) -> None:  # noqa: F811
    src = make_source(db, user, video=small_video)
    data = small_video.read_bytes()
    path = get_storage().url(src.storage_key).replace("http://testserver", "")
    r = client.get(path, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == data[:100]
    assert r.headers["content-range"] == f"bytes 0-99/{len(data)}" and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4" and r.headers["cache-control"] == "private, max-age=3600"
    r = client.get(path, headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == data[-10:]
    r = client.get(path, headers={"Range": f"bytes={len(data) - 5}-"})
    assert r.status_code == 206 and r.content == data[-5:]
    r = client.get(path, headers={"Range": f"bytes={len(data) + 10}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{len(data)}"
    full = client.get(path)
    assert full.status_code == 200 and full.content == data and full.headers["content-length"] == str(len(data))
    assert full.headers["x-content-type-options"] == "nosniff" and full.headers["x-request-id"]
    dl = get_storage().url(src.storage_key, download_name="my clip.mp4").replace("http://testserver", "")
    assert client.get(dl).headers["content-disposition"].startswith('attachment; filename="my clip.mp4"')
    assert client.get(f"{API}/media/not.a-token").status_code == 401
    assert client.get(get_storage().url("users/x/missing.mp4").replace("http://testserver", "")).status_code == 404


def test_dashboard_summary_counts(client, db, user, other_user) -> None:
    from tests.test_publishing_factories import make_account

    src = make_source(db, user, status=SourceStatus.PROCESSING)
    make_source(db, user, status=SourceStatus.READY)
    make_source(db, other_user)
    clips = []
    for i, review in enumerate((ReviewStatus.PENDING, ReviewStatus.PENDING, ReviewStatus.APPROVED)):
        c = GeneratedClip(user_id=user.id, video_source_id=src.id, start_time=i * 10.0, end_time=i * 10.0 + 5,
                          duration=5.0, highlight_score=50.0, title=f"clip {i}", review_status=review)
        db.add(c)
        clips.append(c)
    db.flush()
    acc = make_account(db, user)
    now = utcnow()
    for clip, status, when in ((clips[0], QueueStatus.SCHEDULED, now + timedelta(hours=2)),
                               (clips[1], QueueStatus.PUBLISHED, now - timedelta(hours=1)),
                               (clips[2], QueueStatus.FAILED, now - timedelta(hours=3))):
        db.add(PublishingQueueItem(user_id=user.id, clip_id=clip.id, connected_account_id=acc.id, platform="youtube",
                                   status=status, scheduled_at=when,
                                   published_at=when if status == QueueStatus.PUBLISHED else None,
                                   idempotency_key=f"{clip.id}:{acc.id}"))
    db.commit()
    r = client.get(f"{API}/dashboard/summary", headers=bearer(user))
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["total_videos"], s["total_clips"], s["processing_videos"], s["ready_to_upload"]) == (2, 3, 1, 0)
    assert "awaiting_approval" not in s and s["uploaded_clips"] == 0
    assert (s["scheduled_posts"], s["published_clips"], s["failed_uploads"]) == (1, 1, 1)
    assert s["next_publish_at"] is not None and len(s["upcoming"]) == 1 and s["upcoming"][0]["clip_title"] == "clip 0"
    assert len(s["clips_per_day"]) == 14 and s["clips_per_day"][-1]["count"] == 3
    assert sum(d["youtube"] for d in s["published_per_day"]) == 1
    assert len(s["recent_clips"]) == 3 and s["active_jobs"] == []


def test_notifications(client, db, user, other_user) -> None:
    h = bearer(user)
    a = notify(db, user.id, "processing_complete", "Done", "body", link="/app/videos/x")
    a.created_at = utcnow() - timedelta(seconds=5)  # the Windows clock is too coarse to order these
    notify(db, user.id, "system", "Second")
    notify(db, other_user.id, "system", "Not yours")
    db.commit()
    r = client.get(f"{API}/notifications", headers=h).json()
    assert r["total"] == 2 and r["unread_count"] == 2 and r["items"][0]["title"] == "Second"
    one = client.post(f"{API}/notifications/{a.id}/read", headers=h).json()
    assert one["read_at"] is not None and one["link"] == "/app/videos/x"
    assert client.get(f"{API}/notifications", headers=h, params={"unread_only": True}).json()["total"] == 1
    assert client.post(f"{API}/notifications/read-all", headers=h).json() == {"updated": 1}
    assert client.get(f"{API}/notifications", headers=h).json()["unread_count"] == 0


def test_health_and_capabilities(client, monkeypatch) -> None:
    from app.core.config import get_settings

    # independent of the developer's .env (which may contain real Google credentials)
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    monkeypatch.setattr(get_settings(), "google_client_secret", "")
    assert client.get(f"{API}/health").json() == {"status": "ok", "app": "clipfusion"}
    ready = client.get(f"{API}/health/ready").json()
    assert ready["checks"] == {"database": "ok", "redis": "not_used", "ffmpeg": "ok"} and ready["status"] == "ok"
    caps = client.get(f"{API}/system/capabilities").json()
    assert set(caps["ffmpeg"]) == {"ffmpeg", "ffprobe", "libx264", "libass"} and caps["ffmpeg"]["ffmpeg"] is True
    assert caps["whisper"]["available"] is True and caps["llm"]["configured"] is False
    assert caps["youtube_configured"] is False and "GOOGLE_CLIENT_ID" in caps["google_missing_env"]
    assert caps["storage_backend"] == "local" and caps["worker_mode"] == "local"
    assert caps["app"] == "clipfusion" and caps["api_version"] == 2 and caps["subtitles"] is False
    assert caps["disk_free_bytes"] > 0
    assert isinstance(caps["face_detectors"], list) and isinstance(caps["fonts_installed"], int)
    assert caps["instagram_auth_flow"] in ("instagram_login", "facebook_login", "unified_login")


def test_assets_upload_list_delete(client, db, user, tmp_path: Path) -> None:
    h = bearer(user)
    buf = io.BytesIO()
    Image.new("RGBA", (64, 32), (255, 0, 0, 128)).save(buf, "PNG")
    r = client.post(f"{API}/assets", headers=h, data={"kind": "watermark", "name": "Logo"},
                    files={"file": ("logo.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200, r.text
    wm = r.json()
    assert (wm["kind"], wm["width"], wm["height"], wm["mime_type"]) == ("watermark", 64, 32, "image/png") and wm["url"]
    r = client.post(f"{API}/assets", headers=h, data={"kind": "background"},
                    files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_image"
    intro = make_test_video(tmp_path / "intro.mp4", duration=1.5, width=320, height=240, fps=15)
    r = client.post(f"{API}/assets", headers=h, data={"kind": "intro"},
                    files={"file": ("intro.mp4", intro.read_bytes(), "video/mp4")})
    assert r.status_code == 200 and abs(r.json()["duration"] - 1.5) < 0.3 and r.json()["name"] == "intro"
    long_video = make_test_video(tmp_path / "long.mp4", duration=21, width=160, height=120, fps=5, with_audio=False)
    r = client.post(f"{API}/assets", headers=h, data={"kind": "outro"},
                    files={"file": ("long.mp4", long_video.read_bytes(), "video/mp4")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_too_long"
    assert client.post(f"{API}/assets", headers=h, data={"kind": "sticker"},
                       files={"file": ("a.png", buf.getvalue(), "image/png")}).status_code == 422
    assert len(client.get(f"{API}/assets", headers=h).json()) == 2
    assert [a["id"] for a in client.get(f"{API}/assets", headers=h, params={"kind": "watermark"}).json()] == [wm["id"]]
    assert client.delete(f"{API}/assets/{wm['id']}", headers=h).status_code == 204
    assert len(client.get(f"{API}/assets", headers=h).json()) == 1


def test_default_rate_limit_is_enforced_per_endpoint(client, user, monkeypatch, small_video: Path, db) -> None:  # noqa: F811
    """Regression: slowapi's middleware cannot see routes of included routers (they were unlimited)."""
    from app.core.config import get_settings
    from app.core.ratelimit import limiter

    h = bearer(user)
    media = get_storage().url(make_source(db, user, video=small_video).storage_key).replace("http://testserver", "")
    monkeypatch.setattr(get_settings(), "rate_limit_default", "5/minute")
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    try:
        codes = [client.get(f"{API}/jobs", headers=h).status_code for _ in range(7)]
        assert codes == [200] * 5 + [429] * 2
        assert client.get(f"{API}/jobs", headers=h).json()["error"]["code"] == "rate_limited"
        assert client.get(f"{API}/sources", headers=h).status_code == 200  # separate bucket per endpoint
        assert {client.get(media).status_code for _ in range(8)} == {200}
        assert {client.get(f"{API}/health").status_code for _ in range(8)} == {200}
        # routes with their own limit keep it (auth 10/minute, not the 5/minute default)
        body = {"email": "nobody@example.com", "password": "x"}
        codes = [client.post(f"{API}/auth/login", json=body).status_code for _ in range(11)]
        assert codes == [401] * 10 + [429]
    finally:
        limiter.reset()


def test_auth_router_mounted_only_when_auth_required(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    def paths(required: bool) -> set[str]:
        monkeypatch.setattr(get_settings(), "auth_required", required)
        return set(create_app(start_workers=False).openapi()["paths"])

    with_auth, without_auth = paths(True), paths(False)
    auth_only = {p for p in with_auth if p.startswith(f"{API}/auth/")}
    assert f"{API}/auth/login" in auth_only and with_auth - without_auth == auth_only
    assert f"{API}/users/me" in without_auth and f"{API}/accounts" in without_auth
