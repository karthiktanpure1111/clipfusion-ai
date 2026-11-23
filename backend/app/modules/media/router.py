"""GET /media/{token} — serve a locally stored file behind a short-lived HMAC-signed token.

No auth header is needed (``<video src>``/``<img src>`` can't send one); the token itself
is the capability. Supports a single HTTP byte range (206 / 416) for video seeking.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from app.core.errors import NotFoundError
from app.core.security import safe_filename, verify_signed_payload
from app.core.storage import MEDIA_TOKEN_PURPOSE, LocalStorage, get_storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/media", tags=["media"])

BLOCK = 1024 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_EXTRA_TYPES = {".mp4": "video/mp4", ".m4v": "video/x-m4v", ".mov": "video/quicktime", ".webm": "video/webm",
                ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp", ".ttf": "font/ttf", ".otf": "font/otf",
                ".woff": "font/woff", ".woff2": "font/woff2", ".wav": "audio/wav", ".json": "application/json"}


def _content_type(path: Path) -> str:
    return _EXTRA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _resolve(token: str) -> tuple[Path, str | None]:
    payload = verify_signed_payload(token, MEDIA_TOKEN_PURPOSE)
    key = payload.get("k")
    storage = get_storage()
    if not isinstance(key, str) or not key or not isinstance(storage, LocalStorage):
        raise NotFoundError("File not found")
    try:
        path = storage.local_path(key)  # raises ValueError for keys escaping the storage root
    except ValueError as exc:
        raise NotFoundError("File not found") from exc
    if path is None or not path.is_file():
        raise NotFoundError("File not found")
    dn = payload.get("dn")
    return path, (dn if isinstance(dn, str) and dn else None)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """Parse a single ``bytes=`` range. Returns ``(start, end)`` inclusive, ``None`` when the
    header is absent/ignored (multi-range or malformed -> full content) or ``False`` when
    the range is unsatisfiable."""
    if not header:
        return None
    m = _RANGE_RE.match(header.strip().replace(" ", ""))
    if not m:
        return None
    a, b = m.groups()
    if not a and not b:
        return None
    if not a:  # suffix range: last N bytes
        n = int(b)
        if n == 0 or size == 0:
            return False
        return max(0, size - n), size - 1
    start = int(a)
    end = int(b) if b else size - 1
    if start >= size or (b and end < start):
        return False
    return start, min(end, size - 1)


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            block = fh.read(min(BLOCK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


@router.head("/{token}", include_in_schema=False)
@router.get("/{token}")
def get_media(token: str, request: Request) -> Response:
    """Stream a signed media file (Range supported; ``Content-Disposition: attachment`` for downloads)."""
    path, download_name = _resolve(token)
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    if download_name:
        name = safe_filename(download_name, default=path.name)
        headers["Content-Disposition"] = (f'attachment; filename="{name}"; '
                                          f"filename*=UTF-8''{quote(download_name, safe='')}")
    ctype = _content_type(path)
    rng = parse_range(request.headers.get("range"), size)
    if rng is False:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status_code=416, headers=headers)
    if rng is None:
        start, end, status = 0, size - 1, 200
    else:
        start, end = rng  # type: ignore[misc]
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=ctype)
    return StreamingResponse(_iter_file(path, start, length), status_code=status, headers=headers, media_type=ctype)
