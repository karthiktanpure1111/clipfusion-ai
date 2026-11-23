"""One-pass video analysis: scene cuts, motion and brightness from a single FFmpeg decode.

FFmpeg decodes the video once, drops frames to ``fps`` *before* scaling, scales to a tiny
grayscale image and streams raw frames through a pipe (constant memory for any length).
For every consecutive frame pair we measure

* ``d``  = mean absolute luma difference / 255 (0 = identical, 1 = black <-> white), and
* ``h``  = total-variation distance of the 32-bin luma histograms (0..1),

Scene cut (adaptive, in the spirit of PySceneDetect's AdaptiveDetector, which this replaces):
a pair is a cut when ``score = 0.5 * d_norm + 0.5 * h`` (``d_norm = min(1, 4 d)``) is at least
``MIN_CUT_SCORE`` *and* either ``CUT_RATIO`` times or ``CUT_JUMP`` above the median score of
the surrounding frames (one second each side, excluding the pair itself; the jump rule keeps
cuts inside fast, steady motion). Cuts closer than ``MIN_SCENE_SECONDS`` keep the
stronger one. ``SceneCut.score`` is the pair's score (0..1).

``VisualActivity`` (``motion_hz`` buckets, default 1 Hz): ``motion[k]`` = mean ``d`` over the
pairs of bucket ``k`` excluding scene-cut pairs (cuts are counted separately by the
highlight detector), ``brightness[k]`` = mean luma / 255 of the bucket's frames.

The decoder runs at below-normal OS priority: it overlaps audio extraction and transcription,
which are on the critical path, and should only use the cores they leave idle.

On this project's 12-core benchmark machine CPU decoding (10.7 s for 10 min of 1080p30) beat
NVDEC (23.7 s with ``-hwaccel cuda``), so decoding stays on the CPU.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.core.ffmpeg import ffmpeg_bin
from app.modules.analysis.progress import ProgressFn, report
from app.schemas.media import SceneCut, VisualActivity

log = logging.getLogger(__name__)

DEFAULT_FPS = 10.0
DEFAULT_WIDTH, DEFAULT_HEIGHT = 160, 90
HIST_BINS = 32
MIN_CUT_SCORE = 0.30
CUT_RATIO = 3.0
CUT_JUMP = 0.5
CONTEXT_SECONDS = 1.0
MIN_SCENE_SECONDS = 0.6
PROGRESS_EVERY_FRAMES = 50
DECODE_WAIT_S = 120


@dataclass
class VideoAnalysisResult:
    scenes: list[SceneCut] = field(default_factory=list)
    visual: VisualActivity = field(default_factory=VisualActivity)
    frames: int = 0
    fps: float = DEFAULT_FPS


def _command(video: Path, fps: float, width: int, height: int) -> list[str]:
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video),
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", f"fps={fps:g},scale={width}:{height}:flags=area,format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]


def _low_priority() -> dict:
    """Popen kwargs that start the child at below-normal priority (best effort)."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)}
    return {"preexec_fn": lambda: os.nice(5)}  # noqa: PLW1509 - only calls nice()


def _histogram(frame: np.ndarray) -> np.ndarray:
    hist = np.bincount(frame.ravel() >> 3, minlength=HIST_BINS).astype(np.float64)   # 256/32 = 8 levels per bin
    return hist / max(1.0, hist.sum())


def detect_cuts(scores: np.ndarray, fps: float) -> list[tuple[int, float]]:
    """Indices (pair ``i`` = between frame ``i - 1`` and ``i``) and scores of the scene cuts."""
    n = scores.size
    if n < 2:
        return []
    ctx = max(1, int(round(CONTEXT_SECONDS * fps)))
    candidates: list[tuple[int, float]] = []
    for i in range(1, n):
        s = float(scores[i])
        if s < MIN_CUT_SCORE:
            continue
        lo, hi = max(1, i - ctx), min(n, i + ctx + 1)
        neighbours = np.concatenate([scores[lo:i], scores[i + 1:hi]])
        base = float(np.median(neighbours)) if neighbours.size else 0.0
        if s >= CUT_RATIO * max(base, 0.02) or s - base >= CUT_JUMP:
            candidates.append((i, s))
    min_gap = max(1, int(round(MIN_SCENE_SECONDS * fps)))
    kept: list[tuple[int, float]] = []
    for i, s in candidates:
        if kept and i - kept[-1][0] < min_gap:
            if s > kept[-1][1]:
                kept[-1] = (i, s)
            continue
        kept.append((i, s))
    return kept


def _bucket(values: np.ndarray, frame_idx: np.ndarray, buckets: int) -> list[float]:
    sums = np.bincount(frame_idx, weights=values, minlength=buckets)[:buckets]
    counts = np.bincount(frame_idx, minlength=buckets)[:buckets]
    out = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
    return [round(float(v), 4) for v in out]


def summarise(diffs: np.ndarray, hist_dists: np.ndarray, means: np.ndarray, fps: float,
              motion_hz: float = 1.0) -> VideoAnalysisResult:
    """Scene cuts + bucketed motion/brightness from per-frame measurements (pure numpy)."""
    n = means.size
    if n == 0:
        return VideoAnalysisResult(visual=VisualActivity(sample_hz=motion_hz), fps=fps)
    scores = 0.5 * np.minimum(1.0, 4.0 * diffs) + 0.5 * hist_dists
    scores[0] = 0.0
    cuts = detect_cuts(scores, fps)
    cut_pairs = np.zeros(n, dtype=bool)
    for i, _ in cuts:
        cut_pairs[i] = True
    times = np.arange(n, dtype=np.float64) / fps
    buckets = max(1, int(np.ceil(n / fps * motion_hz - 1e-9)))
    frame_bucket = np.minimum(buckets - 1, (times * motion_hz).astype(np.int64))
    pair_ok = ~cut_pairs
    pair_ok[0] = False
    motion = _bucket(diffs[pair_ok], frame_bucket[pair_ok], buckets) if pair_ok.any() else [0.0] * buckets
    brightness = _bucket(means / 255.0, frame_bucket, buckets)
    scenes = [SceneCut(time=round(float(times[i]), 3), score=round(s, 3)) for i, s in cuts if times[i] > 0]
    return VideoAnalysisResult(scenes=scenes, visual=VisualActivity(sample_hz=float(motion_hz), motion=motion,
                                                                     brightness=brightness),
                               frames=n, fps=fps)


def analyze_video(video: Path, *, fps: float = DEFAULT_FPS, width: int = DEFAULT_WIDTH,
                  height: int = DEFAULT_HEIGHT, motion_hz: float = 1.0, duration: float | None = None,
                  progress: ProgressFn | None = None) -> VideoAnalysisResult:
    """Decode ``video`` once and return its scene cuts and visual activity.

    ``duration`` (seconds, optional) is only used for progress. Exceptions raised by
    ``progress`` (cancellation) stop FFmpeg and propagate. Raises RuntimeError when no frame
    can be decoded."""
    if fps <= 0 or width < 8 or height < 8:
        raise ValueError("invalid analysis fps/size")
    video = Path(video)
    frame_bytes = width * height
    expected = int(duration * fps) if duration and duration > 0 else 0
    diffs: list[float] = []
    hists: list[float] = []
    means: list[float] = []
    prev: np.ndarray | None = None
    prev_hist: np.ndarray | None = None
    report(progress, 0.0, "Analysing video (scene changes, motion)")
    err_file = tempfile.TemporaryFile()      # a file, not a pipe: noisy decoders cannot deadlock us
    proc = subprocess.Popen(_command(video, fps, width, height), stdout=subprocess.PIPE, stderr=err_file,
                            stdin=subprocess.DEVNULL, bufsize=1 << 20, **_low_priority())
    code: int | None = None
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, dtype=np.uint8)
            hist = _histogram(frame)
            means.append(float(frame.mean()))
            if prev is None:
                diffs.append(0.0)
                hists.append(0.0)
            else:
                diffs.append(float(np.abs(frame.astype(np.int16) - prev).mean()) / 255.0)
                hists.append(0.5 * float(np.abs(hist - prev_hist).sum()))
            prev, prev_hist = frame.astype(np.int16), hist
            if len(means) % PROGRESS_EVERY_FRAMES == 0:
                n = len(means)
                frac = n / expected if expected else 0.0
                report(progress, min(0.99, frac), f"Analysing video {n / fps:.0f}s"
                       + (f" / {duration:.0f}s" if duration else ""))
        code = proc.wait(timeout=DECODE_WAIT_S)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        err_file.seek(0)
        stderr = err_file.read()[-4000:].decode("utf-8", "replace")
        err_file.close()
    if not means:
        raise RuntimeError(f"Could not decode video frames of {video.name}: {stderr[-800:]}")
    if code not in (0, None):
        log.warning("Video analysis of %s ended with errors after %d frames: %s", video.name, len(means),
                    stderr[-300:])
    result = summarise(np.asarray(diffs), np.asarray(hists), np.asarray(means), fps, motion_hz)
    report(progress, 1.0, f"{len(result.scenes)} scene changes")
    log.debug("video analysis of %s: %d frames @ %g fps, %d cuts", video.name, result.frames, fps,
              len(result.scenes))
    return result
