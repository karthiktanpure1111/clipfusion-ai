"""Instagram Graph API: Reels publishing (resumable upload) and media insights.

Publishing flow (official content-publishing API):
1. ``POST /{ig_user_id}/media`` media_type=REELS, upload_type=resumable -> container id
2. ``POST https://rupload.facebook.com/ig-api-upload/{ver}/{container_id}`` with the bytes
3. poll ``GET /{container_id}?fields=status_code,status`` until FINISHED
4. ``POST /{ig_user_id}/media_publish`` creation_id -> media id
5. ``GET /{media_id}?fields=permalink,shortcode``
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations import http
from app.integrations.http import ProviderError
from app.integrations.meta_oauth import FLOW_INSTAGRAM

log = logging.getLogger(__name__)

RUPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload"
CAPTION_MAX = 2200
HASHTAGS_MAX = 30
INSIGHT_METRICS = ("views", "reach", "likes", "comments", "shares", "saved")
_HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)
_UPLOAD_TIMEOUT = httpx.Timeout(600.0, connect=15.0)
CONTAINER_TERMINAL = {"FINISHED", "ERROR", "EXPIRED", "PUBLISHED"}


def build_caption(caption: str, hashtags: list[str] | None) -> str:
    """Caption + hashtags within Instagram's limits (<= 30 hashtags, <= 2200 characters)."""
    text = (caption or "").strip()
    existing = {h.lower() for h in _HASHTAG_RE.findall(text)}
    room = max(0, HASHTAGS_MAX - len(existing))
    tags: list[str] = []
    for raw in hashtags or []:
        tag = "#" + re.sub(r"[^\w]", "", str(raw).lstrip("#"), flags=re.UNICODE)
        if len(tag) < 2 or tag.lower() in existing or len(tags) >= room:
            continue
        existing.add(tag.lower())
        tags.append(tag)
    if len(text) > CAPTION_MAX:
        text = text[:CAPTION_MAX].rstrip()
    while tags and len(text) + 2 + len(" ".join(tags)) > CAPTION_MAX:
        tags.pop()
    return f"{text}\n\n{' '.join(tags)}".strip() if tags else text


class InstagramClient:
    """Graph API client for one professional account (``ig_user_id``) and token."""

    def __init__(self, access_token: str, ig_user_id: str, *, flow: str = FLOW_INSTAGRAM) -> None:
        self.token = access_token
        self.ig_user_id = ig_user_id
        self.version = get_settings().meta_graph_version
        host = "graph.instagram.com" if flow == FLOW_INSTAGRAM else "graph.facebook.com"
        self.base = f"https://{host}/{self.version}"
        self.provider = "instagram"

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return http.request_json("GET", f"{self.base}/{path}", provider=self.provider,
                                 params={**params, "access_token": self.token})

    def _post(self, path: str, **data: Any) -> dict[str, Any]:
        return http.request_json("POST", f"{self.base}/{path}", provider=self.provider,
                                 data={**data, "access_token": self.token}, retries=1)

    # ------------------------------------------------------------ publishing
    def publishing_limit(self) -> dict[str, int | None]:
        """``content_publishing_limit`` -> ``{"quota_usage", "quota_total", "quota_duration"}``."""
        body = self._get(f"{self.ig_user_id}/content_publishing_limit", fields="config,quota_usage")
        row = (body.get("data") or [{}])[0]
        config = row.get("config") or {}
        return {"quota_usage": int(row.get("quota_usage") or 0),
                "quota_total": int(config["quota_total"]) if config.get("quota_total") is not None else None,
                "quota_duration": config.get("quota_duration")}

    def create_reel_container(self, caption: str, *, share_to_feed: bool = True,
                              thumb_offset_ms: int | None = None) -> str:
        """Create a resumable REELS container; returns its id."""
        data: dict[str, Any] = {"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                                "share_to_feed": "true" if share_to_feed else "false"}
        if thumb_offset_ms is not None:
            data["thumb_offset"] = str(int(thumb_offset_ms))
        body = self._post(f"{self.ig_user_id}/media", **data)
        if not body.get("id"):
            raise ProviderError(self.provider, "Container was not created (no id returned)")
        return str(body["id"])

    def upload_video(self, container_id: str, path: Path) -> None:
        """Upload the whole file to the container through rupload (offset 0)."""
        size = path.stat().st_size

        def body() -> Any:
            def gen() -> Any:
                with path.open("rb") as fh:
                    while chunk := fh.read(4 * 1024 * 1024):
                        yield chunk
            return gen()

        headers = {"Authorization": f"OAuth {self.token}", "offset": "0", "file_size": str(size),
                   "Content-Type": "application/octet-stream", "Content-Length": str(size)}
        resp = http.request("POST", f"{RUPLOAD_BASE}/{self.version}/{container_id}", provider=self.provider,
                            headers=headers, content=body, retries=2, timeout=_UPLOAD_TIMEOUT)
        try:
            result = resp.json()
        except ValueError:
            result = {}
        if isinstance(result, dict) and result.get("success") is False:
            raise ProviderError(self.provider, str(result.get("message") or "Upload rejected"),
                                status=resp.status_code)

    def container_status(self, container_id: str) -> tuple[str, str | None]:
        """``(status_code, status)`` e.g. ``("IN_PROGRESS", None)`` / ``("ERROR", "Error: ...")``."""
        body = self._get(container_id, fields="status_code,status")
        return str(body.get("status_code") or "IN_PROGRESS"), body.get("status")

    def publish(self, container_id: str) -> str:
        body = self._post(f"{self.ig_user_id}/media_publish", creation_id=container_id)
        if not body.get("id"):
            raise ProviderError(self.provider, "Publish did not return a media id")
        return str(body["id"])

    def media_info(self, media_id: str) -> dict[str, Any]:
        return self._get(media_id, fields="permalink,shortcode,timestamp")

    # -------------------------------------------------------------- insights
    def insights(self, media_id: str) -> dict[str, Any]:
        """Real metrics for a published Reel. Unsupported metrics are skipped individually."""
        values: dict[str, int | None] = {}
        errors: dict[str, str] = {}
        try:
            values.update(self._insight_values(media_id, ",".join(INSIGHT_METRICS)))
        except ProviderError as exc:
            if exc.status not in (400, 403):  # unsupported metric / missing insights permission
                raise
            for metric in INSIGHT_METRICS:
                try:
                    values.update(self._insight_values(media_id, metric))
                except ProviderError as inner:
                    if inner.status not in (400, 403):
                        raise
                    errors[metric] = inner.provider_message
        fields = self._get(media_id, fields="like_count,comments_count")
        values.setdefault("likes", _int(fields.get("like_count")))
        values.setdefault("comments", _int(fields.get("comments_count")))
        return {"views": values.get("views"), "reach": values.get("reach"), "likes": values.get("likes"),
                "comments": values.get("comments"), "shares": values.get("shares"),
                "saves": values.get("saved"), "unsupported": errors, "raw": {**values, **fields}}

    def _insight_values(self, media_id: str, metrics: str) -> dict[str, int | None]:
        body = self._get(f"{media_id}/insights", metric=metrics)
        out: dict[str, int | None] = {}
        for row in body.get("data") or []:
            value = None
            if isinstance(row.get("total_value"), dict):
                value = row["total_value"].get("value")
            elif row.get("values"):
                value = row["values"][0].get("value")
            out[str(row.get("name"))] = _int(value)
        return out


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
