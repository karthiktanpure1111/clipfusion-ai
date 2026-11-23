"""Google Drive v3: import a video the user owns/has access to (scope ``drive.readonly``)."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.core.errors import ValidationFailed
from app.core.security import safe_filename
from app.integrations import http
from app.integrations.cloud_import import stream_to_file

log = logging.getLogger(__name__)

FILES_URL = "https://www.googleapis.com/drive/v3/files"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


def parse_drive_file_id(value: str) -> str | None:
    """Accept a bare file id or a Drive share link (``/file/d/{id}/``, ``open?id=``, ``uc?id=``)."""
    text = (value or "").strip()
    if _ID_RE.match(text):
        return text
    parts = urlsplit(text)
    if (parts.hostname or "").lower() not in ("drive.google.com", "docs.google.com"):
        return None
    if m := re.search(r"/d/([A-Za-z0-9_-]{10,200})", parts.path):
        return m.group(1)
    candidate = (parse_qs(parts.query).get("id") or [None])[0]
    return candidate if candidate and _ID_RE.match(candidate) else None


def download_drive_file(access_token: str, file_id: str, dest_dir: Path, *, max_bytes: int,
                        progress=None) -> tuple[Path, str, str | None]:  # noqa: ANN001
    """Download a Drive video (``files.get`` metadata + ``alt=media``).

    Returns ``(path, filename, mime)``. Refuses non-video files and oversize files."""
    if not _ID_RE.match(file_id or ""):
        raise ValidationFailed("Invalid Google Drive file id", code="invalid_drive_file")
    auth = {"Authorization": f"Bearer {access_token}"}
    meta = http.request_json("GET", f"{FILES_URL}/{file_id}", provider="google_drive", headers=auth,
                             params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"})
    mime = str(meta.get("mimeType") or "")
    if not mime.startswith("video/"):
        raise ValidationFailed(f"'{meta.get('name')}' is not a video file ({mime or 'unknown type'}).",
                               code="not_a_video")
    size = int(meta.get("size") or 0)
    if size and size > max_bytes:
        raise ValidationFailed(f"The file is too large ({size} bytes; limit {max_bytes})", code="file_too_large")
    filename = safe_filename(str(meta.get("name") or ""), default="drive-video")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"
    with http.stream("GET", f"{FILES_URL}/{file_id}", provider="google_drive", headers=auth,
                     params={"alt": "media", "supportsAllDrives": "true"}) as resp:
        written = stream_to_file(resp, dest, max_bytes=max_bytes, progress=progress)
    log.info("Imported Drive file (%s bytes)", written)
    return dest, filename, mime
