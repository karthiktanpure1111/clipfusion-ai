"""Locate and run FFmpeg / FFprobe.

Resolution order: FFMPEG_PATH / FFPROBE_PATH env vars -> binaries on PATH ->
the static FFmpeg bundled with the ``imageio-ffmpeg`` wheel (no ffprobe in that case;
:func:`ffprobe_available` reports it and callers fall back to ``ffmpeg -i`` parsing).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import DependencyMissing

log = logging.getLogger(__name__)


@lru_cache
def ffmpeg_bin() -> str:
    settings = get_settings()
    if settings.ffmpeg_path and Path(settings.ffmpeg_path).exists():
        return settings.ffmpeg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depends on environment
        raise DependencyMissing(
            "FFmpeg was not found. Install FFmpeg (https://ffmpeg.org) or set FFMPEG_PATH."
        ) from exc


@lru_cache
def ffprobe_bin() -> str | None:
    settings = get_settings()
    if settings.ffprobe_path and Path(settings.ffprobe_path).exists():
        return settings.ffprobe_path
    found = shutil.which("ffprobe")
    if found:
        return found
    sibling = Path(ffmpeg_bin()).with_name("ffprobe" + Path(ffmpeg_bin()).suffix)
    return str(sibling) if sibling.exists() else None


def ffprobe_available() -> bool:
    return ffprobe_bin() is not None


def run(cmd: list[str], *, timeout: float | None = None, check: bool = True,
        capture: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess with logging; raises RuntimeError with stderr tail on failure."""
    log.debug("exec: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"Command failed ({proc.returncode}): {Path(cmd[0]).name} ...\n{tail}")
    return proc


def capabilities() -> dict:
    """Report which FFmpeg features are available (exposed via /system/capabilities)."""
    try:
        exe = ffmpeg_bin()
    except DependencyMissing:
        return {"ffmpeg": False, "ffprobe": False, "libx264": False, "libass": False}
    out = run([exe, "-hide_banner", "-encoders"], check=False).stdout.decode("utf-8", "replace")
    filt = run([exe, "-hide_banner", "-filters"], check=False).stdout.decode("utf-8", "replace")
    return {
        "ffmpeg": True,
        "ffprobe": ffprobe_available(),
        "libx264": "libx264" in out,
        "libass": " ass " in filt,
        "path": exe,
    }
