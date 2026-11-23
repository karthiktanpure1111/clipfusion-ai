"""Automatic framing suggestions: right mode per content type, never a cropped face."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.faces.models import ensure_face_models
from app.modules.framing.geometry import interpolate_keyframes
from app.modules.framing.protection import analyze_protection
from app.modules.framing.suggest import suggest_framing
from app.schemas.framing import (
    ClipVisionAnalysis,
    FaceBox,
    FaceTrack,
    Rect,
    SpeakerSegment,
    blocking_warnings,
)
from app.schemas.media import MediaInfo
from tests.helpers.vision_media import analysed, face_photo

MEDIA = MediaInfo(duration=10, width=1920, height=1080, fps=30, has_audio=True)


@pytest.fixture(scope="module")
def real(tmp_media_dir: Path):
    if not ensure_face_models()["yunet"]["available"] or face_photo("astronaut.png") is None \
            or face_photo("armstrong.jpg") is None:
        pytest.skip("face model or portraits unavailable")
    return lambda kind: analysed(kind, tmp_media_dir)


def _contains(outer: Rect, inner: Rect) -> bool:
    return (outer.x <= inner.x + 1e-4 and outer.y <= inner.y + 1e-4 and outer.x + outer.w >= inner.x + inner.w - 1e-4
            and outer.y + outer.h >= inner.y + inner.h - 1e-4)


def _safe(cfg, vision, media) -> bool:
    return not blocking_warnings(analyze_protection(cfg, vision, source_w=media.width, source_h=media.height,
                                                    duration=media.duration))


def test_streamer_gets_facecam_gameplay(real) -> None:
    _, media, vision = real("streamer")
    cfg = suggest_framing(vision, media, media.duration)
    assert cfg.mode == "facecam_gameplay"
    fg = cfg.facecam_gameplay
    assert all(_contains(fg.facecam, b.rect) for b in vision.tracks[0].boxes)
    assert fg.facecam_position == "top" and 0.25 <= fg.split <= 0.45
    if fg.gameplay_fit == "cover":
        assert fg.gameplay.intersection_area(fg.facecam) == 0, "gameplay panel avoids the facecam"
    assert _safe(cfg, vision, media)


def test_podcast_gets_smart_crop_with_keyframes(real) -> None:
    _, media, vision = real("podcast")
    cfg = suggest_framing(vision, media, media.duration)
    assert cfg.mode == "smart" and cfg.smart.primary_track_id == vision.tracks[0].id
    assert len(cfg.smart.keyframes) > 5
    for b in vision.tracks[0].boxes:
        assert _contains(interpolate_keyframes(cfg.smart.keyframes, b.t), b.rect)
    assert _safe(cfg, vision, media)


def test_two_people_both_visible(real) -> None:
    _, media, vision = real("two")
    cfg = suggest_framing(vision, media, media.duration)
    assert cfg.mode in ("facecam_gameplay", "smart")
    if cfg.mode == "facecam_gameplay":  # stacked two-person layout: one person per panel
        fg = cfg.facecam_gameplay
        left, right = sorted(vision.tracks, key=lambda t: t.mean_center[0])
        assert all(_contains(fg.facecam, b.rect) for b in left.boxes)
        assert all(_contains(fg.gameplay, b.rect) for b in right.boxes)
    else:
        assert cfg.smart.auto_switch
    assert _safe(cfg, vision, media)


def test_deterministic(real) -> None:
    _, media, vision = real("streamer")
    assert suggest_framing(vision, media, media.duration) == suggest_framing(vision, media, media.duration)


def _track(tid: int, face: Rect, prominence: float = 0.2) -> FaceTrack:
    return FaceTrack(id=tid, label=f"Person {tid}", boxes=[FaceBox(t=i * 0.25, rect=face) for i in range(41)],
                     coverage=1.0, mean_area=face.area, mean_center=(face.cx, face.cy), prominence=prominence,
                     is_primary=tid == 1)


def _vision(tracks, timeline=None) -> ClipVisionAnalysis:
    return ClipVisionAnalysis(detector="yunet", sample_fps=4, source_width=1920, source_height=1080, clip_start=0,
                              clip_end=10, tracks=tracks, speaker_timeline=timeline or [])


def test_no_vision_no_faces_and_portrait_are_full_frame() -> None:
    assert suggest_framing(None, MEDIA, 10).mode == "full_frame"
    assert suggest_framing(_vision([]), MEDIA, 10).mode == "full_frame"
    portrait = MediaInfo(duration=10, width=1080, height=1920, fps=30, has_audio=True)
    assert suggest_framing(_vision([_track(1, Rect(x=0.4, y=0.3, w=0.2, h=0.1))]), portrait, 10).mode == "full_frame"


def test_falls_back_to_full_frame_when_a_face_cannot_fit() -> None:
    huge = _track(1, Rect(x=0.3, y=0.1, w=0.4, h=0.7))  # wider than any 9:16 crop of the frame
    assert suggest_framing(_vision([huge]), MEDIA, 10).mode == "full_frame"


def test_three_people_without_speaker_info_fall_back() -> None:
    tracks = [_track(1, Rect(x=0.1, y=0.3, w=0.06, h=0.11)), _track(2, Rect(x=0.47, y=0.3, w=0.06, h=0.11)),
              _track(3, Rect(x=0.85, y=0.3, w=0.06, h=0.11))]
    assert suggest_framing(_vision(tracks), MEDIA, 10).mode == "full_frame"


def test_distinguishable_speakers_get_auto_switch() -> None:
    a, b = _track(1, Rect(x=0.3, y=0.3, w=0.06, h=0.11)), _track(2, Rect(x=0.55, y=0.3, w=0.06, h=0.11), 0.19)
    timeline = [SpeakerSegment(start=0, end=5, track_id=1), SpeakerSegment(start=5, end=10, track_id=2)]
    cfg = suggest_framing(_vision([a, b], timeline), MEDIA, 10)
    assert cfg.mode == "smart" and cfg.smart.auto_switch
    assert sum(k.cut for k in cfg.smart.keyframes) == 1
