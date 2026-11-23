"""Face detectors: YuNet (OpenCV DNN), MediaPipe Tasks BlazeFace and a Haar-cascade fallback.

All detectors return :class:`FaceDetection` objects with rects normalised to the input frame.
Frames are resized internally to a detection resolution (~1280 px wide) so small facecam
faces in 1080p streams are still found; small inputs are upscaled. YuNet additionally runs a
low-resolution pass so very large (close-up) faces are found too.

Detector instances are NOT thread-safe; create one per thread with :func:`get_face_detector`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

from app.core.config import get_settings
from app.core.errors import DependencyMissing, ValidationFailed
from app.modules.faces.models import mediapipe_model_path, yunet_model_path
from app.schemas.framing import Rect

log = logging.getLogger(__name__)

DETECT_WIDTH = 1280          # main detection resolution (px, longest side for landscape)
LOWRES_WIDTH = 480           # secondary pass for close-up faces
_HAAR_FILE = "haarcascade_frontalface_default.xml"
_ORDER = ("yunet", "mediapipe", "haar")


@dataclass(frozen=True)
class FaceDetection:
    """One detected face. ``rect`` is normalised to the frame; landmarks are normalised
    ``(x, y)`` points (YuNet/MediaPipe order: right eye, left eye, nose, mouth corners...)."""

    rect: Rect
    score: float
    landmarks: list[tuple[float, float]] | None = field(default=None)


@runtime_checkable
class FaceDetector(Protocol):
    name: str

    def detect(self, frame_bgr: np.ndarray) -> list[FaceDetection]: ...


# ---------------------------------------------------------------- helpers
def _check_frame(frame: np.ndarray) -> tuple[int, int]:
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
        raise ValidationFailed("detect() expects a non-empty BGR image of shape (h, w, 3)")
    return frame.shape[1], frame.shape[0]


def _resize_for(frame: np.ndarray, width: int) -> tuple[np.ndarray, float]:
    """Resize so the longest side equals ``width`` (up- or down-scale); returns (img, scale)."""
    h, w = frame.shape[:2]
    scale = width / max(w, h)
    if abs(scale - 1.0) < 0.02:
        return frame, 1.0
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    size = (max(16, round(w * scale)), max(16, round(h * scale)))
    return cv2.resize(frame, size, interpolation=interp), size[0] / w


def _norm_rect(x: float, y: float, w: float, h: float, fw: float, fh: float) -> Rect | None:
    x1, y1 = max(0.0, x), max(0.0, y)
    x2, y2 = min(fw, x + w), min(fh, y + h)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return Rect(x=x1 / fw, y=y1 / fh, w=(x2 - x1) / fw, h=(y2 - y1) / fh)


def _iou(a: Rect, b: Rect) -> float:
    inter = a.intersection_area(b)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def nms(dets: list[FaceDetection], iou_threshold: float = 0.4) -> list[FaceDetection]:
    """Greedy non-maximum suppression (highest score wins)."""
    kept: list[FaceDetection] = []
    for d in sorted(dets, key=lambda d: d.score, reverse=True):
        if all(_iou(d.rect, k.rect) < iou_threshold for k in kept):
            kept.append(d)
    return kept


# ---------------------------------------------------------------- YuNet
class YuNetDetector:
    """OpenCV Zoo YuNet (2023mar) via ``cv2.FaceDetectorYN``."""

    name = "yunet"

    def __init__(self, model_path: Path, *, score_threshold: float = 0.6, nms_threshold: float = 0.3,
                 detect_width: int = DETECT_WIDTH) -> None:
        self._net = cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), score_threshold,
                                              nms_threshold, 5000)
        self._detect_width = detect_width

    def _run(self, img: np.ndarray, scale: float, fw: int, fh: int) -> list[FaceDetection]:
        h, w = img.shape[:2]
        self._net.setInputSize((w, h))
        _, faces = self._net.detect(img)
        out: list[FaceDetection] = []
        if faces is None:
            return out
        for row in faces:
            x, y, bw, bh = (float(v) / scale for v in row[:4])
            rect = _norm_rect(x, y, bw, bh, fw, fh)
            if rect is None:
                continue
            pts = [(float(row[4 + 2 * i]) / scale / fw, float(row[5 + 2 * i]) / scale / fh) for i in range(5)]
            out.append(FaceDetection(rect=rect, score=float(row[14]), landmarks=pts))
        return out

    def detect(self, frame_bgr: np.ndarray) -> list[FaceDetection]:
        fw, fh = _check_frame(frame_bgr)
        img, scale = _resize_for(frame_bgr, self._detect_width)
        dets = self._run(img, scale, fw, fh)
        if max(fw, fh) > LOWRES_WIDTH * 1.5:  # close-up faces are found more reliably at low res
            small, s2 = _resize_for(frame_bgr, LOWRES_WIDTH)
            dets += [d for d in self._run(small, s2, fw, fh) if d.rect.h > 0.18]
        return nms(dets)


# ---------------------------------------------------------------- MediaPipe
class MediaPipeDetector:
    """MediaPipe Tasks BlazeFace short-range detector (IMAGE running mode)."""

    name = "mediapipe"

    def __init__(self, model_path: Path, *, min_confidence: float = 0.5) -> None:
        from mediapipe.tasks.python import BaseOptions, vision

        opts = vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            min_detection_confidence=min_confidence,
        )
        self._det = vision.FaceDetector.create_from_options(opts)

    def detect(self, frame_bgr: np.ndarray) -> list[FaceDetection]:
        import mediapipe as mp

        fw, fh = _check_frame(frame_bgr)
        img, scale = _resize_for(frame_bgr, DETECT_WIDTH) if max(fw, fh) > DETECT_WIDTH else (frame_bgr, 1.0)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        result = self._det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        out: list[FaceDetection] = []
        for d in result.detections or []:
            bb = d.bounding_box
            rect = _norm_rect(bb.origin_x / scale, bb.origin_y / scale, bb.width / scale, bb.height / scale, fw, fh)
            if rect is None:
                continue
            score = float(d.categories[0].score) if d.categories else 0.5
            pts = [(float(k.x), float(k.y)) for k in (d.keypoints or [])] or None
            out.append(FaceDetection(rect=rect, score=score, landmarks=pts))
        return nms(out)


# ---------------------------------------------------------------- Haar
class HaarDetector:
    """OpenCV frontal-face Haar cascade — always available, least accurate."""

    name = "haar"

    def __init__(self, cascade_path: Path) -> None:
        self._cascade = cv2.CascadeClassifier(str(cascade_path))
        if self._cascade.empty():
            raise DependencyMissing(f"Could not load Haar cascade {cascade_path}")

    def detect(self, frame_bgr: np.ndarray) -> list[FaceDetection]:
        fw, fh = _check_frame(frame_bgr)
        img, scale = _resize_for(frame_bgr, 960)
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        rects, _, weights = self._cascade.detectMultiScale3(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20), outputRejectLevels=True)
        out: list[FaceDetection] = []
        for (x, y, w, h), wt in zip(rects, np.ravel(weights) if len(rects) else [], strict=False):
            rect = _norm_rect(x / scale, y / scale, w / scale, h / scale, fw, fh)
            if rect is None:
                continue
            score = float(1.0 / (1.0 + np.exp(-(float(wt) - 2.0))))  # level weight -> 0..1
            if score < 0.5:  # weak cascade hits are mostly false positives
                continue
            out.append(FaceDetection(rect=rect, score=score, landmarks=None))
        return nms(out)


# ---------------------------------------------------------------- factory
def _haar_path() -> Path:
    return Path(cv2.data.haarcascades) / _HAAR_FILE


@lru_cache(maxsize=8)
def _loads(kind: str, path: str, mtime: float) -> bool:
    """Whether a detector of ``kind`` can actually be created (cached per model file)."""
    try:
        _create(kind, Path(path))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Face detector %s unavailable: %s", kind, exc)
        return False


def _model_path(kind: str) -> Path | None:
    if kind == "yunet":
        return yunet_model_path()
    if kind == "mediapipe":
        return mediapipe_model_path()
    path = _haar_path()
    return path if path.is_file() else None


def _create(kind: str, path: Path) -> FaceDetector:
    if kind == "yunet":
        return YuNetDetector(path)
    if kind == "mediapipe":
        return MediaPipeDetector(path)
    return HaarDetector(path)


def _usable(kind: str) -> Path | None:
    path = _model_path(kind)
    if path is None:
        return None
    return path if _loads(kind, str(path), path.stat().st_mtime) else None


def available_detectors() -> list[str]:
    """Detector kinds usable right now, in preference order (yunet > mediapipe > haar)."""
    return [k for k in _ORDER if _usable(k) is not None]


def get_face_detector(kind: str = "auto") -> FaceDetector:
    """Create a face detector. ``"auto"`` honours ``settings.face_detector`` and otherwise
    picks the best available (yunet > mediapipe > haar). An explicitly requested detector
    that is unavailable raises :class:`DependencyMissing` (run ``scripts/download_assets.py``)."""
    if kind == "auto":
        kind = get_settings().face_detector
    if kind != "auto":
        if kind not in _ORDER:
            raise ValidationFailed(f"Unknown face detector '{kind}'")
        path = _usable(kind)
        if path is None:
            raise DependencyMissing(
                f"Face detector '{kind}' is not available. Run backend/scripts/download_assets.py "
                "to download the model files.")
        return _create(kind, path)
    for k in _ORDER:
        path = _usable(k)
        if path is not None:
            return _create(k, path)
    raise DependencyMissing("No face detector is available (OpenCV Haar cascades are missing).")
