"""GET /storage numbers, POST /sources/{id}/delete-original, disk-space checks (API v2)."""

from __future__ import annotations

from app.core.storage import get_storage
from app.models import GeneratedClip, VideoSource
from app.modules.storage.service import clip_prefix, prefix_size
from tests.test_upload_runs_factories import (  # noqa: F401 - fixtures
    API,
    ORIGINAL_BYTES,
    PROXY_BYTES,
    RENDER_BYTES,
    THUMB_BYTES,
    clip_files_exist,
    headers,
    make_world,
    tasks,
)


def test_storage_numbers(client, db, user, other_user, tasks) -> None:  # noqa: F811
    _acc, src, clips = make_world(db, user, [10.0, 60.0, 120.0])
    make_world(db, other_user, [10.0])  # other users' files are never counted
    h = headers(user)
    removed = prefix_size(clip_prefix(user.id, clips[2].id))
    assert client.delete(f"{API}/clips/{clips[2].id}", headers=h).json()["freed_bytes"] == removed
    s = client.get(f"{API}/storage", headers=h).json()
    assert s["originals_bytes"] == ORIGINAL_BYTES
    assert s["renders_bytes"] == 2 * RENDER_BYTES and s["proxies_bytes"] == 2 * PROXY_BYTES
    assert s["thumbnails_bytes"] == 3 * THUMB_BYTES  # 2 clips + the source thumbnail
    assert s["managed_bytes"] >= ORIGINAL_BYTES + 2 * (RENDER_BYTES + PROXY_BYTES) + 3 * THUMB_BYTES
    assert s["temp_bytes"] >= 0 and s["disk_free_bytes"] > 0 and s["freed_bytes_total"] == removed
    assert s["clips"] == {"total": 3, "generated": 2, "uploaded": 0, "removed": 1, "cleaned": 0}
    row = next(x for x in s["sources"] if x["id"] == str(src.id))
    assert row == {"id": str(src.id), "title": "Long stream", "original_bytes": ORIGINAL_BYTES,
                   "clip_bytes": 2 * (RENDER_BYTES + PROXY_BYTES + THUMB_BYTES), "clips_total": 3, "clips_done": 1,
                   "all_done": False, "original_available": True}
    assert client.get(f"{API}/storage").status_code == 401


def test_delete_original_keeps_clips(client, db, user, tasks) -> None:  # noqa: F811
    _acc, src, clips = make_world(db, user, [10.0, 60.0])
    h = headers(user)
    key, thumb = src.storage_key, src.thumbnail_key
    r = client.post(f"{API}/sources/{src.id}/delete-original", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(src.id) and body["status"] == "deleted" and body["freed_bytes"] == ORIGINAL_BYTES + THUMB_BYTES
    assert body["source"]["original_available"] is False and body["source"]["original_deleted_at"]
    assert not get_storage().exists(key) and not get_storage().exists(thumb)
    assert all(clip_files_exist(c) for c in clips)
    db.expire_all()
    assert db.get(VideoSource, src.id).original_deleted_at is not None
    assert db.get(GeneratedClip, clips[0].id).output_key is not None
    again = client.post(f"{API}/sources/{src.id}/delete-original", headers=h).json()
    assert again["freed_bytes"] == 0  # idempotent
    r = client.post(f"{API}/sources/{src.id}/analyze", json={"rights_confirmed": True}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "original_deleted"
    r = client.post(f"{API}/clips/{clips[0].id}/render", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "original_deleted"


def test_insufficient_disk_space_is_reported(client, db, user, monkeypatch, tasks) -> None:  # noqa: F811
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "min_free_disk_mb", 10**12)  # more than any disk
    r = client.post(f"{API}/uploads/init", json={"filename": "a.mp4", "size": 1000}, headers=headers(user))
    assert r.status_code == 507 and r.json()["error"]["code"] == "insufficient_disk_space"
    assert "Not enough free disk space" in r.json()["error"]["message"]
    _acc, _src, clips = make_world(db, user, [10.0])
    clips[0].render_hash = "stale"
    db.commit()
    r = client.post(f"{API}/clips/{clips[0].id}/render", json={"acknowledge_warnings": True}, headers=headers(user))
    assert r.status_code == 507
