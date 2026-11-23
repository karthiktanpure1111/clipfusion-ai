"""Cloud import: SSRF-safe URL validation, platform refusal, redirects, size caps; Drive download."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.errors import ValidationFailed
from app.integrations import cloud_import
from app.integrations.cloud_import import download_url, validate_import_url
from app.integrations.google_drive import FILES_URL, download_drive_file, parse_drive_file_id

DNS = {"files.example.com": ["93.184.215.14"], "cdn.example.net": ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"],
       "internal.example.com": ["10.1.2.3"], "rebind.example.com": ["93.184.215.14", "127.0.0.1"]}


@pytest.fixture(autouse=True)
def _dns(monkeypatch) -> None:
    def resolve(host: str) -> list[str]:
        if host not in DNS:
            raise ValidationFailed(f"Could not resolve host '{host}'")
        return DNS[host]

    monkeypatch.setattr(cloud_import, "resolve_host", resolve)
    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)


@pytest.mark.parametrize("url", [
    "http://files.example.com/video.mp4",
    "ftp://files.example.com/video.mp4",
    "https://127.0.0.1/video.mp4",
    "https://10.0.0.5/video.mp4",
    "https://192.168.1.20/video.mp4",
    "https://[::1]/video.mp4",
    "https://[::ffff:127.0.0.1]/video.mp4",
    "https://169.254.169.254/latest/meta-data/",
    "https://0.0.0.0/x",
    "https://224.0.0.1/x",
    "https://internal.example.com/video.mp4",
    "https://rebind.example.com/video.mp4",
    "https://user:secret@files.example.com/video.mp4",
    "https://files.example.com:8443/video.mp4",
    "https:///nohost",
])
def test_refuses_unsafe_urls(url: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_import_url(url)


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/shorts/dQw4w9WgXcQ", "https://rr3---sn-abc.googlevideo.com/videoplayback?x=1",
    "https://www.twitch.tv/videos/123", "https://www.tiktok.com/@a/video/1", "https://www.instagram.com/reel/x/",
    "https://www.facebook.com/watch?v=1",
])
def test_refuses_platform_hosts_with_explanation(url: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        validate_import_url(url)
    assert exc.value.code == "platform_url_refused"
    assert "original video file" in exc.value.message


def test_accepts_public_https() -> None:
    validate_import_url("https://files.example.com/share/video.mp4?sig=abc")
    validate_import_url("https://cdn.example.net/a.mov")


@respx.mock
def test_download_follows_validated_redirects(tmp_path) -> None:
    respx.get("https://files.example.com/s/abc").mock(return_value=httpx.Response(
        302, headers={"Location": "https://cdn.example.net/blob/123"}))
    body = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000
    respx.get("https://cdn.example.net/blob/123").mock(return_value=httpx.Response(200, content=body, headers={
        "Content-Type": "video/mp4", "Content-Disposition": "attachment; filename*=UTF-8''My%20Stream%20%231.mp4"}))
    progress: list[float] = []
    path, name, mime = download_url("https://files.example.com/s/abc", tmp_path, max_bytes=10_000,
                                    progress=lambda f, m=None: progress.append(f))
    assert path.read_bytes() == body and path.parent == tmp_path and path.suffix == ".mp4"
    assert name == "My Stream 1.mp4" and mime == "video/mp4" and progress[-1] == pytest.approx(1.0)


@respx.mock
def test_download_refuses_redirect_to_private_and_oversize(tmp_path) -> None:
    respx.get("https://files.example.com/to-internal").mock(return_value=httpx.Response(
        301, headers={"Location": "https://169.254.169.254/latest/"}))
    with pytest.raises(ValidationFailed):
        download_url("https://files.example.com/to-internal", tmp_path, max_bytes=1000)
    respx.get("https://files.example.com/big.mp4").mock(return_value=httpx.Response(
        200, content=b"y" * 2048, headers={"Content-Type": "video/mp4"}))
    with pytest.raises(ValidationFailed) as exc:
        download_url("https://files.example.com/big.mp4", tmp_path, max_bytes=1000)
    assert exc.value.code == "file_too_large"
    assert list(tmp_path.iterdir()) == []
    respx.get("https://files.example.com/page").mock(return_value=httpx.Response(
        200, content=b"<html></html>", headers={"Content-Type": "text/html; charset=utf-8"}))
    with pytest.raises(ValidationFailed) as exc:
        download_url("https://files.example.com/page", tmp_path, max_bytes=1000)
    assert exc.value.code == "not_a_file"


@respx.mock
def test_download_too_many_redirects(tmp_path) -> None:
    respx.get(url__startswith="https://files.example.com/loop").mock(return_value=httpx.Response(
        302, headers={"Location": "/loop"}))
    with pytest.raises(ValidationFailed) as exc:
        download_url("https://files.example.com/loop", tmp_path, max_bytes=1000)
    assert exc.value.code == "too_many_redirects"


def test_parse_drive_file_id() -> None:
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    assert parse_drive_file_id(f"https://drive.google.com/file/d/{fid}/view?usp=sharing") == fid
    assert parse_drive_file_id(f"https://drive.google.com/open?id={fid}") == fid
    assert parse_drive_file_id(fid) == fid
    assert parse_drive_file_id("https://example.com/file/d/abc") is None


@respx.mock
def test_drive_download_and_mime_check(tmp_path) -> None:
    fid = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    meta = respx.get(f"{FILES_URL}/{fid}", params={"fields": "id,name,mimeType,size"}).mock(
        return_value=httpx.Response(200, json={"id": fid, "name": "../stream.mov", "mimeType": "video/quicktime",
                                               "size": "6"}))
    media = respx.get(f"{FILES_URL}/{fid}", params={"alt": "media"}).mock(return_value=httpx.Response(
        200, content=b"moov!!"))
    path, name, mime = download_drive_file("drive-token", fid, tmp_path, max_bytes=100)
    assert path.read_bytes() == b"moov!!" and name == "stream.mov" and mime == "video/quicktime"
    assert meta.calls[0].request.headers["Authorization"] == "Bearer drive-token" and media.called
    other = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa98765"
    respx.get(f"{FILES_URL}/{other}").mock(return_value=httpx.Response(200, json={
        "id": other, "name": "notes.pdf", "mimeType": "application/pdf", "size": "10"}))
    with pytest.raises(ValidationFailed) as exc:
        download_drive_file("drive-token", other, tmp_path, max_bytes=100)
    assert exc.value.code == "not_a_video"
