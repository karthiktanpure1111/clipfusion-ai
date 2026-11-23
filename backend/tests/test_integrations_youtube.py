"""YouTube Data/Analytics API client and resumable uploader (HTTP mocked with respx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.errors import IntegrationNotConfigured, PermissionDenied
from app.integrations import quota
from app.integrations.youtube import (
    ANALYTICS_URL,
    API_BASE,
    fetch_retention_curve,
    fetch_video_metadata,
    parse_iso8601_duration,
    parse_video_id,
)
from app.integrations.youtube_upload import UPLOAD_URL, YouTubeUploader, build_video_resource, limit_tags
from tests.test_publishing_factories import make_account, write_bytes_file

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=sess-1"
KIB = 1024


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)


@pytest.mark.parametrize(("url", "expected"), [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=x", "dQw4w9WgXcQ"),
    ("youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=short", None),
    ("https://vimeo.com/123456", None),
    ("https://evil.com/watch?v=dQw4w9WgXcQ", None),
    ("", None),
])
def test_parse_video_id(url: str, expected: str | None) -> None:
    assert parse_video_id(url) == expected


def test_parse_duration() -> None:
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("P1DT1M") == 86460
    assert parse_iso8601_duration("garbage") == 0


def test_video_resource_limits() -> None:
    res = build_video_resource({"title": "<b>" + "x" * 150, "description": "d", "privacy": "unlisted",
                                "tags": [f"tag number {i}" for i in range(80)], "hashtags": ["shorts"]})
    assert len(res["snippet"]["title"]) == 100 and "<" not in res["snippet"]["title"]
    assert res["snippet"]["categoryId"] == "22" and res["snippet"]["description"].endswith("#shorts")
    tags = res["snippet"]["tags"]
    assert sum(len(t) + 2 for t in tags) + len(tags) - 1 <= 500
    assert res["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False,
                             "containsSyntheticMedia": False}
    assert limit_tags(["a", "A", "#b"]) == ["a", "b"]


@respx.mock
def test_resumable_upload_with_308_and_resume_after_error(tmp_path) -> None:
    total = 600 * KIB
    path = write_bytes_file(tmp_path / "clip.mp4", total)
    init = respx.post(url__startswith=UPLOAD_URL).mock(return_value=httpx.Response(200, headers={"Location": SESSION}))
    seen: list[str] = []
    state = {"failed": False}

    def put(request: httpx.Request) -> httpx.Response:
        cr = request.headers["Content-Range"]
        seen.append(cr)
        if cr == f"bytes 0-{256 * KIB - 1}/{total}":
            return httpx.Response(308, headers={"Range": f"bytes=0-{256 * KIB - 1}"})
        if cr.startswith(f"bytes {256 * KIB}-") and not state["failed"]:
            state["failed"] = True
            return httpx.Response(503, json={"error": {"message": "Backend Error", "errors": [{"reason": "backendError"}]}})
        if cr == f"bytes */{total}":
            return httpx.Response(308, headers={"Range": f"bytes=0-{512 * KIB - 1}"})
        if cr == f"bytes {512 * KIB}-{total - 1}/{total}":
            assert len(request.content) == total - 512 * KIB
            return httpx.Response(200, json={"id": "vid12345678", "status": {"uploadStatus": "uploaded"}})
        return httpx.Response(400, json={"error": {"message": f"unexpected {cr}"}})

    respx.put(SESSION).mock(side_effect=put)
    sessions: list[str] = []
    video = YouTubeUploader("tok", chunk_bytes=256 * KIB).upload(
        path, build_video_resource({"title": "T"}), on_session=sessions.append)
    assert video["id"] == "vid12345678"
    assert sessions == [SESSION]
    sent = json.loads(init.calls[0].request.content)
    assert sent["snippet"]["title"] == "T"
    req = init.calls[0].request
    assert req.headers["X-Upload-Content-Length"] == str(total) and req.headers["Authorization"] == "Bearer tok"
    assert "uploadType=resumable" in str(req.url) and "part=snippet%2Cstatus" in str(req.url)
    assert seen == [f"bytes 0-{256 * KIB - 1}/{total}", f"bytes {256 * KIB}-{512 * KIB - 1}/{total}",
                    f"bytes */{total}", f"bytes {512 * KIB}-{total - 1}/{total}"]


@respx.mock
def test_upload_resumes_persisted_session_or_restarts_expired(tmp_path) -> None:
    total = 300 * KIB
    path = write_bytes_file(tmp_path / "clip.mp4", total)
    seen: list[str] = []

    def put(request: httpx.Request) -> httpx.Response:
        cr = request.headers["Content-Range"]
        seen.append(cr)
        if cr == f"bytes */{total}":
            return httpx.Response(308, headers={"Range": f"bytes=0-{256 * KIB - 1}"})
        return httpx.Response(201, json={"id": "resumed0001"})

    respx.put(SESSION).mock(side_effect=put)
    init = respx.post(url__startswith=UPLOAD_URL)
    video = YouTubeUploader("tok", chunk_bytes=256 * KIB).upload(path, {}, session_url=SESSION)
    assert video["id"] == "resumed0001" and not init.called
    assert seen == [f"bytes */{total}", f"bytes {256 * KIB}-{total - 1}/{total}"]

    expired = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=old"
    respx.put(expired).mock(return_value=httpx.Response(404, json={"error": {"message": "gone"}}))
    init.mock(return_value=httpx.Response(200, headers={"Location": SESSION}))
    sessions: list[str] = []
    video = YouTubeUploader("tok", chunk_bytes=256 * KIB).upload(path, {}, session_url=expired,
                                                                  on_session=sessions.append)
    assert video["id"] == "resumed0001" and sessions == [SESSION]


@respx.mock
def test_fetch_video_metadata_with_connected_account(db, user) -> None:
    acc = make_account(db, user, external_id="UCowner000000000000000000", token="yt-token")
    route = respx.get(f"{API_BASE}/videos").mock(return_value=httpx.Response(200, json={"items": [{
        "id": "dQw4w9WgXcQ",
        "snippet": {"title": "Stream", "description": "desc", "channelId": "UCowner000000000000000000",
                    "channelTitle": "Me", "publishedAt": "2026-01-02T03:04:05Z", "tags": ["a"],
                    "thumbnails": {"high": {"url": "https://i.ytimg.com/hq.jpg"}}},
        "contentDetails": {"duration": "PT1H30M"}, "statistics": {"viewCount": "1200", "likeCount": "30"},
        "status": {"privacyStatus": "public"}}]}))
    before = quota.get_usage(db, quota.YOUTUBE, str(acc.id))
    meta = fetch_video_metadata(db, user, "dQw4w9WgXcQ")
    assert route.calls[0].request.headers["Authorization"] == "Bearer yt-token"
    assert meta["duration_seconds"] == 5400 and meta["view_count"] == 1200 and meta["comment_count"] is None
    assert meta["is_owner"] is True and meta["owner_account_id"] == str(acc.id)
    assert meta["thumbnail_url"] == "https://i.ytimg.com/hq.jpg"
    assert quota.get_usage(db, quota.YOUTUBE, str(acc.id)) == before + 1


@respx.mock
def test_fetch_video_metadata_api_key_or_not_configured(db, user, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "youtube_api_key", "")
    with pytest.raises(IntegrationNotConfigured) as exc:
        fetch_video_metadata(db, user, "dQw4w9WgXcQ")
    assert "YOUTUBE_API_KEY" in exc.value.message
    monkeypatch.setattr(get_settings(), "youtube_api_key", "server-key")
    route = respx.get(f"{API_BASE}/videos").mock(return_value=httpx.Response(200, json={"items": [{
        "id": "dQw4w9WgXcQ", "snippet": {"title": "Public", "channelId": "UCsomeoneelse"},
        "contentDetails": {"duration": "PT10S"}, "statistics": {}}]}))
    meta = fetch_video_metadata(db, user, "dQw4w9WgXcQ")
    assert "key=server-key" in str(route.calls[0].request.url)
    assert meta["is_owner"] is False and meta["owner_account_id"] is None


@respx.mock
def test_retention_curve_owner_only(db, user) -> None:
    acc = make_account(db, user, external_id="UCmine00000000000000000000")
    respx.get(f"{API_BASE}/videos").mock(side_effect=lambda req: httpx.Response(200, json={"items": [{
        "id": "abcdefghijk", "snippet": {"channelId": "UCmine00000000000000000000" if "abcdefghijk" in str(req.url)
                                         else "UCother", "publishedAt": "2026-02-01T00:00:00Z"}}]}))
    report = respx.get(ANALYTICS_URL).mock(return_value=httpx.Response(200, json={
        "columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"},
                          {"name": "relativeRetentionPerformance"}],
        "rows": [[0.02, 0.9, 0.55], [0.01, 1.0, 0.6]]}))
    points = fetch_retention_curve(db, acc, "abcdefghijk")
    assert [p.ratio for p in points] == [0.01, 0.02] and points[0].audience_watch_ratio == 1.0
    params = report.calls[0].request.url.params
    assert params["ids"] == "channel==MINE" and params["filters"] == "video==abcdefghijk"
    assert params["startDate"] == "2026-02-01" and params["dimensions"] == "elapsedVideoTimeRatio"
    with pytest.raises(PermissionDenied):
        fetch_retention_curve(db, acc, "zzzzzzzzzzz")
    acc.scopes = [s for s in acc.scopes if "analytics" not in s]
    with pytest.raises(PermissionDenied) as exc:
        fetch_retention_curve(db, acc, "abcdefghijk")
    assert exc.value.code == "missing_scope"
