"""Media probing, file validation and frame extraction (FFprobe / FFmpeg).

``probe_media`` returns a :class:`~app.schemas.media.MediaInfo` with the *display*
geometry (width/height swapped for +-90 degree rotations). ``MediaInfo.rotation`` is the
clockwise rotation, in degrees, a player applies to display the stored frames (the legacy
``rotate`` tag convention: a phone portrait recording is usually ``90``).

``validate_video_file`` is the gate every uploaded/imported original passes through:
magic-byte sniffing, container structure (truncation) check, FFprobe, duration limits and a
real decode of the first and last frames.
"""

from __future__ import annotations

import json
import logging
import re
import struct
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.ffmpeg import ffmpeg_bin, ffprobe_bin, run
from app.schemas.media import MediaInfo

log = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 120
FRAME_TIMEOUT_S = 120

BROWSER_CONTAINERS = {"mp4", "mov", "m4v", "webm"}
BROWSER_VIDEO_CODECS = {"vp8", "vp9", "av1"}
BROWSER_H264_PIX_FMTS = {"yuv420p", "yuvj420p"}
BROWSER_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "flac"}

_QUICKTIME_ATOMS = {b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_MKV_SEGMENT_ID = b"\x18\x53\x80\x67"


# ----------------------------------------------------------------- helpers
def _parse_rate(value: str | None) -> float:
    """Parse an FFprobe rational (``"30000/1001"``) or decimal string; ``0`` if unknown."""
    if not value:
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except ValueError:
        return 0.0


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _clockwise_rotation(stream: dict) -> int:
    """Clockwise display rotation (0/90/180/270) from the display matrix or ``rotate`` tag.

    FFprobe reports the display-matrix angle counter-clockwise (``rotation: -90`` for a
    stream that must be turned 90 degrees clockwise), the legacy tag is clockwise."""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            angle = _to_float(side.get("rotation"))
            if angle is not None:
                return int(round(-angle / 90.0)) * 90 % 360
    tag = _to_float((stream.get("tags") or {}).get("rotate"))
    if tag is not None:
        return int(round(tag / 90.0)) * 90 % 360
    return 0


def _container(fmt_name: str, path: Path, tags: dict) -> str:
    names = {n.strip() for n in fmt_name.split(",") if n.strip()}
    ext = path.suffix.lower()
    if names & {"mov", "mp4"}:
        brand = str(tags.get("major_brand", "")).strip().lower()
        if ext == ".mov" or brand == "qt":
            return "mov"
        if ext == ".m4v" or brand.startswith("m4v"):
            return "m4v"
        return "mp4"
    if names & {"matroska", "webm"}:
        return "webm" if ext == ".webm" else "mkv"
    if "avi" in names:
        return "avi"
    return next(iter(sorted(names)), "") or ext.lstrip(".")


def _browser_playable(container: str, vcodec: str | None, pix_fmt: str | None, acodec: str | None) -> bool:
    if container not in BROWSER_CONTAINERS or not vcodec:
        return False
    if container == "webm" and vcodec not in BROWSER_VIDEO_CODECS:
        return False
    video_ok = vcodec in BROWSER_VIDEO_CODECS or (vcodec == "h264" and (pix_fmt or "") in BROWSER_H264_PIX_FMTS)
    audio_ok = acodec is None or acodec in BROWSER_AUDIO_CODECS
    return video_ok and audio_ok


def _is_cover_art(stream: dict) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


# ------------------------------------------------------------- raw probing
def _ffprobe_json(path: Path) -> dict:
    exe = ffprobe_bin()
    if exe is None:
        return _ffmpeg_info_json(path)
    proc = run([exe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
               timeout=PROBE_TIMEOUT_S)
    try:
        return json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FFprobe returned invalid JSON for {path.name}") from exc


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_BITRATE_RE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")
_INPUT_RE = re.compile(r"Input #0,\s*(.+?),\s*from")
_STREAM_RE = re.compile(r"Stream #0:\d+[^:]*:\s*(Video|Audio):\s*(\w+)(.*)")
_SIZE_RE = re.compile(r",\s*(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_PIX_RE = re.compile(r",\s*(yuv\w+|yuvj\w+|nv12|nv21|rgb\w+|bgr\w+|gray\w*|p010\w*)")
_HZ_RE = re.compile(r"(\d+)\s*Hz")
_ROT_RE = re.compile(r"rotation of\s*(-?\d+(?:\.\d+)?)\s*degrees")


def _ffmpeg_info_json(path: Path) -> dict:
    """Fallback when FFprobe is unavailable: parse ``ffmpeg -i`` output into FFprobe-like JSON."""
    proc = run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], check=False, timeout=PROBE_TIMEOUT_S)
    text = (proc.stderr or b"").decode("utf-8", "replace")
    m_in = _INPUT_RE.search(text)
    if not m_in:
        raise RuntimeError(f"FFmpeg could not read {path.name}: {text[-500:]}")
    fmt: dict[str, Any] = {"format_name": m_in.group(1)}
    if m := _DURATION_RE.search(text):
        fmt["duration"] = str(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
    if m := _BITRATE_RE.search(text):
        fmt["bit_rate"] = str(int(m.group(1)) * 1000)
    streams: list[dict] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _STREAM_RE.search(line)
        if not m:
            continue
        kind, codec, rest = m.group(1).lower(), m.group(2), m.group(3)
        stream: dict[str, Any] = {"codec_type": kind, "codec_name": codec}
        if kind == "video":
            if "(attached pic)" in rest:
                stream["disposition"] = {"attached_pic": 1}
            if sz := _SIZE_RE.search(rest):
                stream["width"], stream["height"] = int(sz.group(1)), int(sz.group(2))
            if fps := _FPS_RE.search(rest):
                stream["avg_frame_rate"] = fps.group(1)
            if pix := _PIX_RE.search(rest):
                stream["pix_fmt"] = pix.group(1)
            for follow in lines[i + 1:i + 6]:
                if rot := _ROT_RE.search(follow):
                    stream["side_data_list"] = [{"rotation": float(rot.group(1))}]
                    break
                if _STREAM_RE.search(follow):
                    break
        elif hz := _HZ_RE.search(rest):
            stream["sample_rate"] = hz.group(1)
        streams.append(stream)
    return {"streams": streams, "format": fmt}


# --------------------------------------------------------------- public API
def probe_media(path: Path) -> MediaInfo:
    """Probe ``path`` with FFprobe and return its :class:`MediaInfo`.

    Audio-only files are supported (``width = height = 0``, ``fps = 0``). Raises
    ``RuntimeError`` when the file cannot be read at all."""
    path = Path(path)
    data = _ffprobe_json(path)
    streams: list[dict] = data.get("streams") or []
    fmt: dict = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover_art(s)), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise RuntimeError(f"No audio or video streams found in {path.name}")

    durations = [_to_float(fmt.get("duration"))] + [_to_float(s.get("duration")) for s in streams]
    duration = next((d for d in durations if d and d > 0), 0.0)

    width = height = 0
    fps = 0.0
    rotation = 0
    vcodec = pix_fmt = None
    if video is not None:
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        rotation = _clockwise_rotation(video)
        if rotation in (90, 270):
            width, height = height, width
        fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
        vcodec = video.get("codec_name")
        pix_fmt = video.get("pix_fmt")
    acodec = audio.get("codec_name") if audio is not None else None
    container = _container(str(fmt.get("format_name", "")), path, fmt.get("tags") or {})

    return MediaInfo(
        duration=round(float(duration), 3),
        width=width,
        height=height,
        fps=round(fps, 3),
        has_audio=audio is not None,
        video_codec=vcodec,
        audio_codec=acodec,
        container=container or None,
        rotation=rotation,
        bit_rate=_to_int(fmt.get("bit_rate")),
        sample_rate=_to_int(audio.get("sample_rate")) if audio is not None else None,
        size_bytes=path.stat().st_size if path.exists() else None,
        browser_playable=video is not None and _browser_playable(container, vcodec, pix_fmt, acodec),
    )


def extract_frame(path: Path, t: float, dest: Path, width: int = 640) -> Path:
    """Write the frame at source time ``t`` (accurate seek) to ``dest`` as a JPEG ``width`` px wide."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    w = max(2, int(width) - int(width) % 2)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.3f}",
           "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={w}:-2", "-q:v", "3",
           "-f", "image2", str(dest)]
    run(cmd, timeout=FRAME_TIMEOUT_S)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"Could not extract a frame at {t:.2f}s from {Path(path).name}")
    return dest


# --------------------------------------------------------------- validation
def sniff_container(path: Path) -> str | None:
    """Identify the container family from magic bytes: ``"isobmff"`` (MP4/MOV/M4V),
    ``"ebml"`` (MKV/WebM), ``"avi"`` or ``None``."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if len(head) >= 8 and (head[4:8] == b"ftyp" or head[4:8] in _QUICKTIME_ATOMS):
        return "isobmff"
    if head.startswith(_EBML_MAGIC):
        return "ebml"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    return None


def _isobmff_complete(path: Path, size: int) -> bool:
    """Walk the top-level boxes; a box that extends past EOF means the file is truncated."""
    with open(path, "rb") as fh:
        pos = 0
        while pos + 8 <= size:
            fh.seek(pos)
            header = fh.read(16)
            box_size = struct.unpack(">I", header[:4])[0]
            if box_size == 1:
                if len(header) < 16:
                    return False
                box_size = struct.unpack(">Q", header[8:16])[0]
            elif box_size == 0:  # box extends to end of file
                return True
            if box_size < 8:
                return False
            pos += box_size
        return pos <= size


def _read_vint(data: bytes, offset: int) -> tuple[int, int, bool]:
    """Decode an EBML variable-length integer -> (value, length, is_unknown_size)."""
    first = data[offset]
    length = 1
    mask = 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8 or offset + length > len(data):
        raise ValueError("invalid EBML vint")
    value = first & (mask - 1)
    for b in data[offset + 1:offset + length]:
        value = (value << 8) | b
    unknown = value == (1 << (7 * length)) - 1
    return value, length, unknown


def _ebml_complete(path: Path, size: int) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(4096)
    try:
        hdr_size, hdr_len, _ = _read_vint(head, 4)
        seg_pos = 4 + hdr_len + hdr_size
        if head[seg_pos:seg_pos + 4] != _MKV_SEGMENT_ID:
            return True  # unusual layout; leave the decision to ffprobe/decoding
        seg_size, seg_len, unknown = _read_vint(head, seg_pos + 4)
    except (ValueError, IndexError):
        return False
    return unknown or seg_pos + 4 + seg_len + seg_size <= size


def _avi_complete(path: Path, size: int) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(8)
    riff_size = struct.unpack("<I", head[4:8])[0]
    return riff_size + 8 <= size


def _decodes_frame_at(path: Path, t: float) -> bool:
    proc = run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
                "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=16:16", "-pix_fmt", "gray", "-f", "rawvideo",
                "-"], check=False, timeout=FRAME_TIMEOUT_S)
    return proc.returncode == 0 and len(proc.stdout or b"") >= 256


_STRUCTURE_CHECKS = {"isobmff": _isobmff_complete, "ebml": _ebml_complete, "avi": _avi_complete}


def validate_video_file(path: Path) -> MediaInfo:
    """Validate an uploaded/imported video and return its :class:`MediaInfo`.

    Checks, in order: magic bytes (MP4/MOV/M4V, MKV/WebM, AVI), container structure
    (truncated uploads), FFprobe readability, presence of a video stream, duration in
    ``(0.5 s, MAX_VIDEO_DURATION_SECONDS]`` and that the first and last frames decode.
    Raises :class:`~app.core.errors.ValidationFailed` with a user-facing message."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise ValidationFailed("The file is empty.", code="invalid_media")
    size = path.stat().st_size
    kind = sniff_container(path)
    if kind is None:
        raise ValidationFailed("Unsupported file type: expected an MP4, MOV, M4V, MKV, WebM or AVI video.",
                               code="unsupported_format")
    if not _STRUCTURE_CHECKS[kind](path, size):
        raise ValidationFailed("The video file is incomplete (truncated upload or corrupted file).",
                               code="invalid_media")
    try:
        info = probe_media(path)
    except Exception as exc:  # noqa: BLE001 - any probe failure means an unreadable file
        log.info("Probe failed for %s: %s", path.name, exc)
        raise ValidationFailed("The file could not be read as a video; it may be corrupted or truncated.",
                               code="invalid_media") from exc
    if info.width <= 0 or info.height <= 0:
        raise ValidationFailed("The file does not contain a video stream.", code="no_video_stream")
    if info.duration <= 0.5:
        raise ValidationFailed("The video is too short (minimum 0.5 seconds).", code="invalid_duration")
    max_duration = get_settings().max_video_duration_seconds
    if info.duration > max_duration:
        raise ValidationFailed(
            f"The video is too long ({info.duration / 3600:.1f} h); the maximum is {max_duration / 3600:.1f} h.",
            code="invalid_duration")
    if not _decodes_frame_at(path, 0.0) or not _decodes_frame_at(path, max(0.0, info.duration - 1.0)):
        raise ValidationFailed("The video stream could not be decoded; the file may be corrupted or truncated.",
                               code="invalid_media")
    return info
