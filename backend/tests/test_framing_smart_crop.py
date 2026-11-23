"""Smart crop keyframes: the followed face stays fully inside the crop, the camera is calm,
speaker switches are hard cuts."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.modules.faces.models import ensure_face_models
from app.modules.framing.geometry import interpolate_keyframes
from app.modules.framing.smart_crop import compute_smart_keyframes, crop_size, shot_plan
from app.schemas.framing import (
    FULL_FRAME,
    ClipVisionAnalysis,
    FaceBox,
    FaceTrack,
    Rect,
    SmartCropConfig,
    SpeakerSegment,
)
from tests.helpers.vision_media import analysed, face_photo


def _inside(crop: Rect, face: Rect, tol: float = 1e-4) -> bool:
    return (crop.x <= face.x + tol and crop.x + crop.w >= face.x + face.w - tol
            and crop.y <= face.y + tol and crop.y + crop.h >= face.y + face.h - tol)


def _track(tid: int, fn, duration: float = 10.0, step: float = 0.25, **kw) -> FaceTrack:
    boxes = [FaceBox(t=round(i * step, 3), rect=fn(i * step)) for i in range(int(duration / step) + 1)
             if fn(i * step) is not None]
    return FaceTrack(id=tid, boxes=boxes, coverage=len(boxes) / (duration / step + 1), mean_area=0.02,
                     prominence=kw.pop("prominence", 0.1), **kw)


def _vision(tracks: list[FaceTrack], timeline: list[SpeakerSegment] | None = None, duration: float = 10.0,
            w: int = 1920, h: int = 1080) -> ClipVisionAnalysis:
    return ClipVisionAnalysis(detector="yunet", sample_fps=4.0, source_width=w, source_height=h, clip_start=0.0,
                              clip_end=duration, tracks=tracks, speaker_timeline=timeline or [])


def test_real_podcast_face_always_inside_and_camera_calm(tmp_media_dir: Path) -> None:
    if not ensure_face_models()["yunet"]["available"] or face_photo("astronaut.png") is None:
        pytest.skip("face model or portrait unavailable")
    _, media, vision = analysed("podcast", tmp_media_dir)
    keys = compute_smart_keyframes(vision, SmartCropConfig(), source_w=1920, source_h=1080,
                                   duration=media.duration)
    assert 0 < len(keys) <= media.duration * 5 + 2
    assert not any(k.cut for k in keys)
    cw = keys[0].rect.w
    assert abs(cw * 1920 / (keys[0].rect.h * 1080) - 9 / 16) < 1e-3
    for box in vision.tracks[0].boxes:
        assert _inside(interpolate_keyframes(keys, box.t), box.rect), f"face cut at t={box.t}"
    xs = np.array([interpolate_keyframes(keys, t).x for t in np.arange(0, media.duration, 0.1)])
    per_second = np.abs(xs[10:] - xs[:-10])
    face_speed = 110 / 1920  # px/s of the synthetic walk, normalised
    assert per_second.max() <= face_speed * 1.6 + 0.01, "camera must not move much faster than the person"
    assert np.abs(np.diff(xs)).max() < 0.02, "no jumps between samples"


def test_fast_motion_never_cuts_the_face() -> None:
    track = _track(1, lambda t: Rect(x=0.05 + 0.08 * t, y=0.3, w=0.06, h=0.11), is_primary=True)
    keys = compute_smart_keyframes(_vision([track]), SmartCropConfig(smoothing=1.0), source_w=1920,
                                   source_h=1080, duration=10.0)
    for b in track.boxes:
        assert _inside(interpolate_keyframes(keys, b.t), b.rect)


def test_auto_switch_hard_cuts_respect_min_shot() -> None:
    a = _track(1, lambda t: Rect(x=0.1, y=0.3, w=0.06, h=0.11), is_primary=True, prominence=0.2)
    b = _track(2, lambda t: Rect(x=0.8, y=0.3, w=0.06, h=0.11))
    timeline = [SpeakerSegment(start=0, end=4, track_id=1), SpeakerSegment(start=4, end=5, track_id=2),
                SpeakerSegment(start=5, end=6, track_id=1), SpeakerSegment(start=6, end=12, track_id=2)]
    vision = _vision([a, b], timeline, duration=12.0)
    smart = SmartCropConfig(auto_switch=True, min_shot_seconds=2.5)
    assert shot_plan(vision, smart, 12.0) == [(0.0, 6.0, 1), (6.0, 12.0, 2)]
    keys = compute_smart_keyframes(vision, smart, source_w=1920, source_h=1080, duration=12.0)
    cuts = [k for k in keys if k.cut]
    assert len(cuts) == 1 and cuts[0].t == 6.0
    before, after = interpolate_keyframes(keys, 5.99), interpolate_keyframes(keys, 6.0)
    assert _inside(before, a.boxes[0].rect) and _inside(after, b.boxes[0].rect)
    assert not _inside(before, b.boxes[0].rect)


def test_without_auto_switch_follows_selected_person() -> None:
    a = _track(1, lambda t: Rect(x=0.1, y=0.3, w=0.06, h=0.11), is_primary=True)
    b = _track(2, lambda t: Rect(x=0.8, y=0.3, w=0.06, h=0.11))
    keys = compute_smart_keyframes(_vision([a, b]), SmartCropConfig(primary_track_id=2), source_w=1920,
                                   source_h=1080, duration=10.0)
    assert all(_inside(interpolate_keyframes(keys, bx.t), bx.rect) for bx in b.boxes)


def test_zoom_headroom_places_face_in_upper_part() -> None:
    track = _track(1, lambda t: Rect(x=0.45, y=0.35, w=0.06, h=0.12), is_primary=True)
    keys = compute_smart_keyframes(_vision([track]), SmartCropConfig(zoom=2.0), source_w=1920, source_h=1080,
                                   duration=10.0)
    crop = interpolate_keyframes(keys, 5.0)
    assert crop.h == pytest.approx(0.5, abs=1e-4)
    rel = (track.boxes[0].rect.cy - crop.y) / crop.h
    assert 0.3 <= rel <= 0.45
    assert _inside(crop, track.boxes[0].rect)


def test_missing_face_holds_position() -> None:
    track = _track(1, lambda t: Rect(x=0.2, y=0.3, w=0.06, h=0.11) if t < 3 else
                   (Rect(x=0.7, y=0.3, w=0.06, h=0.11) if t > 7 else None), is_primary=True)
    keys = compute_smart_keyframes(_vision([track]), SmartCropConfig(), source_w=1920, source_h=1080,
                                   duration=10.0)
    x_start, x_mid, x_end = (interpolate_keyframes(keys, t).x for t in (1.0, 4.5, 9.5))
    assert abs(x_mid - x_start) < 0.25 * abs(x_end - x_start)
    for b in track.boxes:
        assert _inside(interpolate_keyframes(keys, b.t), b.rect)


def test_portrait_no_faces_and_crop_size() -> None:
    assert compute_smart_keyframes(_vision([], w=1080, h=1920), SmartCropConfig(), source_w=1080,
                                   source_h=1920, duration=5)[0].rect == FULL_FRAME
    (only,) = compute_smart_keyframes(_vision([]), SmartCropConfig(), source_w=1920, source_h=1080, duration=5)
    assert only.rect.cx == pytest.approx(0.5, abs=1e-5) and only.rect.h == 1.0
    w, h = crop_size(1440, 1080, 1.5, 9 / 16)
    assert h == pytest.approx(1 / 1.5) and w * 1440 / (h * 1080) == pytest.approx(9 / 16)
