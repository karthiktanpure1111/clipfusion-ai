"""Shot/scene-change detection with PySceneDetect's ``ContentDetector``.

Frames are automatically downscaled before comparison and high-frame-rate sources are
sub-sampled to ~15 analysed frames per second, which keeps multi-hour streams tractable
while still catching hard cuts. The video is processed in chunks so progress can be
reported.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.modules.analysis.progress import ProgressFn, report
from app.schemas.media import SceneCut

log = logging.getLogger(__name__)

CONTENT_THRESHOLD = 27.0
MIN_SCENE_SECONDS = 0.6
ANALYSIS_FPS = 15.0
CHUNK_SECONDS = 60.0


def _open(video: Path):  # noqa: ANN202 - scenedetect VideoStream
    from scenedetect import open_video

    return open_video(str(video), backend="opencv")


def detect_scenes(video: Path, *, progress: ProgressFn | None = None) -> list[SceneCut]:
    """Detect scene cuts in ``video`` and return them sorted by time (source seconds).

    ``SceneCut.score`` is ``None``: ContentDetector does not expose a per-cut strength
    without keeping per-frame statistics for the whole video."""
    from scenedetect import ContentDetector, SceneManager

    stream = _open(Path(video))
    fps = float(stream.frame_rate or 0.0) or 30.0
    duration = float(stream.duration.get_seconds()) if stream.duration is not None else 0.0
    frame_skip = max(0, int(fps // ANALYSIS_FPS) - 1)
    min_len = max(2, int(round(MIN_SCENE_SECONDS * fps / (frame_skip + 1))))

    manager = SceneManager()
    manager.auto_downscale = True
    manager.add_detector(ContentDetector(threshold=CONTENT_THRESHOLD, min_scene_len=min_len))
    report(progress, 0.0, "Detecting scene changes")
    if duration <= 0:
        manager.detect_scenes(video=stream, frame_skip=frame_skip)
    else:
        t = 0.0
        while t < duration:
            t = min(duration, t + CHUNK_SECONDS)
            processed = manager.detect_scenes(video=stream, end_time=t, frame_skip=frame_skip)
            report(progress, t / duration, None)
            if processed == 0 and t < duration:
                log.debug("scene detection: decoder stopped early at %.1fs of %.1fs", t, duration)
                break
    cuts = sorted({round(float(tc.get_seconds()), 3) for tc in manager.get_cut_list(show_warning=False)})
    report(progress, 1.0, f"{len(cuts)} scene changes")
    log.debug("scene detection on %s: %d cuts (frame_skip=%d)", Path(video).name, len(cuts), frame_skip)
    return [SceneCut(time=t, score=None) for t in cuts if t > 0]
