"""Face detection: model management and real detections on public-domain portraits."""

from __future__ import annotations

from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
import respx

from app.core.errors import DependencyMissing, ValidationFailed
from app.modules.faces import models as face_models
from app.modules.faces.detector import (
    FaceDetection,
    FaceDetector,
    available_detectors,
    get_face_detector,
    nms,
)
from app.schemas.framing import Rect
from tests.helpers.vision_media import face_photo

# astronaut.png face (verified manually): x .35-.53, y .12-.34
ASTRONAUT_FACE = Rect(x=0.348, y=0.117, w=0.179, h=0.226)


@pytest.fixture(scope="module")
def models_ready() -> dict:
    result = face_models.ensure_face_models()
    if not result["yunet"]["available"]:
        pytest.skip(f"YuNet model unavailable (offline?): {result['yunet']['error']}")
    return result


@pytest.fixture(scope="module")
def astronaut() -> np.ndarray:
    path = face_photo("astronaut.png")
    if path is None:
        pytest.skip("test portrait unavailable (offline)")
    return cv2.imread(str(path))


def _best_iou(dets: list[FaceDetection], truth: Rect) -> float:
    def iou(a: Rect, b: Rect) -> float:
        inter = a.intersection_area(b)
        return inter / (a.area + b.area - inter)
    return max((iou(d.rect, truth) for d in dets), default=0.0)


def test_ensure_face_models_is_idempotent(models_ready: dict) -> None:
    again = face_models.ensure_face_models()
    assert again["yunet"]["available"] and not again["yunet"]["downloaded"]
    assert Path(again["yunet"]["path"]).stat().st_size > 100_000


def test_lfs_pointer_is_rejected_and_mirror_used(models_ready: dict, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path(models_ready["yunet"]["path"]).read_bytes()
    monkeypatch.setattr(face_models, "models_dir", lambda: tmp_path)
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 232589\n"
    with respx.mock(assert_all_called=False) as mock:
        mock.get(face_models._YUNET_URLS[0]).mock(return_value=httpx.Response(200, content=pointer))
        mock.get(face_models._YUNET_URLS[1]).mock(return_value=httpx.Response(200, content=real))
        result = face_models.ensure_face_models(include_mediapipe=False)
    assert result["yunet"] == {"available": True, "downloaded": True,
                               "path": str(tmp_path / face_models.YUNET_FILENAME), "error": None}
    assert (tmp_path / face_models.YUNET_FILENAME).read_bytes() == real
    assert not list(tmp_path.glob("*.part*")), "temporary files must be cleaned up"


def test_all_mirrors_failing_reports_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_models, "models_dir", lambda: tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        for url in face_models._YUNET_URLS:
            mock.get(url).mock(return_value=httpx.Response(404))
        result = face_models.ensure_face_models(include_mediapipe=False)
    assert result["yunet"]["available"] is False and "404" in result["yunet"]["error"]
    assert not (tmp_path / face_models.YUNET_FILENAME).exists()


@pytest.mark.parametrize("kind", ["yunet", "mediapipe", "haar"])
def test_detectors_find_the_face(kind: str, models_ready: dict, astronaut: np.ndarray) -> None:
    if kind not in available_detectors():
        pytest.skip(f"{kind} not available")
    det = get_face_detector(kind)
    assert isinstance(det, FaceDetector) and det.name == kind
    dets = det.detect(astronaut)
    assert dets, f"{kind} found no face"
    assert _best_iou(dets, ASTRONAUT_FACE) > 0.5
    assert all(0 <= d.score <= 1 for d in dets)
    if kind != "haar":
        assert dets[0].landmarks and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in dets[0].landmarks)


def test_small_facecam_face_found_in_1080p_frame(models_ready: dict, astronaut: np.ndarray) -> None:
    frame = np.full((1080, 1920, 3), 40, np.uint8)
    cam = cv2.resize(astronaut, (240, 240))  # face ~43 px wide in a 1080p frame
    frame[800:1040, 1640:1880] = cam
    dets = get_face_detector("yunet").detect(frame)
    assert len(dets) == 1
    r = dets[0].rect
    assert 1640 / 1920 <= r.cx <= 1880 / 1920 and 800 / 1080 <= r.cy <= 1040 / 1080


def test_close_up_face_found(models_ready: dict, astronaut: np.ndarray) -> None:
    h, w = astronaut.shape[:2]
    face = astronaut[0:int(0.45 * h), int(0.25 * w):int(0.62 * w)]
    big = cv2.resize(face, (1100, 1300))  # face fills most of the frame
    dets = get_face_detector("yunet").detect(big)
    assert dets and max(d.rect.h for d in dets) > 0.4


def test_auto_prefers_yunet_and_rejects_bad_input(models_ready: dict) -> None:
    assert available_detectors()[0] == "yunet"
    assert get_face_detector("auto").name == "yunet"
    with pytest.raises(ValidationFailed):
        get_face_detector("auto").detect(np.zeros((10, 10), np.uint8))
    with pytest.raises(ValidationFailed):
        get_face_detector("nope")


def test_missing_model_raises_dependency_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.faces import detector

    monkeypatch.setattr(detector, "yunet_model_path", lambda: None)
    assert "yunet" not in available_detectors()
    with pytest.raises(DependencyMissing):
        get_face_detector("yunet")


def test_nms_keeps_highest_score() -> None:
    a = FaceDetection(rect=Rect(x=0.1, y=0.1, w=0.2, h=0.2), score=0.9)
    b = FaceDetection(rect=Rect(x=0.11, y=0.1, w=0.2, h=0.2), score=0.7)
    c = FaceDetection(rect=Rect(x=0.6, y=0.1, w=0.2, h=0.2), score=0.8)
    assert nms([b, c, a]) == [a, c]


def test_blank_frame_has_no_faces(models_ready: dict) -> None:
    assert get_face_detector("yunet").detect(np.full((720, 1280, 3), 128, np.uint8)) == []
