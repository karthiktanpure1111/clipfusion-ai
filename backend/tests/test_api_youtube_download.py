"""Opt-in YouTube-link downloads (yt-dlp is mocked; one real-network test is marked network)."""

from __future__ import annotations

import shutil
import uuid

import pytest

from app.core.errors import IntegrationNotConfigured
from app.integrations import ytdlp
from app.models import ProcessingJob, VideoSource
from app.models.enums import JobType, SourceStatus
from app.workers.jobs import run_job
from tests.helpers.media import make_test_video
from tests.test_api_core_helpers import bearer, dispatched  # noqa: F401 - fixture

API = "/api/v1"
META = {"video_id": "jNQXAC9IVRw", "title": "Me at the zoo", "channel_title": "jawed", "duration_seconds": 19.0}


def _youtube_source(client, h, monkeypatch) -> dict:
    monkeypatch.setattr("app.integrations.youtube.fetch_video_metadata", lambda db, user, vid: dict(META))
    r = client.post(f"{API}/sources/youtube", headers=h, json={"url": "https://youtu.be/jNQXAC9IVRw",
                                                                "rights_confirmed": True})
    assert r.status_code == 200, r.text
    return r.json()


def _enable(client, h, on: bool = True) -> None:
    r = client.patch(f"{API}/users/me", headers=h, json={"settings": {"youtube_download_enabled": on}})
    assert r.status_code == 200 and r.json()["settings"]["youtube_download_enabled"] is on


def test_download_is_off_by_default_and_needs_rights(client, db, user, monkeypatch, dispatched) -> None:  # noqa: F811
    h = bearer(user)
    src = _youtube_source(client, h, monkeypatch)
    r = client.post(f"{API}/sources/{src['id']}/download", headers=h, json={"rights_confirmed": True})
    assert r.status_code == 403 and r.json()["error"]["code"] == "youtube_download_disabled"
    _enable(client, h)
    r = client.post(f"{API}/sources/{src['id']}/download", headers=h, json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rights_not_confirmed"
    r = client.post(f"{API}/sources/{src['id']}/download", headers=h, json={"rights_confirmed": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "importing"
    job = db.query(ProcessingJob).filter_by(video_source_id=uuid.UUID(src["id"]), job_type=JobType.IMPORT).one()
    assert job.params["provider"] == "youtube" and job.params["quality"] == "best" and job.params["auto_analyze"]
    assert dispatched[-1][0] == "import_source"


def test_import_task_downloads_validates_and_starts_processing(client, db, user, monkeypatch, tmp_media_dir,
                                                              dispatched) -> None:  # noqa: F811
    h = bearer(user)
    src = _youtube_source(client, h, monkeypatch)
    _enable(client, h)
    video = make_test_video(tmp_media_dir / "yt-dl.mp4", duration=4.0)

    def fake_download(video_id, dest, *, quality, max_bytes, progress=None, ensure_space=None):
        assert video_id == "jNQXAC9IVRw" and quality == "1080"
        progress and progress(0.5, "half")
        out = dest / "Me at the zoo.mp4"
        shutil.copy(video, out)
        return out, out.name, "video/mp4"

    monkeypatch.setattr(ytdlp, "download", fake_download)
    assert client.post(f"{API}/sources/{src['id']}/download", headers=h,
                       json={"rights_confirmed": True, "quality": "1080"}).status_code == 200
    job = db.query(ProcessingJob).filter_by(video_source_id=uuid.UUID(src["id"]), job_type=JobType.IMPORT).one()
    from app.workers.tasks_processing import _import_handler

    run_job(str(job.id), _import_handler, task_name="import_source")
    db.expire_all()
    source = db.get(VideoSource, uuid.UUID(src["id"]))
    assert source.storage_key and source.status in (SourceStatus.UPLOADED, SourceStatus.QUEUED)
    assert any(name == "analyze_source" for name, _ in dispatched)  # processing started automatically


def test_private_video_fails_with_a_clear_message(client, db, user, monkeypatch, dispatched) -> None:  # noqa: F811
    h = bearer(user)
    src = _youtube_source(client, h, monkeypatch)
    _enable(client, h)

    def refused(*a, **k):
        raise ytdlp._explain(RuntimeError("ERROR: [youtube] x: Private video. Sign in if you've been granted access"))

    monkeypatch.setattr(ytdlp, "download", refused)
    client.post(f"{API}/sources/{src['id']}/download", headers=h, json={"rights_confirmed": True})
    job = db.query(ProcessingJob).filter_by(video_source_id=uuid.UUID(src["id"]), job_type=JobType.IMPORT).one()
    from app.workers.tasks_processing import _import_handler, on_import_failed

    run_job(str(job.id), _import_handler, task_name="import_source", on_final_failure=on_import_failed)
    db.expire_all()
    assert db.get(ProcessingJob, job.id).status == "failed" and db.get(ProcessingJob, job.id).attempts == 1
    source = db.get(VideoSource, uuid.UUID(src["id"]))
    assert source.status == SourceStatus.METADATA_ONLY and "private" in source.error.lower()


def test_metadata_falls_back_to_ytdlp_only_when_downloads_are_on(client, user, monkeypatch) -> None:
    h = bearer(user)

    def not_configured(db, user, vid):
        raise IntegrationNotConfigured("no key")

    monkeypatch.setattr("app.integrations.youtube.fetch_video_metadata", not_configured)
    monkeypatch.setattr(ytdlp, "fetch_info", lambda vid: {**META, "video_id": vid, "source": "yt-dlp"})
    body = {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "rights_confirmed": True}
    assert client.post(f"{API}/sources/youtube", headers=h, json=body).status_code == 503
    _enable(client, h)
    r = client.post(f"{API}/sources/youtube", headers=h, json=body)
    assert r.status_code == 200 and r.json()["title"] == "Me at the zoo"


@pytest.mark.network
def test_real_download_of_a_tiny_public_video(tmp_path) -> None:
    path, name, mime = ytdlp.download("jNQXAC9IVRw", tmp_path, quality="720", max_bytes=50_000_000)
    assert path.exists() and path.stat().st_size > 100_000 and name.endswith(".mp4")




def test_quality_list_groups_resolution_and_fps_and_skips_hdr() -> None:
    info = {"duration": 100, "formats": [
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 1_000_000},
        {"format_id": "251", "vcodec": "none", "acodec": "opus", "filesize": 9_000_000},
        {"format_id": "299", "vcodec": "avc1.64002a", "height": 1080, "fps": 60, "filesize": 50_000_000},
        {"format_id": "303", "vcodec": "vp9", "height": 1080, "fps": 60, "filesize": 40_000_000},
        {"format_id": "315", "vcodec": "vp9", "height": 2160, "fps": 60, "filesize": 300_000_000},
        {"format_id": "337", "vcodec": "vp9.2", "height": 2160, "fps": 60, "dynamic_range": "HDR10", "filesize": 1},
        {"format_id": "136", "vcodec": "avc1.4d401f", "height": 720, "fps": 30, "filesize": 10_000_000},
    ]}
    q = ytdlp.qualities_from_info(info)
    assert [x["label"] for x in q] == ["2160p60 (4K)", "1080p60 (Full HD)", "720p30 (HD)"]
    assert q[1]["codec"] == "avc1" and q[1]["size_bytes"] == 51_000_000  # H.264 preferred, AAC audio counted
    assert ytdlp._format_spec("best").startswith("bv*[dynamic_range=SDR]")
    assert "[height<=1080]" in ytdlp._format_spec("1080")
