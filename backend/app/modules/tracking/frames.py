"""Decode sampled video frames through an FFmpeg pipe (rawvideo BGR24, display orientation)."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from app.core.errors import ValidationFailed
from app.core.ffmpeg import ffmpeg_bin

log = logging.getLogger(__name__)

MAX_DECODE_SIDE = 1280


def decode_size(source_w: int, source_h: int, max_side: int = MAX_DECODE_SIDE) -> tuple[int, int]:
    """Even-sized decode resolution with the longest side <= ``max_side`` (never upscaled)."""
    if source_w <= 0 or source_h <= 0:
        raise ValidationFailed("The video has no picture (width/height is 0)")
    scale = min(1.0, max_side / max(source_w, source_h))
    w = max(2, int(round(source_w * scale / 2)) * 2)
    h = max(2, int(round(source_h * scale / 2)) * 2)
    return w, h


def iter_frames(video: Path, start: float, end: float, *, fps: float, size: tuple[int, int],
                timeout: float | None = None) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(clip_time, frame_bgr)`` for frames sampled at ``fps`` between ``start`` and
    ``end`` (source seconds). FFmpeg auto-rotates, so frames are in display orientation.
    Frame ``i`` is the frame nearest to clip time ``i / fps``."""
    duration = max(0.0, end - start)
    if duration <= 0:
        return
    w, h = size
    cmd = [ffmpeg_bin(), "-nostdin", "-hide_banner", "-loglevel", "error",
           "-ss", f"{max(0.0, start):.3f}", "-i", str(video), "-t", f"{duration:.3f}",
           "-an", "-sn", "-dn", "-vf", f"fps={fps:g},scale={w}:{h}:flags=area",
           "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    frame_bytes = w * h * 3
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, bufsize=frame_bytes)
        assert proc.stdout is not None
        index = 0
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                yield index / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
                index += 1
        finally:
            proc.stdout.close()
            try:
                code = proc.wait(timeout=timeout or 30)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait()
        if code not in (0, None) and index == 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace")[-1500:]
            raise RuntimeError(f"FFmpeg could not decode {Path(video).name}: {tail}")
        if code not in (0, None):
            log.warning("FFmpeg exited with %s after %d frames of %s", code, index, Path(video).name)
