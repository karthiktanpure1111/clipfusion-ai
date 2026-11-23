"""YouTube resumable upload (``videos.insert``), processing status and statistics.

Protocol (https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol):
1. ``POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`` with the
   video resource as JSON and ``X-Upload-Content-Length/Type`` -> session URI (Location).
2. ``PUT`` chunks (multiples of 256 KiB) with ``Content-Range: bytes a-b/total``;
   ``308 Resume Incomplete`` + ``Range: bytes=0-N`` acknowledges progress; 200/201 = done.
3. After an interruption ``PUT`` with ``Content-Range: bytes */total`` reports the
   persisted offset so the upload resumes instead of restarting.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations import http
from app.integrations.http import ProviderError

log = logging.getLogger(__name__)

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
CHUNK_GRANULARITY = 256 * 1024
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_TOTAL_MAX = 500
CATEGORY_PEOPLE_BLOGS = "22"
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d+)")
_UPLOAD_TIMEOUT = httpx.Timeout(300.0, connect=15.0)


class UploadSessionExpired(ProviderError):
    """The resumable session URI is no longer valid (404/410); start a new session."""


def _clean_text(text: str) -> str:
    # YouTube rejects '<' and '>' in titles/descriptions.
    return (text or "").replace("<", "").replace(">", "").strip()


def _tag_cost(tag: str) -> int:
    # YouTube counts quotes around tags containing spaces, plus a separating comma.
    return len(tag) + (2 if " " in tag else 0)


def limit_tags(tags: list[str]) -> list[str]:
    """Deduplicate tags and keep them within YouTube's 500-character total."""
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in tags or []:
        tag = _clean_text(str(raw)).lstrip("#").replace(",", " ").strip()[:100]
        if not tag or tag.lower() in seen:
            continue
        cost = _tag_cost(tag) + (1 if out else 0)
        if total + cost > TAGS_TOTAL_MAX:
            break
        out.append(tag)
        seen.add(tag.lower())
        total += cost
    return out


def build_video_resource(payload: dict[str, Any], *, default_title: str = "Untitled clip") -> dict[str, Any]:
    """Build the ``videos.insert`` resource from a queue payload snapshot."""
    title = _clean_text(payload.get("title") or "") or _clean_text(default_title) or "Untitled clip"
    description = _clean_text(payload.get("description") or "")
    hashtags = [h.lstrip("#").strip() for h in payload.get("hashtags") or [] if str(h).strip()]
    missing = [f"#{h}" for h in hashtags if f"#{h}".lower() not in description.lower()]
    if missing:
        description = (description + "\n\n" + " ".join(missing)).strip()
    privacy = payload.get("privacy") or "public"
    if privacy not in ("public", "unlisted", "private"):
        privacy = "private"
    return {
        "snippet": {"title": title[:TITLE_MAX], "description": description[:DESCRIPTION_MAX],
                    "tags": limit_tags(list(payload.get("tags") or [])),
                    "categoryId": str(payload.get("category_id") or CATEGORY_PEOPLE_BLOGS)},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(payload.get("made_for_kids", False)),
                   "containsSyntheticMedia": False},
    }


def _chunk_size(requested: int | None) -> int:
    size = requested or get_settings().youtube_upload_chunk_bytes
    return max(CHUNK_GRANULARITY, (size // CHUNK_GRANULARITY) * CHUNK_GRANULARITY)


class YouTubeUploader:
    """Resumable uploads + status/statistics for one OAuth access token."""

    def __init__(self, access_token: str, *, chunk_bytes: int | None = None, max_retries: int = 5) -> None:
        self._auth = {"Authorization": f"Bearer {access_token}"}
        self.chunk_bytes = _chunk_size(chunk_bytes)
        self.max_retries = max_retries

    # ------------------------------------------------------------- session
    def start_session(self, total: int, resource: dict[str, Any], content_type: str = "video/mp4") -> str:
        """Initiate a resumable session; returns the session URI."""
        headers = {**self._auth, "Content-Type": "application/json; charset=UTF-8",
                   "X-Upload-Content-Length": str(total), "X-Upload-Content-Type": content_type}
        resp = http.request("POST", UPLOAD_URL, provider="youtube", headers=headers, json=resource,
                            params={"uploadType": "resumable", "part": "snippet,status"}, retries=3)
        location = resp.headers.get("location")
        if not location:
            raise ProviderError("youtube", "Upload session was not created (no Location header)",
                                status=resp.status_code)
        return location

    def query_offset(self, session_url: str, total: int) -> tuple[int, dict[str, Any] | None]:
        """Ask how many bytes the server has. Returns ``(offset, video_or_None_if_incomplete)``."""
        headers = {**self._auth, "Content-Range": f"bytes */{total}", "Content-Length": "0"}
        try:
            resp = http.request("PUT", session_url, provider="youtube", headers=headers, content=b"",
                                ok_statuses={308}, retries=3)
        except ProviderError as exc:
            if exc.status in (404, 410):
                raise UploadSessionExpired("youtube", "Upload session expired", status=exc.status) from exc
            raise
        if resp.status_code == 308:
            return _next_offset(resp), None
        return total, resp.json()

    # -------------------------------------------------------------- upload
    def upload(self, path: Path, resource: dict[str, Any], *, session_url: str | None = None,
               on_session: Callable[[str], None] | None = None,
               progress: Callable[[float, str | None], None] | None = None,
               content_type: str = "video/mp4") -> dict[str, Any]:
        """Upload ``path`` (resuming ``session_url`` when given). Returns the video resource.

        ``on_session`` is called with every new session URI so callers can persist it."""
        total = path.stat().st_size
        offset = 0
        if session_url:
            try:
                offset, done = self.query_offset(session_url, total)
                if done is not None:
                    return done
                log.info("Resuming YouTube upload at byte %s/%s", offset, total)
            except UploadSessionExpired:
                log.info("YouTube upload session expired; starting a new one")
                session_url = None
        if not session_url:
            session_url = self.start_session(total, resource, content_type)
            if on_session:
                on_session(session_url)
        return self._send_chunks(path, session_url, total, offset, progress)

    def _send_chunks(self, path: Path, session_url: str, total: int, offset: int,
                     progress: Callable[[float, str | None], None] | None) -> dict[str, Any]:
        failures = 0
        with path.open("rb") as fh:
            while True:
                fh.seek(offset)
                chunk = fh.read(self.chunk_bytes)
                end = offset + len(chunk) - 1
                headers = {**self._auth, "Content-Length": str(len(chunk)),
                           "Content-Range": f"bytes {offset}-{end}/{total}" if chunk else f"bytes */{total}"}
                try:
                    resp = http.request("PUT", session_url, provider="youtube", headers=headers,
                                        content=chunk, ok_statuses={308}, retries=0, timeout=_UPLOAD_TIMEOUT)
                except ProviderError as exc:
                    if exc.status in (404, 410):
                        raise UploadSessionExpired("youtube", "Upload session expired", status=exc.status) from exc
                    if not exc.transient or failures >= self.max_retries:
                        raise
                    failures += 1
                    http._sleep(min(60.0, 2.0 ** failures))
                    offset, done = self.query_offset(session_url, total)
                    if done is not None:
                        return done
                    continue
                if resp.status_code == 308:
                    offset = _next_offset(resp)
                    failures = 0
                    if progress:
                        progress(offset / total if total else 1.0, f"Uploaded {offset}/{total} bytes")
                    continue
                if progress:
                    progress(1.0, "Upload complete")
                return resp.json()

    # ---------------------------------------------------------- status/stats
    def get_status(self, video_id: str) -> dict[str, Any] | None:
        """``videos.list part=status,processingDetails`` (1 unit). None when the video is gone."""
        body = http.request_json("GET", VIDEOS_URL, provider="youtube", headers=self._auth,
                                 params={"part": "status,processingDetails", "id": video_id})
        items = body.get("items") or []
        if not items:
            return None
        status = items[0].get("status") or {}
        proc = items[0].get("processingDetails") or {}
        return {"upload_status": status.get("uploadStatus"), "privacy_status": status.get("privacyStatus"),
                "failure_reason": status.get("failureReason"), "rejection_reason": status.get("rejectionReason"),
                "processing_status": proc.get("processingStatus"),
                "processing_failure_reason": proc.get("processingFailureReason")}


def _next_offset(resp: httpx.Response) -> int:
    match = _RANGE_RE.search(resp.headers.get("range", ""))
    return int(match.group(2)) + 1 if match else 0
