"""Clip vision analysis on real FFmpeg-encoded videos built from public-domain portraits,
plus unit tests of association, fragment merging and the speaker timeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.core.errors import ValidationFailed
from app.modules.faces.models import ensure_face_models
from app.modules.tracking.association import Observation, associate, merge_fragments
from app.modules.tracking.speakers import speaker_timeline
from app.modules.tracking.tracker import analyze_clip_vision
from app.schemas.framing import Rect
from app.schemas.media import MediaInfo
from tests.helpers.vision_media import analysed, face_photo

# the bordered facecam composited by make_streamer_video (incl. its 8 px white border)
STREAMER_OVERLAY = Rect(x=1464 / 1920, y=724 / 1080, w=416 / 1920, h=316 / 1080)


@pytest.fixture(scope="module", autouse=True)
def _assets() -> None:
    if not ensure_face_models()["yunet"]["available"] or face_photo("astronaut.png") is None \
            or face_photo("armstrong.jpg") is None:
        pytest.skip("face model or test portraits unavailable (offline)")


def _iou(a: Rect, b: Rect) -> float:
    inter = a.intersection_area(b)
    return inter / (a.area + b.area - inter)


def _contains(outer: Rect, inner: Rect, tol: float = 1e-3) -> bool:
    return (outer.x <= inner.x + tol and outer.y <= inner.y + tol and outer.x + outer.w >= inner.x + inner.w - tol
            and outer.y + outer.h >= inner.y + inner.h - tol)


def test_streamer_facecam_detected(tmp_media_dir: Path) -> None:
    _, media, vision = analysed("streamer", tmp_media_dir)
    assert vision.detector == "yunet" and vision.source_width == 1920 and vision.source_height == 1080
    assert len(vision.tracks) == 1
    cam = vision.tracks[0]
    assert cam.is_facecam and cam.is_primary and cam.label == "Facecam"
    assert cam.coverage > 0.9 and cam.mean_area < 0.06
    assert cam.facecam_rect is not None
    assert all(_contains(cam.facecam_rect, b.rect) for b in cam.boxes), "facecam_rect must contain the face"
    assert _iou(cam.facecam_rect, STREAMER_OVERLAY) > 0.75, cam.facecam_rect
    assert vision.layout_hint == "facecam_gameplay"
    assert vision.suggested_mode == "facecam_gameplay"
    assert vision.speaker_timeline and vision.speaker_timeline[0].track_id == cam.id


def test_streamer_activity_grid_excludes_facecam(tmp_media_dir: Path) -> None:
    _, _, vision = analysed("streamer", tmp_media_dir)
    grid = vision.activity
    assert grid is not None and grid.cols == 16 and grid.rows == 9
    arr = np.array(grid.values).reshape(9, 16)
    assert abs(arr.sum() - 1) < 1e-3
    assert arr[7:, 13:].sum() < 0.01  # the facecam corner is excluded
    assert arr[:, :12].sum() > 0.8    # testsrc2 motion is left/centre


def test_podcast_single_stable_track(tmp_media_dir: Path) -> None:
    _, media, vision = analysed("podcast", tmp_media_dir)
    assert len(vision.tracks) == 1
    tr = vision.tracks[0]
    assert not tr.is_facecam and tr.coverage > 0.9
    xs = [b.rect.cx for b in tr.boxes]
    assert xs[-1] - xs[0] > 0.4, "the person walks left to right"
    assert np.all(np.diff(xs) > -0.01), "a single, continuous track"
    times = [b.t for b in tr.boxes]
    assert times == sorted(times) and times[0] < 0.3 and times[-1] <= media.duration
    assert vision.layout_hint == "single_speaker"
    assert vision.suggested_mode == "smart"


def test_two_people_two_tracks(tmp_media_dir: Path) -> None:
    _, _, vision = analysed("two", tmp_media_dir)
    assert len(vision.tracks) == 2
    xs = sorted(t.mean_center[0] for t in vision.tracks)
    assert xs[0] < 0.3 and xs[1] > 0.7
    assert not any(t.is_facecam for t in vision.tracks)
    assert {t.label for t in vision.tracks} == {"Person 1", "Person 2"}
    assert sum(t.is_primary for t in vision.tracks) == 1
    assert vision.layout_hint == "multi_speaker"


def test_clip_range_progress_and_speech(tmp_media_dir: Path) -> None:
    path, media, _ = analysed("podcast", tmp_media_dir)
    calls: list[float] = []
    vision = analyze_clip_vision(path, 2.0, 6.0, media, sample_fps=2.0, speech=[(1.0, 4.0), (5.5, 9.0)],
                                 progress=lambda f, _m: calls.append(f))
    assert vision.clip_start == 2.0 and vision.clip_end == 6.0 and vision.sample_fps == 2.0
    boxes = vision.tracks[0].boxes
    assert 7 <= len(boxes) <= 9 and all(0 <= b.t <= 4.0 for b in boxes)
    # clip t=0 is source t=2: the face must be where the full analysis saw it at t=2
    _, _, full = analysed("podcast", tmp_media_dir)
    ref = min(full.tracks[0].boxes, key=lambda b: abs(b.t - 2.0))
    assert abs(boxes[0].rect.cx - ref.rect.cx) < 0.01
    assert calls and calls[-1] == 1.0 and calls == sorted(calls)


def test_invalid_inputs(tmp_media_dir: Path) -> None:
    path, media, _ = analysed("podcast", tmp_media_dir)
    with pytest.raises(ValidationFailed):
        analyze_clip_vision(path, 3.0, 2.0, media)
    audio_only = MediaInfo(duration=5, width=0, height=0, fps=0, has_audio=True)
    with pytest.raises(ValidationFailed):
        analyze_clip_vision(path, 0.0, 2.0, audio_only)


# ------------------------------------------------------------------ units
def _obs(t: float, x: float, y: float = 0.4, s: float = 0.1, mouth: float = 0.0) -> Observation:
    return Observation(t=t, rect=Rect(x=x, y=y, w=s, h=s * 16 / 9), score=0.9, mouth=mouth)


def test_association_bridges_gaps_and_separates_people() -> None:
    frames = []
    for i in range(20):
        t = i * 0.25
        dets = [_obs(t, 0.1 + 0.005 * i)]
        if not 2.0 <= t <= 3.0:  # person B missing for 1 s (within the 1.5 s gap)
            dets.append(_obs(t, 0.7))
        frames.append((t, dets))
    tracks = merge_fragments(associate(frames, aspect=16 / 9), aspect=16 / 9)
    assert len(tracks) == 2
    assert sorted(len(t.obs) for t in tracks) == [15, 20]


def test_fragments_of_a_static_face_are_merged_after_long_gaps() -> None:
    frames = [(i * 0.25, [_obs(i * 0.25, 0.8, 0.75, 0.05)]) for i in range(40) if not 10 <= i < 25]
    tracks = merge_fragments(associate(frames, aspect=16 / 9), aspect=16 / 9)
    assert len(tracks) == 1 and len(tracks[0].obs) == 25


def test_speaker_timeline_hysteresis() -> None:
    a = [_obs(i * 0.25, 0.1, mouth=6.0 if i * 0.25 < 5 else 0.0) for i in range(48)]
    b = [_obs(i * 0.25, 0.7, mouth=0.0 if i * 0.25 < 5 else 6.0) for i in range(48)]
    # a short 1 s burst from A after the switch must not cause a switch back
    for o in a:
        if 8.0 <= o.t < 9.0:
            o.mouth = 9.0
    for o in b:
        if 8.0 <= o.t < 9.0:
            o.mouth = 0.0
    segs = speaker_timeline({1: a, 2: b}, duration=12.0, step=0.25, speech=None, default_track=1)
    assert [s.track_id for s in segs] == [1, 2]
    assert 4.5 <= segs[1].start <= 5.5 and segs[0].start == 0.0 and segs[-1].end == 12.0


def test_single_track_timeline_covers_clip() -> None:
    segs = speaker_timeline({3: [_obs(0, 0.1)]}, duration=7.0, step=0.25, speech=None, default_track=3)
    assert [(s.start, s.end, s.track_id) for s in segs] == [(0.0, 7.0, 3)]
