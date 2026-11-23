"""YouTube Data API v3 + YouTube Analytics API v2 (official REST endpoints via httpx).

Metadata lookups use the user's connected Google account when available, otherwise the
optional server API key (``YOUTUBE_API_KEY``). Audience retention is only available to
the channel owner through the Analytics API (scope ``yt-analytics.readonly``).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.errors import (
    AppError,
    IntegrationNotConfigured,
    NotFoundError,
    PermissionDenied,
    ValidationFailed,
)
from app.integrations import http, quota
from app.integrations.google_oauth import SCOPE_YOUTUBE_READONLY, SCOPE_YT_ANALYTICS
from app.integrations.http import ProviderError
from app.integrations.youtube_upload import YouTubeUploader  # noqa: F401  (public re-export)
from app.models import ConnectedAccount, User
from app.models.enums import Platform
from app.schemas.media import RetentionPoint

log = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
             "www.youtube-nocookie.com"}
_PATH_PREFIXES = ("shorts", "live", "embed", "v", "e")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_video_id(url: str) -> str | None:
    """Extract an 11-character video id from any common YouTube URL form (or a bare id)."""
    if not url:
        return None
    text = url.strip()
    if _ID_RE.match(text):
        return text
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    candidate: str | None = None
    if host in ("youtu.be", "www.youtu.be"):
        candidate = segments[0] if segments else None
    elif host in _YT_HOSTS:
        if parts.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parts.query).get("v") or [None])[0]
        elif len(segments) >= 2 and segments[0] in _PATH_PREFIXES:
            candidate = segments[1]
    return candidate if candidate and _ID_RE.match(candidate) else None


def parse_iso8601_duration(value: str | None) -> float:
    """``PT1H2M3S`` -> 3723.0 (``P0D`` / unknown -> 0.0)."""
    m = _DURATION_RE.match(value or "")
    if not m:
        return 0.0
    days, hours, minutes, seconds = m.groups()
    return (int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60
            + float(seconds or 0))


def _best_thumbnail(snippet: dict[str, Any]) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        if isinstance(thumbs.get(key), dict) and thumbs[key].get("url"):
            return thumbs[key]["url"]
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def api_get(path: str, *, params: dict[str, Any], access_token: str | None = None,
            api_key: str | None = None) -> dict[str, Any]:
    """GET a Data API resource with either OAuth bearer auth or an API key."""
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
    if api_key and not access_token:
        params = {**params, "key": api_key}
    return http.request_json("GET", f"{API_BASE}/{path}", provider="youtube", params=params, headers=headers)


def fetch_my_channel(access_token: str) -> dict[str, Any]:
    """``channels.list(mine=true)`` -> channel identity for a freshly connected account."""
    body = api_get("channels", params={"part": "snippet,contentDetails", "mine": "true"},
                   access_token=access_token)
    items = body.get("items") or []
    if not items:
        raise ValidationFailed("This Google account has no YouTube channel. Create a channel on YouTube "
                               "first, then connect again.", code="youtube_no_channel")
    ch = items[0]
    snippet = ch.get("snippet") or {}
    uploads = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    return {"channel_id": ch.get("id"), "title": snippet.get("title") or "",
            "custom_url": snippet.get("customUrl"), "thumbnail_url": _best_thumbnail(snippet),
            "uploads_playlist_id": uploads}


def _user_youtube_accounts(db: Session, user: User) -> list[ConnectedAccount]:
    return list(db.scalars(select(ConnectedAccount).where(
        ConnectedAccount.user_id == user.id, ConnectedAccount.platform == Platform.YOUTUBE)))


def _metadata_auth(db: Session, user: User) -> tuple[str | None, str | None, str]:
    """Return ``(access_token, api_key, quota_scope)`` for a metadata lookup."""
    from app.modules.accounts.tokens import find_google_account, get_access_token

    settings = get_settings()
    account = find_google_account(db, user, need_scope=SCOPE_YOUTUBE_READONLY)
    if account is not None:
        try:
            return get_access_token(db, account), None, str(account.id)
        except AppError as exc:
            if not settings.youtube_api_key:
                raise
            log.info("Falling back to API key for metadata lookup: %s", exc.message)
    if settings.youtube_api_key:
        return None, settings.youtube_api_key, "global"
    raise IntegrationNotConfigured(
        "YouTube metadata lookup needs either a connected YouTube account (Connected Accounts > "
        "Connect YouTube) or a server API key (set YOUTUBE_API_KEY with the YouTube Data API v3 enabled).",
        details={"missing_env": ["YOUTUBE_API_KEY"], "alternative": "connect_youtube_account"})


def fetch_video_metadata(db: Session, user: User, video_id: str) -> dict[str, Any]:
    """``videos.list`` (1 quota unit) -> normalised metadata dict (see ARCHITECTURE §3.3)."""
    if not _ID_RE.match(video_id or ""):
        raise ValidationFailed("Invalid YouTube video id")
    token, key, scope = _metadata_auth(db, user)
    body = api_get("videos", params={"part": "snippet,contentDetails,statistics,status", "id": video_id},
                   access_token=token, api_key=key)
    quota.record_usage(db, quota.YOUTUBE, scope, quota.COST_LIST)
    items = body.get("items") or []
    if not items:
        raise NotFoundError("YouTube video not found, private, or not accessible with these credentials.")
    item = items[0]
    snippet = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    channel_id = snippet.get("channelId")
    owner = next((a for a in _user_youtube_accounts(db, user) if a.external_account_id == channel_id), None)
    return {
        "video_id": item.get("id") or video_id,
        "title": snippet.get("title") or "",
        "description": snippet.get("description") or "",
        "channel_id": channel_id,
        "channel_title": snippet.get("channelTitle") or "",
        "published_at": snippet.get("publishedAt"),
        "duration_seconds": parse_iso8601_duration((item.get("contentDetails") or {}).get("duration")),
        "thumbnail_url": _best_thumbnail(snippet),
        "view_count": _int_or_none(stats.get("viewCount")),
        "like_count": _int_or_none(stats.get("likeCount")),
        "comment_count": _int_or_none(stats.get("commentCount")),
        "tags": list(snippet.get("tags") or []),
        "is_owner": owner is not None,
        "owner_account_id": str(owner.id) if owner else None,
    }


def fetch_retention_curve(db: Session, account: ConnectedAccount, video_id: str) -> list[RetentionPoint]:
    """Real audience retention (owner only) from the YouTube Analytics API."""
    from app.modules.accounts.tokens import get_access_token

    if account.platform != Platform.YOUTUBE:
        raise ValidationFailed("Retention data is only available for YouTube accounts")
    if SCOPE_YT_ANALYTICS not in (account.scopes or []):
        raise PermissionDenied("This YouTube connection was made without the YouTube Analytics permission. "
                               "Reconnect the account and allow 'View YouTube Analytics reports'.",
                               code="missing_scope")
    token = get_access_token(db, account)
    body = api_get("videos", params={"part": "snippet", "id": video_id}, access_token=token)
    quota.record_usage(db, quota.YOUTUBE, str(account.id), quota.COST_LIST)
    items = body.get("items") or []
    if not items:
        raise NotFoundError("YouTube video not found or not accessible")
    snippet = items[0].get("snippet") or {}
    if snippet.get("channelId") != account.external_account_id:
        raise PermissionDenied("Audience retention is only available for videos on your own connected channel "
                               "(YouTube Analytics API restricts it to the channel owner).", code="not_owner")
    published = (snippet.get("publishedAt") or "")[:10] or "2005-04-23"
    params = {"ids": "channel==MINE", "metrics": "audienceWatchRatio,relativeRetentionPerformance",
              "dimensions": "elapsedVideoTimeRatio", "filters": f"video=={video_id}",
              "startDate": published, "endDate": date.today().isoformat()}
    try:
        report = http.request_json("GET", ANALYTICS_URL, provider="youtube_analytics", params=params,
                                   headers={"Authorization": f"Bearer {token}"})
    except ProviderError as exc:
        if exc.status in (401, 403):
            raise PermissionDenied(f"YouTube Analytics refused the request: {exc.provider_message}",
                                   code="analytics_forbidden") from exc
        raise
    return _parse_retention(report)


def _parse_retention(report: dict[str, Any]) -> list[RetentionPoint]:
    headers = [h.get("name") for h in report.get("columnHeaders") or []]
    try:
        i_ratio = headers.index("elapsedVideoTimeRatio")
        i_watch = headers.index("audienceWatchRatio")
    except ValueError:
        return []
    i_rel = headers.index("relativeRetentionPerformance") if "relativeRetentionPerformance" in headers else None
    points = []
    for row in report.get("rows") or []:
        rel = row[i_rel] if i_rel is not None and row[i_rel] is not None else None
        points.append(RetentionPoint(ratio=float(row[i_ratio]), audience_watch_ratio=float(row[i_watch]),
                                     relative_performance=float(rel) if rel is not None else None))
    points.sort(key=lambda p: p.ratio)
    return points


def fetch_video_statistics(access_token: str, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    """``videos.list part=statistics`` for up to 50 ids -> ``{id: {views, likes, comments, raw}}``."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        body = api_get("videos", params={"part": "statistics", "id": ",".join(batch)}, access_token=access_token)
        for item in body.get("items") or []:
            stats = item.get("statistics") or {}
            out[item["id"]] = {"views": _int_or_none(stats.get("viewCount")),
                               "likes": _int_or_none(stats.get("likeCount")),
                               "comments": _int_or_none(stats.get("commentCount")),
                               "raw": stats, "fetched_at": utcnow().isoformat()}
    return out
