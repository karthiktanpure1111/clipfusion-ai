"""Visual activity: frame-difference motion and brightness sampled at a low rate.

FFmpeg decodes the video at ``sample_hz`` frames per second, scaled to a tiny grayscale
image, and streams raw frames through a pipe (constant memory for any video length).

* ``motion[i]`` = mean absolute pixel difference between sample ``i`` and ``i - 1`` divided
  by 255 (0 = static, 1 = every pixel flipped black/white); ``motion[0] = 0``.
* ``brightness[i]`` = mean luma of sample ``i`` divided by 255.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from app.core.ffmpeg import ffmpeg_bin
from app.modules.analysis.progress import ProgressFn, report
from app.schemas.media import VisualActivity

log = logging.getLogger(__name__)

FRAME_W, FRAME_H = 128, 72
_FRAME_BYTES = FRAME_W * FRAME_H


def _probe_duration(video: Path) -> float:
    from app.modules.analysis.probe import probe_media

    try:
        return probe_media(video).duration
    except Exception:  # noqa: BLE001 - progress is best effort only
        return 0.0


def analyze_visual_activity(video: Path, *, sample_hz: float = 1.0,
                            progress: ProgressFn | None = None) -> VisualActivity:
    """Sample ``video`` at ``sample_hz`` and return per-sample motion and brightness (0..1)."""
    if sample_hz <= 0:
        raise ValueError("sample_hz must be positive")
    video = Path(video)
    duration = _probe_duration(video)
    expected = max(1, int(duration * sample_hz)) if duration > 0 else 0
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
           "-map", "0:v:0", "-an", "-sn",
           "-vf", f"fps={sample_hz:g},scale={FRAME_W}:{FRAME_H}:flags=area,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    motion: list[float] = []
    brightness: list[float] = []
    prev: np.ndarray | None = None
    report(progress, 0.0, "Measuring on-screen motion")
    err_file = tempfile.TemporaryFile()  # a file, not a pipe: noisy decoders cannot deadlock us
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file)
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(_FRAME_BYTES)
            if len(buf) < _FRAME_BYTES:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
            brightness.append(round(float(frame.mean()) / 255.0, 4))
            motion.append(0.0 if prev is None else round(float(np.abs(frame - prev).mean()) / 255.0, 4))
            prev = frame
            if expected and len(motion) % 30 == 0:
                report(progress, len(motion) / expected, None)
        code = proc.wait(timeout=60)
        err_file.seek(0)
        stderr = err_file.read()[-4000:].decode("utf-8", "replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        err_file.close()
    if code != 0 and not motion:
        raise RuntimeError(f"Could not decode video frames of {video.name}: {stderr[-800:]}")
    if code != 0:
        log.warning("Frame sampling of %s ended with errors after %d samples: %s",
                    video.name, len(motion), stderr[-300:])
    report(progress, 1.0, None)
    return VisualActivity(sample_hz=float(sample_hz), motion=motion, brightness=brightness)
