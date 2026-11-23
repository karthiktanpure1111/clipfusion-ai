"""Resumable chunked uploads end-to-end with a real video (resume, duplicate, invalid file)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.storage import get_storage
from app.models import UploadSession, VideoSource
from app.modules.uploads.service import upload_dir
from tests.test_api_core_helpers import bearer, dispatched, small_video  # noqa: F401 - fixtures

API = "/api/v1"
CHUNK = 16 * 1024


@pytest.fixture(autouse=True)
def _small_chunks(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "upload_chunk_bytes", CHUNK)


def _init(client, headers, name: str, size: int) -> dict:
    r = client.post(f"{API}/uploads/init", headers=headers, json={"filename": name, "size": size,
                                                                   "mime_type": "video/mp4"})
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, headers, upload_id: str, index: int, data: bytes):
    return client.put(f"{API}/uploads/{upload_id}/chunks/{index}", headers={**headers,
                      "Content-Type": "application/octet-stream"}, content=data)


def _chunks(data: bytes) -> list[bytes]:
    return [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]


def test_chunked_upload_resume_complete_and_duplicate(client, db, user, other_user, small_video: Path,  # noqa: F811
                                                       dispatched) -> None:  # noqa: F811
    h = bearer(user)
    data = small_video.read_bytes()
    up = _init(client, h, "My Stream.mp4", len(data))
    parts = _chunks(data)
    assert up["chunk_size"] == CHUNK and up["total_chunks"] == len(parts) and up["received_chunks"] == []
    # wrong length is rejected (exact chunk length required)
    r = _put(client, h, up["upload_id"], 0, parts[0][:-1])
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_chunk_length"
    assert _put(client, h, up["upload_id"], len(parts), b"x").status_code == 422
    # upload all but the last chunk, then "resume"
    for i, part in enumerate(parts[:-1]):
        assert _put(client, h, up["upload_id"], i, part).status_code == 200
    assert _put(client, h, up["upload_id"], 0, parts[0]).json()["complete"] is False  # idempotent re-upload
    state = client.get(f"{API}/uploads/{up['upload_id']}", headers=h).json()
    missing = sorted(set(range(len(parts))) - set(state["received_chunks"]))
    assert missing == [len(parts) - 1]
    r = client.post(f"{API}/uploads/{up['upload_id']}/complete", headers=h, json={"rights_confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "upload_incomplete"
    last = _put(client, h, up["upload_id"], missing[0], parts[-1])
    assert last.json() == {"received_chunks": list(range(len(parts))), "complete": True}
    # other users can't see it
    assert client.get(f"{API}/uploads/{up['upload_id']}", headers=bearer(other_user)).status_code == 404
    assert _put(client, bearer(other_user), up["upload_id"], 0, parts[0]).status_code == 404
    # rights confirmation required
    r = client.post(f"{API}/uploads/{up['upload_id']}/complete", headers=h, json={"rights_confirmed": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "rights_not_confirmed"
    r = client.post(f"{API}/uploads/{up['upload_id']}/complete", headers=h,
                    json={"rights_confirmed": True, "rights_statement": "I recorded it"})
    assert r.status_code == 200, r.text
    src = r.json()
    # API v2: processing starts automatically (defaults from the user's settings)
    assert src["status"] == "queued" and src["has_file"] and src["duplicate"] is False
    assert src["active_job"]["job_type"] == "analysis" and dispatched[-1][0] == "analyze_source"
    assert src["analysis_options"]["mode"] == "balanced" and src["analysis_options"]["min_duration"] == 20
    assert src["processing_mode"] == "balanced" and src["original_available"] and src["original_bytes"] > 0
    assert src["title"] == "My Stream" and src["source_type"] == "upload" and src["rights_confirmed"]
    assert abs(src["duration"] - 2.0) < 0.3 and src["width"] == 320 and src["thumbnail_url"]
    assert src["mime_type"] == "video/mp4" and src["clip_count"] == 0
    row = db.get(VideoSource, uuid.UUID(src["id"]))
    assert row.checksum_sha256 and get_storage().exists(row.storage_key) and get_storage().exists(row.thumbnail_key)
    assert not upload_dir(row.id).exists() and not upload_dir(uuid.UUID(up["upload_id"])).exists()
    # completing again is idempotent
    again = client.post(f"{API}/uploads/{up['upload_id']}/complete", headers=h, json={"rights_confirmed": True})
    assert again.status_code == 200 and again.json()["id"] == src["id"]
    # same file again -> duplicate of the existing source
    up2 = _init(client, h, "copy.mp4", len(data))
    for i, part in enumerate(parts):
        _put(client, h, up2["upload_id"], i, part)
    r = client.post(f"{API}/uploads/{up2['upload_id']}/complete", headers=h, json={"rights_confirmed": True})
    assert r.status_code == 200 and r.json()["duplicate"] is True and r.json()["id"] == src["id"]
    assert [name for name, _ in dispatched].count("analyze_source") == 1  # duplicates never re-process


def test_upload_rejects_renamed_non_video_and_bad_init(client, db, user) -> None:
    h = bearer(user)
    assert client.post(f"{API}/uploads/init", headers=h, json={"filename": "notes.txt", "size": 10}).status_code == 422
    r = client.post(f"{API}/uploads/init", headers=h, json={"filename": "big.mp4", "size": 10**15})
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_too_large"
    junk = os.urandom(CHUNK + 100)
    up = _init(client, h, "fake.mp4", len(junk))
    for i, part in enumerate(_chunks(junk)):
        assert _put(client, h, up["upload_id"], i, part).status_code == 200
    r = client.post(f"{API}/uploads/{up['upload_id']}/complete", headers=h, json={"rights_confirmed": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsupported_format"
    session = db.get(UploadSession, uuid.UUID(up["upload_id"]))
    db.refresh(session)
    assert session.status == "aborted" and not upload_dir(session.id).exists()
    assert _put(client, h, up["upload_id"], 0, junk[:CHUNK]).status_code == 409


def test_abort_upload(client, db, user) -> None:
    h = bearer(user)
    up = _init(client, h, "a.mp4", CHUNK * 2)
    _put(client, h, up["upload_id"], 0, b"\0" * CHUNK)
    assert client.delete(f"{API}/uploads/{up['upload_id']}", headers=h).status_code == 204
    assert client.get(f"{API}/uploads/{up['upload_id']}", headers=h).json()["status"] == "aborted"
