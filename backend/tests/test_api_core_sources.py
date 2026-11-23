"""Sources API: list/patch/delete, analyze validation, jobs cancel/retry, YouTube links with real
retention (integration mocked), cloud import validation, manual clips and access control."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.storage import get_storage
from app.models import GeneratedClip, Notification, ProcessingJob, UserAsset, VideoSource
from app.models.enums import JobType, SourceStatus, SourceType
from app.modules.notifications.service import notify
from app.schemas.media import RetentionPoint
from tests.test_api_core_helpers import bearer, dispatched, make_source, small_video  # noqa: F401 - fixtures

API = "/api/v1"


def test_list_patch_delete_sources(client, db, user, small_video: Path) -> None:  # noqa: F811
    h = bearer(user)
    a = make_source(db, user, video=small_video, title="Podcast episode 12")
    make_source(db, user, title="Gaming night", status=SourceStatus.FAILED)
    r = client.get(f"{API}/sources", headers=h, params={"q": "podcast"})
    assert r.status_code == 200 and r.json()["total"] == 1
    item = r.json()["items"][0]
    assert item["id"] == str(a.id) and item["has_file"] and item["thumbnail_url"].startswith("http://testserver/api/v1/media/")
    assert item["retention_points"] is None and item["warnings"] == [] and item["duplicate"] is False
    assert client.get(f"{API}/sources", headers=h, params={"status": "failed,ready"}).json()["total"] == 1
    page = client.get(f"{API}/sources", headers=h, params={"limit": 1, "offset": 1}).json()
    assert page["limit"] == 1 and page["offset"] == 1 and len(page["items"]) == 1 and page["total"] == 2
    r = client.patch(f"{API}/sources/{a.id}", headers=h, json={"title": "Renamed", "description": "d"})
    assert r.json()["title"] == "Renamed" and r.json()["description"] == "d"
    key = a.storage_key
    assert client.delete(f"{API}/sources/{a.id}", headers=h).status_code == 204
    assert not get_storage().exists(key)
    assert client.get(f"{API}/sources/{a.id}", headers=h).status_code == 404


def test_analyze_validation_jobs_cancel_retry(client, db, user, small_video: Path, dispatched) -> None:  # noqa: F811
    h = bearer(user)
    no_file = make_source(db, user)
    r = client.post(f"{API}/sources/{no_file.id}/analyze", headers=h, json={"rights_confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_file"
    src = make_source(db, user, video=small_video)
    r = client.post(f"{API}/sources/{src.id}/analyze", headers=h, json={"rights_confirmed": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rights_not_confirmed"
    bad = {"rights_confirmed": True, "clip_durations": [2]}
    assert client.post(f"{API}/sources/{src.id}/analyze", headers=h, json=bad).status_code == 422
    bad = {"rights_confirmed": True, "whisper_model": "../../evil"}
    assert client.post(f"{API}/sources/{src.id}/analyze", headers=h, json=bad).status_code == 422
    body = {"rights_confirmed": True, "clip_durations": [15, 30], "max_clips": 4, "use_llm": False,
            "language": "en"}
    r = client.post(f"{API}/sources/{src.id}/analyze", headers=h, json=body)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["job_type"] == "analysis" and job["status"] == "queued" and job["source_title"] == "My video"
    assert dispatched[-1] == ("analyze_source", {"job_id": job["id"]})
    again = client.post(f"{API}/sources/{src.id}/analyze", headers=h, json=body).json()
    assert again["id"] == job["id"] and len(dispatched) == 1  # idempotent while active
    detail = client.get(f"{API}/sources/{src.id}", headers=h).json()
    assert detail["status"] == "queued" and detail["active_job"]["id"] == job["id"] and detail["rights_confirmed"]
    assert detail["analysis_options"]["clip_durations"] == [15.0, 30.0] and detail["analysis_options"]["max_clips"] == 4
    listed = client.get(f"{API}/jobs", headers=h, params={"active": "true", "video_source_id": str(src.id)}).json()
    assert listed["total"] == 1
    r = client.post(f"{API}/jobs/{job['id']}/cancel", headers=h)
    assert r.json()["status"] == "cancelled"
    assert client.get(f"{API}/sources/{src.id}", headers=h).json()["status"] == "uploaded"
    assert client.post(f"{API}/jobs/{job['id']}/cancel", headers=h).status_code == 409
    r = client.post(f"{API}/jobs/{job['id']}/retry", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["attempts"] == 0
    assert dispatched[-1][0] == "analyze_source"
    assert client.get(f"{API}/sources/{src.id}", headers=h).json()["status"] == "queued"


def test_transcript_and_media_url(client, db, user, small_video: Path) -> None:  # noqa: F811
    h = bearer(user)
    src = make_source(db, user, video=small_video)
    assert client.get(f"{API}/sources/{src.id}/transcript", headers=h).status_code == 404
    r = client.get(f"{API}/sources/{src.id}/media-url", headers=h)
    assert r.status_code == 200 and r.json()["browser_playable"] is True and r.json()["expires_in"] > 0
    media = client.get(r.json()["url"].replace("http://testserver", ""))
    assert media.status_code == 200 and media.content == small_video.read_bytes()


def test_youtube_source_with_real_retention(client, db, user, monkeypatch) -> None:
    from tests.test_publishing_factories import make_account

    account = make_account(db, user)
    meta = {"video_id": "dQw4w9WgXcQ", "title": "My upload", "description": "desc", "channel_id": "UC1",
            "is_owner": True, "owner_account_id": str(account.id), "duration_seconds": 212.0}
    monkeypatch.setattr("app.integrations.youtube.fetch_video_metadata", lambda db, user, vid: dict(meta))
    monkeypatch.setattr("app.integrations.youtube.fetch_retention_curve", lambda db, acc, vid: [
        RetentionPoint(ratio=0.0, audience_watch_ratio=1.0), RetentionPoint(ratio=0.5, audience_watch_ratio=0.6)])
    h = bearer(user)
    r = client.post(f"{API}/sources/youtube", headers=h, json={"url": "https://example.com/x", "rights_confirmed": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_youtube_url"
    r = client.post(f"{API}/sources/youtube", headers=h, json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rights_not_confirmed"
    r = client.post(f"{API}/sources/youtube", headers=h, json={"url": "https://youtu.be/dQw4w9WgXcQ",
                                                                "rights_confirmed": True})
    assert r.status_code == 200, r.text
    src = r.json()
    assert src["status"] == "metadata_only" and src["source_type"] == "youtube" and not src["has_file"]
    assert src["attribution"] == "Credit: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert src["original_available"] is False and src["duration"] == 212.0
    assert src["has_retention"] and len(src["retention_points"]) == 2 and src["title"] == "My upload"
    assert src["youtube_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    # same video again returns the existing source
    assert client.post(f"{API}/sources/youtube", headers=h,
                       json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "rights_confirmed": True}
                       ).json()["id"] == src["id"]
    from app.core.errors import PermissionDenied

    def denied(db, acc, vid):  # noqa: ANN001, ANN202
        raise PermissionDenied("Reconnect with analytics permission", code="missing_scope")

    monkeypatch.setattr("app.integrations.youtube.fetch_retention_curve", denied)
    r = client.post(f"{API}/sources/{src['id']}/retention/refresh", headers=h)
    assert r.status_code == 403
    after = client.get(f"{API}/sources/{src['id']}", headers=h).json()
    assert any("retention unavailable" in w for w in after["warnings"])
    assert after["has_retention"]  # previous real points are kept


def test_cloud_import_validation_and_job(client, db, user, monkeypatch, dispatched) -> None:  # noqa: F811
    h = bearer(user)
    r = client.post(f"{API}/sources/cloud", headers=h, json={"provider": "url", "url": "https://files.example.com/v.mp4",
                                                              "rights_confirmed": False})
    assert r.status_code == 422
    r = client.post(f"{API}/sources/cloud", headers=h, json={"provider": "url", "url": "http://files.example.com/v.mp4",
                                                              "rights_confirmed": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_import_url"
    r = client.post(f"{API}/sources/cloud", headers=h, json={"provider": "google_drive", "file_id": "1AbCdEfGhIjKlMn",
                                                              "rights_confirmed": True})
    assert r.status_code == 503 and "Drive" in r.json()["error"]["message"]
    monkeypatch.setattr("app.integrations.cloud_import.validate_import_url", lambda url: None)
    r = client.post(f"{API}/sources/cloud", headers=h, json={"provider": "url", "url": "https://files.example.com/v.mp4",
                                                              "title": "Imported", "rights_confirmed": True})
    assert r.status_code == 200, r.text
    src = r.json()
    assert src["status"] == "importing" and src["active_job"]["job_type"] == "import" and src["cloud_provider"] == "url"
    assert dispatched[-1] == ("import_source", {"job_id": src["active_job"]["id"]})


def test_manual_clip(client, db, user, small_video: Path, dispatched) -> None:  # noqa: F811
    h = bearer(user)
    src = make_source(db, user, video=small_video)
    r = client.post(f"{API}/sources/{src.id}/clips", headers=h, json={"start": 0.2, "end": 1.8, "title": "Mine"})
    assert r.status_code == 200, r.text
    clip = r.json()
    assert clip["title"] == "Mine" and clip["detection_method"] == "manual" and clip["highlight_score"] == 0
    assert clip["reasons"] == ["Created manually"] and abs(clip["duration"] - 1.6) < 1e-6
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.clip_id == uuid.UUID(clip["id"])))
    assert job.job_type == JobType.CLIP_VISION and job.idempotency_key == f"clip_vision:{clip['id']}"
    assert dispatched[-1] == ("clip_vision", {"job_id": str(job.id)})
    r = client.post(f"{API}/sources/{src.id}/clips", headers=h, json={"start": 0.25, "end": 1.8})
    assert r.status_code == 409 and r.json()["error"]["details"]["clip_id"] == clip["id"]
    assert client.post(f"{API}/sources/{src.id}/clips", headers=h, json={"start": 1, "end": 0.5}).status_code == 422
    assert client.get(f"{API}/sources/{src.id}", headers=h).json()["clip_count"] == 1


def test_access_control_other_users_objects(client, db, user, other_user, small_video: Path, dispatched) -> None:  # noqa: F811
    mine = make_source(db, user, video=small_video)
    job = ProcessingJob(user_id=user.id, job_type=JobType.ANALYSIS, video_source_id=mine.id, status="failed",
                        stage="x", progress=0.0, max_attempts=3)
    note = notify(db, user.id, "system", "Hello")
    asset = UserAsset(user_id=user.id, kind="watermark", name="wm", original_filename="wm.png",
                      storage_key=f"users/{user.id}/watermark/x.png", mime_type="image/png", file_size=1,
                      created_at=mine.created_at)
    db.add_all([job, asset])
    db.commit()
    h = bearer(other_user)
    for method, path in [("get", f"/sources/{mine.id}"), ("patch", f"/sources/{mine.id}"),
                         ("delete", f"/sources/{mine.id}"), ("post", f"/sources/{mine.id}/analyze"),
                         ("get", f"/sources/{mine.id}/media-url"), ("post", f"/sources/{mine.id}/clips"),
                         ("get", f"/jobs/{job.id}"), ("post", f"/jobs/{job.id}/retry"),
                         ("post", f"/notifications/{note.id}/read"), ("delete", f"/assets/{asset.id}")]:
        kwargs = {"json": {"title": "x"}} if method == "patch" else {}
        if method == "post":
            kwargs = {"json": {"rights_confirmed": True, "start": 0, "end": 1}}
        r = getattr(client, method)(f"{API}{path}", headers=h, **kwargs)
        assert r.status_code == 404, (method, path, r.status_code)
    assert client.get(f"{API}/sources", headers=h).json()["total"] == 0
    assert client.get(f"{API}/jobs", headers=h).json()["total"] == 0
    assert client.get(f"{API}/assets", headers=h).json() == []
    assert db.get(VideoSource, mine.id) is not None and db.get(Notification, note.id).read_at is None
    assert not db.scalars(select(GeneratedClip).where(GeneratedClip.video_source_id == mine.id)).all()
    assert SourceType.UPLOAD == mine.source_type
