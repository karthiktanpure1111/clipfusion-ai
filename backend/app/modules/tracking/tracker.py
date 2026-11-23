"""Clip vision analysis: face detection + tracking, facecam detection, speaker activity and a
coarse motion-activity grid. Produces :class:`ClipVisionAnalysis` (app/schemas/framing.py).

Pipeline: FFmpeg decodes frames between ``start`` and ``end`` at ``sample_fps`` (<= 1280 px,
display orientation) -> faces are detected in a small thread pool (one detector per thread)
-> per-frame mouth/eye motion energies and a frame-difference energy map are accumulated
-> detections are associated into tracks, fragments merged, weak/tiny tracks dropped ->
facecam, speaking score, prominence, speaker timeline, ActivityGrid, layout hint.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from app.core.errors import ValidationFailed
from app.modules.faces.detector import FaceDetection, FaceDetector, get_face_detector
from app.modules.tracking.association import Observation, RawTrack, associate, merge_fragments
from app.modules.tracking.facecam import analyze_facecam
from app.modules.tracking.frames import decode_size, iter_frames
from app.modules.tracking.speakers import PATCH, compute_face_motion, speaker_timeline, speaking_score
from app.schemas.framing import ActivityGrid, ClipVisionAnalysis, FaceBox, FaceTrack
from app.schemas.media import MediaInfo

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str | None], None]

MIN_COVERAGE = 0.15
MIN_FACE_PX = 14.0           # mean face height in source pixels
GRID_COLS, GRID_ROWS = 16, 9
_ENERGY_W, _ENERGY_H = 160, 90
_NOISE_LEVEL = 6             # gray-level differences below this are compression noise
_SCENE_CUT_MEAN = 35.0       # frame pairs with a larger mean difference are scene cuts
_MIN_ACTIVITY = 0.02         # mean per-pixel energy below which there is no meaningful activity
_FACECAM_FRAMES = 12


# ------------------------------------------------------------------ per frame
def _observation(t: float, det: FaceDetection, gray: np.ndarray) -> Observation:
    h, w = gray.shape
    x1, y1 = int(det.rect.x * w), int(det.rect.y * h)
    x2, y2 = max(x1 + 2, int((det.rect.x + det.rect.w) * w)), max(y1 + 2, int((det.rect.y + det.rect.h) * h))
    patch = cv2.resize(gray[y1:y2, x1:x2], (PATCH, PATCH), interpolation=cv2.INTER_AREA)
    return Observation(t=round(t, 4), rect=det.rect, score=round(det.score, 4), patch=patch)


class _Accumulator:
    """Sequential per-frame state: observations, motion energy map, frames for facecam."""

    def __init__(self, keep_every: int) -> None:
        self.frames: list[tuple[float, list[Observation]]] = []
        self.energy = np.zeros((_ENERGY_H, _ENERGY_W), np.float64)
        self.pairs = 0
        self.gray_samples: list[np.ndarray] = []
        self._keep_every = keep_every
        self._prev_small: np.ndarray | None = None

    def add(self, t: float, frame: np.ndarray, dets: list[FaceDetection]) -> None:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (_ENERGY_W, _ENERGY_H), interpolation=cv2.INTER_AREA).astype(np.int16)
        if self._prev_small is not None:
            diff = np.abs(small - self._prev_small)
            if float(diff.mean()) < _SCENE_CUT_MEAN:
                self.energy += np.where(diff > _NOISE_LEVEL, diff, 0)
                self.pairs += 1
        obs = [_observation(t, d, gray) for d in dets]
        if len(self.frames) % self._keep_every == 0 and len(self.gray_samples) < _FACECAM_FRAMES * 2:
            self.gray_samples.append(gray)
        self.frames.append((t, obs))
        self._prev_small = small


# ------------------------------------------------------------------ detection
def _detect_all(video: Path, start: float, end: float, media: MediaInfo, sample_fps: float,
                kind: str, progress: ProgressFn | None) -> _Accumulator:
    size = decode_size(media.width, media.height)
    expected = max(1, int(math.ceil((end - start) * sample_fps)))
    acc = _Accumulator(keep_every=max(1, expected // _FACECAM_FRAMES))
    local = threading.local()

    def detect(frame: np.ndarray) -> list[FaceDetection]:
        det: FaceDetector | None = getattr(local, "detector", None)
        if det is None:
            det = local.detector = get_face_detector(kind)
        return det.detect(frame)

    workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    pending: deque[tuple[float, np.ndarray, Future]] = deque()

    def drain(limit: int) -> None:
        while len(pending) > limit:
            t, frame, fut = pending.popleft()
            acc.add(t, frame, fut.result())
            if progress and len(acc.frames) % 8 == 0:
                progress(min(0.95, len(acc.frames) / expected), "Detecting faces")

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="faces") as pool:
        for t, frame in iter_frames(video, start, end, fps=sample_fps, size=size):
            pending.append((t, frame, pool.submit(detect, frame)))
            drain(workers * 2)
        drain(0)
    return acc


# ------------------------------------------------------------------ tracks
def _keep(track: RawTrack, n_frames: int, source_h: int) -> bool:
    mean = track.mean_rect()
    coverage = len(track.obs) / max(1, n_frames)
    return coverage >= MIN_COVERAGE and mean.h * source_h >= MIN_FACE_PX and mean.area >= 0.0002


def _build_tracks(acc: _Accumulator, media: MediaInfo, speech_clip: list[tuple[float, float]]
                  ) -> tuple[list[FaceTrack], dict[int, list[Observation]]]:
    aspect = media.width / media.height
    raw = merge_fragments(associate(acc.frames, aspect=aspect), aspect=aspect)
    n = len(acc.frames)
    scored: list[tuple[float, FaceTrack, list[Observation]]] = []
    for tr in (t for t in raw if _keep(t, n, media.height)):
        mean = tr.mean_rect()
        rects = [o.rect for o in tr.obs]
        compute_face_motion(tr.obs)
        cam = analyze_facecam(rects, mean, acc.gray_samples)
        coverage = len(tr.obs) / max(1, n)
        mean_area = float(np.mean([r.area for r in rects]))
        speak = speaking_score(tr.obs, speech_clip or None)
        prominence = coverage * math.sqrt(mean_area) * (1 + speak)
        track = FaceTrack(
            id=0, boxes=[FaceBox(t=o.t, rect=o.rect, score=o.score) for o in tr.obs],
            coverage=round(coverage, 4), mean_area=round(mean_area, 6),
            mean_center=(round(float(np.mean([r.cx for r in rects])), 5),
                         round(float(np.mean([r.cy for r in rects])), 5)),
            prominence=round(prominence, 6), is_facecam=cam.is_facecam,
            facecam_rect=cam.rect, speaking_score=speak,
        )
        scored.append((prominence, track, tr.obs))
    scored.sort(key=lambda s: (-s[0], s[1].mean_center))
    tracks: list[FaceTrack] = []
    obs_by_id: dict[int, list[Observation]] = {}
    person = 0
    for i, (_p, track, obs) in enumerate(scored, start=1):
        if not track.is_facecam:
            person += 1
        label = "Facecam" if track.is_facecam else f"Person {person}"
        tracks.append(track.model_copy(update={"id": i, "label": label, "is_primary": i == 1}))
        obs_by_id[i] = obs
    return tracks, obs_by_id


def _activity_grid(acc: _Accumulator, tracks: list[FaceTrack]) -> ActivityGrid | None:
    if acc.pairs == 0:
        return None
    energy = acc.energy.copy()
    for tr in tracks:
        if tr.is_facecam and tr.facecam_rect is not None:
            r = tr.facecam_rect
            x1, y1 = int((r.x - 0.01) * _ENERGY_W), int((r.y - 0.01) * _ENERGY_H)
            x2, y2 = math.ceil((r.x + r.w + 0.01) * _ENERGY_W), math.ceil((r.y + r.h + 0.01) * _ENERGY_H)
            energy[max(0, y1):y2, max(0, x1):x2] = 0.0
    if energy.sum() / (acc.pairs * _ENERGY_W * _ENERGY_H) < _MIN_ACTIVITY:
        return None
    cells = energy.reshape(GRID_ROWS, _ENERGY_H // GRID_ROWS, GRID_COLS, _ENERGY_W // GRID_COLS).sum(axis=(1, 3))
    values = (cells / cells.sum()).ravel()
    return ActivityGrid(cols=GRID_COLS, rows=GRID_ROWS, values=[round(float(v), 6) for v in values])


def _layout_hint(tracks: list[FaceTrack], activity: ActivityGrid | None) -> str:
    if not tracks:
        return "screen_content" if activity is not None else "no_faces"
    if any(t.is_facecam for t in tracks):
        return "facecam_gameplay"
    top = max(t.prominence for t in tracks)
    significant = [t for t in tracks if t.coverage >= 0.25 and t.prominence >= 0.3 * top]
    return "multi_speaker" if len(significant) >= 2 else "single_speaker"


def _clip_speech(speech: Sequence[tuple[float, float]] | None, start: float, end: float
                 ) -> list[tuple[float, float]]:
    out = []
    for a, b in speech or []:
        if b > start and a < end:
            out.append((max(0.0, a - start), min(end, b) - start))
    return out


def analyze_clip_vision(video: Path, start: float, end: float, media: MediaInfo, *,
                        sample_fps: float = 4.0, speech: list[tuple[float, float]] | None = None,
                        progress: ProgressFn | None = None) -> ClipVisionAnalysis:
    """Analyse the faces and activity of ``video`` between ``start`` and ``end`` (source seconds).

    ``speech`` are speech ranges in SOURCE seconds (e.g. transcript segments or the complement
    of the audio silences); they sharpen speaking scores and the speaker timeline. Box and
    timeline times in the result are clip-relative.
    """
    if media.width <= 0 or media.height <= 0:
        raise ValidationFailed("Vision analysis needs a video stream")
    if end <= start:
        raise ValidationFailed("Clip end must be after its start")
    if not 0 < sample_fps <= 30:
        raise ValidationFailed("sample_fps must be in (0, 30]")
    detector_name = get_face_detector("auto").name
    duration = end - start
    acc = _detect_all(Path(video), start, end, media, sample_fps, detector_name, progress)
    if not acc.frames:
        raise ValidationFailed("No frames could be decoded from the clip range")
    speech_clip = _clip_speech(speech, start, end)
    tracks, obs_by_id = _build_tracks(acc, media, speech_clip)
    activity = _activity_grid(acc, tracks)
    timeline = speaker_timeline(obs_by_id, duration=duration, step=1.0 / sample_fps,
                                speech=speech_clip or None,
                                default_track=tracks[0].id if tracks else None)
    vision = ClipVisionAnalysis(
        detector=detector_name, sample_fps=sample_fps, source_width=media.width,
        source_height=media.height, clip_start=start, clip_end=end, tracks=tracks,
        activity=activity, speaker_timeline=timeline, layout_hint=_layout_hint(tracks, activity),
    )
    vision = vision.model_copy(update={"suggested_mode": _suggested_mode(vision, media, duration)})
    if progress:
        progress(1.0, f"{len(tracks)} face track(s)")
    log.info("vision %s [%.2f-%.2f]: %d frames, %d tracks, hint=%s, mode=%s", Path(video).name, start, end,
             len(acc.frames), len(tracks), vision.layout_hint, vision.suggested_mode)
    return vision


def _suggested_mode(vision: ClipVisionAnalysis, media: MediaInfo, duration: float) -> str:
    from app.modules.framing.suggest import suggest_framing

    return suggest_framing(vision, media, duration).mode
