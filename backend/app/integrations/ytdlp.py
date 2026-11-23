"""Download public YouTube videos with yt-dlp (opt-in per user: Settings → "Download from YouTube links").

The user has accepted that YouTube's Terms of Service do not allow downloading without YouTube's
permission. Only public videos are supported: no cookies, no account login, no members-only, private,
paid or age-restricted videos, and live streams must have ended. yt-dlp needs a JavaScript runtime
for YouTube's player code; Node.js (or Deno) on PATH is used.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.errors import DependencyMissing, ExternalServiceError, ValidationFailed

log = logging.getLogger(__name__)

QUALITY_HEIGHTS = {"2160": 2160, "1440": 1440, "1080": 1080, "720": 720, "480": 480}  # "best" = no limit
# highest resolution, then highest frame rate; among equals prefer H.264 (fastest to decode) and AAC audio
FORMAT_SORT = ["res", "fps", "vcodec:h264", "acodec:aac", "proto"]
_LABELS = {2160: "4K", 1440: "2K", 1080: "Full HD", 720: "HD"}
ProgressFn = Callable[[float, str | None], None]

# Errors the user can't fix by retrying (the job fails immediately with this message)
_PERMANENT_HINTS = (
    ("private video", "This video is private, so it can't be downloaded. Ask the creator for the file."),
    ("members-only", "This is a members-only video, so it can't be downloaded."),
    ("join this channel", "This is a members-only video, so it can't be downloaded."),
    ("sign in to confirm your age", "This video is age-restricted, so it can't be downloaded without signing in."),
    ("age-restricted", "This video is age-restricted, so it can't be downloaded without signing in."),
    ("video unavailable", "This video is unavailable (removed, private or blocked in your country)."),
    ("this live event will begin", "This live stream hasn't started yet."),
    ("is live", "This video is still live. Download it after the stream has ended."),
    ("requires payment", "This is a paid video, so it can't be downloaded."),
    ("copyright", "This video was removed for copyright reasons."),
)


class YouTubeDownloadRefused(ValidationFailed):
    """The video can't be downloaded (private, members-only, age-restricted, live ...)."""


def _js_runtimes() -> dict[str, dict[str, str]]:
    runtimes: dict[str, dict[str, str]] = {}
    for name in ("deno", "node"):
        path = shutil.which(name)
        if path:
            runtimes[name] = {"path": path}
    return runtimes


def available() -> bool:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def _ydl(extra: dict[str, Any]):
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - dependency is in pyproject
        raise DependencyMissing("yt-dlp is not installed (pip install yt-dlp yt-dlp-ejs).") from exc
    from app.core.ffmpeg import ffmpeg_bin

    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "js_runtimes": _js_runtimes(), "ffmpeg_location": str(Path(ffmpeg_bin()).parent),
        "socket_timeout": 30, "retries": 5, "fragment_retries": 10,
        "logger": _Logger(),
    }
    opts.update(extra)
    return yt_dlp.YoutubeDL(opts)


class _Logger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)


def _explain(exc: Exception) -> Exception:
    text = str(exc).lower()
    for needle, message in _PERMANENT_HINTS:
        if needle in text:
            return YouTubeDownloadRefused(message, code="youtube_download_refused")
    return ExternalServiceError(f"YouTube download failed: {str(exc)[:300]}", code="youtube_download_failed")


def _video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def fetch_info(video_id: str) -> dict[str, Any]:
    """Public metadata through yt-dlp (used when the YouTube Data API isn't available), in the same
    shape as :func:`app.integrations.youtube.fetch_video_metadata`."""
    try:
        with _ydl({"skip_download": True}) as ydl:
            info = ydl.extract_info(_video_url(video_id), download=False)
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises its own DownloadError types
        raise _explain(exc) from exc
    return {
        "video_id": video_id, "title": info.get("title") or "", "description": info.get("description") or "",
        "channel_id": info.get("channel_id"), "channel_title": info.get("channel") or info.get("uploader"),
        "published_at": info.get("upload_date"), "duration_seconds": info.get("duration"),
        "thumbnail_url": info.get("thumbnail"), "view_count": info.get("view_count"),
        "like_count": info.get("like_count"), "comment_count": info.get("comment_count"),
        "tags": info.get("tags") or [], "is_owner": False, "owner_account_id": None, "source": "yt-dlp",
        "is_live": bool(info.get("is_live")), "filesize_approx": info.get("filesize_approx"),
    }


def _format_spec(quality: str) -> str:
    height = QUALITY_HEIGHTS.get(str(quality))
    hf = f"[height<={height}]" if height else ""
    # SDR first (HDR would look washed out after processing), then any video, then a muxed file
    return f"bv*{hf}[dynamic_range=SDR]+ba/bv*{hf}+ba/b{hf}/b"


def _is_hdr(f: dict[str, Any]) -> bool:
    return str(f.get("dynamic_range") or "SDR").upper() != "SDR" or str(f.get("vcodec") or "").startswith(
        ("vp09.02", "vp9.2"))


def _codec_rank(vcodec: str) -> int:
    v = vcodec.lower()
    return 0 if v.startswith("avc1") else 1 if v.startswith(("vp09", "vp9")) else 2


def qualities_from_info(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Distinct downloadable qualities (resolution + frame rate), best first, with a size estimate."""
    duration = float(info.get("duration") or 0)
    formats = info.get("formats") or []
    audio = [f for f in formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    aac = [f for f in audio if str(f.get("acodec") or "").startswith("mp4a")] or audio  # the track we download
    audio_size = max((f.get("filesize") or f.get("filesize_approx") or 0 for f in aac), default=0)
    groups: dict[tuple[int, int], dict[str, Any]] = {}
    for f in formats:
        if f.get("vcodec") in (None, "none") or not f.get("height") or _is_hdr(f):
            continue
        height, fps = int(f["height"]), int(round(f.get("fps") or 30))
        size = f.get("filesize") or f.get("filesize_approx") or (
            (f.get("tbr") or 0) * 125 * duration if f.get("tbr") and duration else 0)
        cand = {"height": height, "fps": fps, "codec": str(f.get("vcodec") or "").split(".")[0],
                "size_bytes": int(size + audio_size) if size else None}
        cur = groups.get((height, fps))
        if cur is None or (_codec_rank(cand["codec"]), -(cand["size_bytes"] or 0)) < (
                _codec_rank(cur["codec"]), -(cur["size_bytes"] or 0)):
            groups[(height, fps)] = cand
    out = sorted(groups.values(), key=lambda q: (q["height"], q["fps"]), reverse=True)
    for q in out:
        tag = _LABELS.get(q["height"])
        q["label"] = f"{q['height']}p{q['fps']}" + (f" ({tag})" if tag else "")
        q["quality"] = str(q["height"])
    return [q for q in out if q["height"] >= 360]


def list_qualities(video_id: str) -> list[dict[str, Any]]:
    """The qualities this YouTube video can be downloaded in (one request to YouTube)."""
    try:
        with _ydl({"skip_download": True}) as ydl:
            info = ydl.extract_info(_video_url(video_id), download=False)
    except Exception as exc:  # noqa: BLE001
        raise _explain(exc) from exc
    return qualities_from_info(info)


def download(video_id: str, dest_dir: Path, *, quality: str = "best", max_bytes: int,
             progress: ProgressFn | None = None,
             ensure_space: Callable[[int], None] | None = None) -> tuple[Path, str, str]:
    """Download the video at ``quality`` ("best" = highest resolution and frame rate available, e.g.
    2160p60; or a maximum height such as "1080") into ``dest_dir``. Returns ``(path, filename, mime)``.
    ``ensure_space(bytes)`` is called with the expected size before anything is downloaded. Raises
    :class:`YouTubeDownloadRefused` for videos that can't be downloaded."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {"files": {}, "lock": threading.Lock(), "label": ""}

    def hook(d: dict[str, Any]) -> None:
        if d.get("status") != "downloading" or progress is None:
            return
        with state["lock"]:
            state["files"][d.get("filename")] = (d.get("downloaded_bytes") or 0,
                                                 d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            done = sum(a for a, _ in state["files"].values())
            total = sum(b for _, b in state["files"].values())
        if total:
            speed = d.get("speed") or 0
            progress(min(done / total, 1.0) * 0.98,
                     f"Downloading {state['label']} from YouTube: {done / 1e6:.0f} / {total / 1e6:.0f} MB"
                     + (f" at {speed / 1e6:.1f} MB/s" if speed else ""))

    opts = {"format": _format_spec(quality), "format_sort": FORMAT_SORT, "merge_output_format": "mp4",
            "outtmpl": str(dest_dir / "%(id)s.%(ext)s"), "progress_hooks": [hook], "max_filesize": max_bytes}
    try:
        with _ydl(opts) as ydl:
            info = ydl.extract_info(_video_url(video_id), download=False)
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise YouTubeDownloadRefused("This video is still live (or hasn't started). Download it after the "
                                             "stream has ended.", code="youtube_download_refused")
            chosen = info.get("requested_formats") or [info]
            video = next((f for f in chosen if f.get("vcodec") not in (None, "none")), chosen[0])
            state["label"] = f"{video.get('height') or '?'}p{int(round(video.get('fps') or 0)) or ''}"
            expected = sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in chosen)
            if ensure_space is not None and expected:
                ensure_space(expected)
            log.info("YouTube %s: downloading %s (%s)", video_id, state["label"], video.get("vcodec"))
            info = ydl.process_ie_result(info, download=True)
    except YouTubeDownloadRefused:
        raise
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ == "InsufficientDiskSpace":
            raise
        raise _explain(exc) from exc
    files = [p for p in dest_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
    if not files:
        raise ExternalServiceError("YouTube download finished without a video file (it may be too large for "
                                   "MAX_UPLOAD_BYTES).", code="youtube_download_failed")
    path = max(files, key=lambda p: p.stat().st_size)
    title = (info or {}).get("title") or video_id
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:120] or video_id
    log.info("Downloaded YouTube %s at %s (%d MB)", video_id, state["label"], path.stat().st_size // 1_000_000)
    return path, f"{safe}{path.suffix}", "video/mp4"

