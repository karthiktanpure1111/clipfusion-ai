"""Face-detection model files: download, validation and lookup.

Models live in ``settings.models_dir``:

* ``face_detection_yunet_2023mar.onnx`` — OpenCV Zoo YuNet (MIT). Primary detector.
* ``blaze_face_short_range.tflite``      — MediaPipe BlazeFace short range (Apache-2.0).

:func:`ensure_face_models` is idempotent: valid files are kept, invalid ones (e.g. a Git-LFS
pointer saved instead of the binary) are replaced. Downloads go to a temporary file first and
are moved into place only after validation, so a crash never leaves a corrupt model behind.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
BLAZE_FILENAME = "blaze_face_short_range.tflite"

_YUNET_URLS = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx",
)
_BLAZE_URLS = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/"
    "blaze_face_short_range.tflite",
)
_MAX_MODEL_BYTES = 64 * 1024 * 1024
_USER_AGENT = "ClipFusion-AI model fetcher/1.0"


@dataclass(frozen=True)
class _ModelSpec:
    key: str
    filename: str
    urls: tuple[str, ...]
    min_bytes: int
    required: bool


_SPECS = (
    _ModelSpec("yunet", YUNET_FILENAME, _YUNET_URLS, 50_000, True),
    _ModelSpec("mediapipe", BLAZE_FILENAME, _BLAZE_URLS, 50_000, False),
)


def models_dir() -> Path:
    """Directory holding the face models (created on demand)."""
    path = Path(get_settings().models_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _looks_like_lfs_pointer(head: bytes) -> bool:
    return head.startswith(b"version https://git-lfs")


def _valid_onnx(path: Path) -> bool:
    """Real ONNX protobuf (not an LFS pointer / HTML page) that OpenCV can load."""
    try:
        head = path.read_bytes()[:64]
    except OSError:
        return False
    if _looks_like_lfs_pointer(head) or head.lstrip().startswith(b"<"):
        return False
    if not head or head[0] != 0x08:  # field 1 (ir_version, varint) opens every ONNX model
        return False
    try:
        import cv2

        cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.6, 0.3)
    except Exception as exc:  # noqa: BLE001 - any load failure means "invalid"
        log.warning("YuNet model at %s failed to load: %s", path, exc)
        return False
    return True


def _valid_tflite(path: Path) -> bool:
    try:
        head = path.read_bytes()[:16]
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"TFL3"


def _is_valid(spec: _ModelSpec, path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < spec.min_bytes:
        return False
    return _valid_onnx(path) if spec.filename.endswith(".onnx") else _valid_tflite(path)


def yunet_model_path() -> Path | None:
    """Path of a usable YuNet model, or None when it has not been downloaded."""
    path = models_dir() / YUNET_FILENAME
    return path if path.is_file() and path.stat().st_size >= _SPECS[0].min_bytes else None


def mediapipe_model_path() -> Path | None:
    """Path of a usable BlazeFace model, or None when it has not been downloaded."""
    path = models_dir() / BLAZE_FILENAME
    return path if path.is_file() and path.stat().st_size >= _SPECS[1].min_bytes else None


def _download(url: str, dest: Path, *, timeout: float) -> None:
    """Stream ``url`` into ``dest`` (size-capped)."""
    total = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout,
                      headers={"User-Agent": _USER_AGENT}) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(64 * 1024):
                total += len(chunk)
                if total > _MAX_MODEL_BYTES:
                    raise ValueError(f"model download exceeds {_MAX_MODEL_BYTES} bytes")
                fh.write(chunk)


def _ensure_one(spec: _ModelSpec, *, timeout: float) -> dict:
    target = models_dir() / spec.filename
    if _is_valid(spec, target):
        return {"available": True, "downloaded": False, "path": str(target), "error": None}
    errors: list[str] = []
    for url in spec.urls:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{spec.key}-", suffix=".part" + Path(spec.filename).suffix,
                                       dir=models_dir())
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            _download(url, tmp, timeout=timeout)
            if not _is_valid(spec, tmp):
                raise ValueError("downloaded file is not a valid model (Git-LFS pointer or wrong format?)")
            os.replace(tmp, target)
            log.info("Downloaded %s model from %s", spec.key, url)
            return {"available": True, "downloaded": True, "path": str(target), "error": None}
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            errors.append(f"{url}: {exc}")
            log.warning("Could not fetch %s model from %s: %s", spec.key, url, exc)
        finally:
            tmp.unlink(missing_ok=True)
    return {"available": False, "downloaded": False, "path": None, "error": "; ".join(errors)}


def ensure_face_models(*, include_mediapipe: bool = True, timeout: float = 60.0) -> dict:
    """Download the face-detection models into ``settings.models_dir`` (idempotent).

    Returns ``{"yunet": {...}, "mediapipe": {...}}`` where each entry has ``available``,
    ``downloaded``, ``path`` and ``error``. Never raises for network problems — callers
    inspect ``available`` (the Haar cascade bundled with OpenCV is always usable).
    """
    result: dict[str, dict] = {}
    for spec in _SPECS:
        if spec.key == "mediapipe" and not include_mediapipe:
            result[spec.key] = {"available": mediapipe_model_path() is not None, "downloaded": False,
                                "path": None, "error": "skipped"}
            continue
        result[spec.key] = _ensure_one(spec, timeout=timeout)
    return result
