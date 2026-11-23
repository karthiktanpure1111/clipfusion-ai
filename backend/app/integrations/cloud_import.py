"""Import an authorised video file from a direct HTTPS link (cloud storage share links,
pre-signed object URLs, ...). SSRF-safe and deliberately refuses streaming platforms.

* https only, default port, no credentials in the URL
* every resolved IP must be public (no private/loopback/link-local/multicast/reserved);
  the connected peer address is re-checked after connecting (DNS rebinding)
* at most 5 redirects, each hop re-validated
* platform video pages (YouTube, Twitch, TikTok, Instagram, Facebook, ...) are refused:
  their videos must be uploaded as original files by the rights holder
"""

from __future__ import annotations

import ipaddress
import logging
import mimetypes
import re
import socket
import uuid
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.core.errors import ValidationFailed
from app.core.security import safe_filename
from app.integrations import http

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
_CHUNK = 1024 * 1024
BLOCKED_HOST_SUFFIXES = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com", "ytimg.com",
    "twitch.tv", "ttvnw.net", "jtvnw.net", "tiktok.com", "tiktokcdn.com", "tiktokv.com",
    "instagram.com", "cdninstagram.com", "facebook.com", "fb.watch", "fbcdn.net",
    "kick.com", "twitter.com", "x.com", "twimg.com", "vimeo.com", "dailymotion.com",
    "rumble.com", "reddit.com", "redd.it", "snapchat.com",
)
_CD_FILENAME_STAR = re.compile(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", re.IGNORECASE)
_CD_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


def _ip_allowed(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                 or ip.is_reserved or ip.is_unspecified)


def _blocked_platform(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in BLOCKED_HOST_SUFFIXES)


def resolve_host(host: str) -> list[str]:
    """Resolve ``host`` to IP strings (separate function so tests can stub DNS)."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationFailed(f"Could not resolve host '{host}'", code="invalid_import_url") from exc
    return sorted({info[4][0] for info in infos})


def validate_import_url(url: str) -> None:
    """Raise :class:`ValidationFailed` unless ``url`` is a safe, public HTTPS file link."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError as exc:
        raise ValidationFailed("Invalid URL", code="invalid_import_url") from exc
    if parts.scheme.lower() != "https":
        raise ValidationFailed("Only https:// links can be imported", code="invalid_import_url")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValidationFailed("The URL has no host", code="invalid_import_url")
    if parts.username or parts.password:
        raise ValidationFailed("URLs containing credentials are not allowed", code="invalid_import_url")
    if port not in (None, 443):
        raise ValidationFailed("Only the standard HTTPS port (443) is allowed", code="invalid_import_url")
    if _blocked_platform(host):
        raise ValidationFailed(
            "Videos hosted on YouTube, Twitch, TikTok, Instagram, Facebook and similar platforms cannot be "
            "downloaded from their pages. Upload the original video file you own instead (for YouTube links, "
            "paste the link on the YouTube tab to fetch metadata and then attach your original file).",
            code="platform_url_refused")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    addresses = [str(literal)] if literal else resolve_host(host)
    for addr in addresses:
        if not _ip_allowed(ipaddress.ip_address(addr.split("%")[0])):
            raise ValidationFailed("The link points to a private or reserved network address",
                                   code="ssrf_blocked")


def _check_peer(resp: httpx.Response) -> None:
    stream = resp.extensions.get("network_stream")
    if stream is None:
        return
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:  # noqa: BLE001 - transport without that info
        return
    if peer and not _ip_allowed(ipaddress.ip_address(str(peer[0]).split("%")[0])):
        raise ValidationFailed("The link resolved to a private network address", code="ssrf_blocked")


def filename_from_response(resp: httpx.Response, url: str) -> str:
    """Filename from ``Content-Disposition`` (RFC 6266) or the URL path, sanitised."""
    cd = resp.headers.get("content-disposition", "")
    name = None
    if m := _CD_FILENAME_STAR.search(cd):
        name = unquote(m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8", errors="replace")
    elif m := _CD_FILENAME.search(cd):
        name = m.group(1).strip()
    if not name:
        name = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    name = safe_filename(name or "", default="video")
    mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not Path(name).suffix and mime:
        ext = mimetypes.guess_extension(mime) or ""
        name += ext
    return name


def stream_to_file(resp: httpx.Response, dest: Path, *, max_bytes: int, progress=None) -> int:  # noqa: ANN001
    """Write a streaming body to ``dest`` enforcing ``max_bytes``; returns bytes written."""
    total = int(resp.headers.get("content-length") or 0) or None
    if total and total > max_bytes:
        raise ValidationFailed(f"The file is too large ({total} bytes; limit {max_bytes})", code="file_too_large")
    written = 0
    try:
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise ValidationFailed(f"The file exceeds the {max_bytes}-byte limit", code="file_too_large")
                fh.write(chunk)
                if progress:
                    progress(written / total if total else 0.0, f"Downloaded {written // (1024 * 1024)} MB")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written


def download_url(url: str, dest_dir: Path, *, max_bytes: int,
                 progress=None) -> tuple[Path, str, str | None]:  # noqa: ANN001
    """Download a validated public HTTPS link into ``dest_dir``.

    Returns ``(path, filename, mime_or_None)``. Redirects are followed manually (<= 5),
    re-validating each hop."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    current = url.strip()
    for _hop in range(MAX_REDIRECTS + 1):
        validate_import_url(current)
        with http.stream("GET", current, provider="cloud_import", allow_redirect_status=True) as resp:
            _check_peer(resp)
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location")
                if not location:
                    raise ValidationFailed("Redirect without a Location header", code="invalid_import_url")
                current = urljoin(current, location)
                continue
            mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower() or None
            if mime and mime.startswith("text/html"):
                raise ValidationFailed("The link returned a web page, not a video file. Use a direct "
                                       "download link (e.g. a share link with download enabled).",
                                       code="not_a_file")
            filename = filename_from_response(resp, current)
            dest = dest_dir / f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
            size = stream_to_file(resp, dest, max_bytes=max_bytes, progress=progress)
            log.info("Imported %s bytes from %s", size, urlsplit(current).hostname)
            return dest, filename, mime
    raise ValidationFailed(f"Too many redirects (more than {MAX_REDIRECTS})", code="too_many_redirects")
