"""/clips API (v2): listing/filters/sort (sequence order), detail, trim validation, Remove Clip,
bulk actions, metadata regeneration, access control and the removed approve/reject/subtitle API."""

from __future__ import annotations

import uuid

import pytest

from app.core.storage import clip_key, get_storage
from app.models import GeneratedClip, ProcessingJob, UserAsset
from app.models.enums import RenderStatus
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


def test_list_filters_sort_and_isolation(db, user, other_user, a1b_client, enqueued) -> None:
    src = make_source(db, user)
    a = make_clip(db, user, src, start=10, end=40, score=80, title="Big win")
    b = make_clip(db, user, src, start=100, end=130, score=30, title="Calm chat")
    c = make_clip(db, user, make_source(db, user), start=5, end=35, score=55, title="Other video")
    make_clip(db, other_user, make_source(db, other_user), title="Not mine")
    h = headers_for(user)
    page = a1b_client.get(f"{API}/clips", params={"sort": "score"}, headers=h).json()
    assert [i["id"] for i in page["items"]] == [str(a.id), str(c.id), str(b.id)] and page["total"] == 3
    item = page["items"][0]
    assert item["source_title"] == "Long stream" and item["render_is_current"] is False
    assert item["warning_count"] == 0 and item["framing_mode"] is None and item["thumbnail_url"] is None
    assert item["status"] == "pending" and item["upload_status"] == "none" and "review_status" not in item
    assert item["sequence"] == 1 and item["media_available"] is False and item["removed_at"] is None
    by_src = a1b_client.get(f"{API}/clips", params={"video_source_id": str(src.id)}, headers=h).json()
    assert [(i["id"], i["sequence"]) for i in by_src["items"]] == [(str(a.id), 1), (str(b.id), 2)]
    assert a1b_client.get(f"{API}/clips", params={"min_score": 50}, headers=h).json()["total"] == 2
    assert a1b_client.get(f"{API}/clips", params={"q": "calm"}, headers=h).json()["items"][0]["id"] == str(b.id)
    assert a1b_client.get(f"{API}/clips", params={"status": "pending"}, headers=h).json()["total"] == 3
    assert a1b_client.get(f"{API}/clips", params={"status": "generated"}, headers=h).json()["total"] == 0
    paged = a1b_client.get(f"{API}/clips", params={"limit": 1, "offset": 1, "sort": "score"}, headers=h).json()
    assert paged["limit"] == 1 and paged["offset"] == 1 and paged["items"][0]["id"] == str(c.id)
    other_h = headers_for(other_user)
    for method in ("get", "patch", "delete"):
        r = getattr(a1b_client, method)(f"{API}/clips/{a.id}", headers=other_h,
                                        **({"json": {"title": "x"}} if method == "patch" else {}))
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert a1b_client.get(f"{API}/clips").status_code == 401


def test_approve_reject_and_subtitle_endpoints_are_gone(db, user, a1b_client, enqueued) -> None:
    clip = make_clip(db, user, make_source(db, user))
    h = headers_for(user)
    for path in (f"/clips/{clip.id}/approve", f"/clips/{clip.id}/reject"):
        assert a1b_client.post(f"{API}{path}", headers=h).status_code in (404, 405)
    assert a1b_client.get(f"{API}/clips/{clip.id}/subtitles", headers=h).status_code in (404, 405)
    for action in ("approve", "reject", "delete"):
        r = a1b_client.post(f"{API}/clips/bulk", json={"ids": [str(clip.id)], "action": action}, headers=h)
        assert r.status_code == 422
    from app.main import create_app

    paths = set(create_app(start_workers=False).openapi()["paths"])
    assert not any(p.startswith(("/api/v1/subtitle", "/api/v1/fonts")) or p.endswith(("/approve", "/reject"))
                   or "/subtitles" in p for p in paths)
    assert "/api/v1/upload-runs" in paths and "/api/v1/storage" in paths
    assert "/api/v1/sources/{source_id}/pipeline" in paths


def test_detail_and_trim(db, user, a1b_client, enqueued) -> None:
    src = make_source(db, user, duration=300)
    src.storage_key = "users/x/sources/y/original.mp4"  # file content is not needed (dispatch recorded)
    db.commit()
    make_transcript(db, src, spoken(10, 20) + spoken(40, 50, "and then the second part happened right here"))
    clip = make_clip(db, user, src, start=10, end=30)
    h = headers_for(user)
    detail = a1b_client.get(f"{API}/clips/{clip.id}", headers=h).json()
    assert detail["source"]["width"] == 1920 and "subtitles" not in detail
    assert detail["render_options"]["normalize_audio"] is True and detail["active_jobs"] == []
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"end_time": 11.0}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_clip_length"
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"start_time": 200, "end_time": 320}, headers=h)
    assert r.json()["error"]["code"] == "clip_out_of_range"
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"start_time": 0, "end_time": 200}, headers=h)
    assert r.json()["error"]["code"] == "invalid_clip_length"
    clip.render_status, clip.output_key, clip.render_hash = RenderStatus.RENDERED, "k", "h"
    db.commit()
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"start_time": 38.0, "end_time": 55.0}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duration"] == 17.0 and body["render_status"] == "stale" and body["status"] == "processing"
    assert body["transcript_text"].startswith("and then")
    assert enqueued == ["clip_vision"] and [j["job_type"] for j in body["active_jobs"]] == ["clip_vision"]
    make_clip(db, user, src, start=60, end=80)
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"start_time": 60, "end_time": 80}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "clip_exists"


def test_metadata_and_render_options_edits(db, user, other_user, a1b_client, enqueued) -> None:
    clip = make_clip(db, user, make_source(db, user))
    h = headers_for(user)
    meta = {"youtube": {"title": "My title", "description": "d", "tags": ["a"], "hashtags": ["shorts"]},
            "instagram": {"caption": "cap", "hashtags": ["reels"]}, "source": "llm"}
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"social_metadata": meta, "title": " New "}, headers=h)
    body = r.json()
    assert body["social_metadata"]["source"] == "user" and body["title"] == "New"
    bad = a1b_client.patch(f"{API}/clips/{clip.id}", json={"social_metadata": {"youtube": {"privacy": "x"}}},
                           headers=h)
    assert bad.status_code == 422
    from app.core.database import utcnow

    foreign = UserAsset(user_id=other_user.id, kind="intro", name="i", original_filename="i.mp4", storage_key="k",
                        mime_type="video/mp4", file_size=1, created_at=utcnow())
    db.add(foreign)
    db.commit()
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"render_options": {"intro_asset_id": str(foreign.id)}},
                         headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "asset_not_found"
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"render_options": {"remove_silence": True}}, headers=h)
    assert r.json()["render_options"]["remove_silence"] is True
    r = a1b_client.post(f"{API}/clips/{clip.id}/metadata/regenerate", json={"use_llm": False}, headers=h)
    assert r.status_code == 200 and r.json()["job_type"] == "metadata" and enqueued == ["generate_metadata"]
    from app.workers.tasks_clips import generate_metadata_task

    generate_metadata_task(r.json()["id"])
    db.expire_all()
    assert db.get(GeneratedClip, clip.id).social_metadata["source"] == "extractive"
    assert db.get(ProcessingJob, uuid.UUID(r.json()["id"])).status == "succeeded"


def test_bulk_render_and_remove(db, user, other_user, a1b_client, enqueued) -> None:
    src = make_source(db, user)
    a, b, c = (make_clip(db, user, src, start=s, end=s + 20) for s in (0, 30, 60))
    mine_not = make_clip(db, other_user, make_source(db, other_user))
    h = headers_for(user)
    r = a1b_client.post(f"{API}/clips/bulk", json={"ids": [str(c.id), str(mine_not.id)], "action": "render"},
                        headers=h)
    res = {x["id"]: x for x in r.json()["results"]}
    assert res[str(c.id)]["error"]["code"] == "source_missing"  # no original file attached
    assert res[str(mine_not.id)]["error"]["code"] == "not_found"
    key = clip_key(user.id, c.id, "thumb.jpg")
    get_storage().put_bytes(b"jpg", key, "image/jpeg")
    r = a1b_client.post(f"{API}/clips/bulk", json={"ids": [str(c.id)], "action": "remove"}, headers=h)
    assert r.json()["results"][0]["ok"] and not get_storage().exists(key)
    r = a1b_client.delete(f"{API}/clips/{b.id}", headers=h)
    assert r.status_code == 200 and r.json() == {"id": str(b.id), "status": "removed", "freed_bytes": 0}
    db.expire_all()
    kept = db.get(GeneratedClip, b.id)
    assert kept is not None and kept.removed_at is not None and db.get(GeneratedClip, mine_not.id) is not None
    page = a1b_client.get(f"{API}/clips", params={"video_source_id": str(src.id)}, headers=h).json()
    assert [i["id"] for i in page["items"]] == [str(a.id)] and page["items"][0]["sequence"] == 1
    removed = a1b_client.get(f"{API}/clips", params={"video_source_id": str(src.id), "include_removed": True},
                             headers=h).json()["items"]
    assert {i["id"]: (i["status"], i["sequence"]) for i in removed} == {
        str(a.id): ("pending", 1), str(b.id): ("removed", None), str(c.id): ("removed", None)}
    r = a1b_client.patch(f"{API}/clips/{b.id}", json={"title": "x"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "clip_removed"
