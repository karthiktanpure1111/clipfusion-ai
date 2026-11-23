"""Crop protection on real analysed videos and synthetic vision data."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.modules.faces.models import ensure_face_models
from app.modules.framing.layout import default_framing_config
from app.modules.framing.protection import analyze_protection, time_ranges
from app.schemas.framing import (
    ClipVisionAnalysis,
    FaceBox,
    FaceTrack,
    FramingConfig,
    Rect,
    blocking_warnings,
)
from app.schemas.subtitles import Caption, CaptionWord, SubtitleStyle
from tests.helpers.vision_media import analysed, face_photo

FACE_CODES = {"face_outside", "head_cut", "streamer_hidden"}


def apply_patch(cfg: FramingConfig, patch: dict) -> FramingConfig:
    """Deep-merge a FramingSuggestion.config_patch like the frontend does."""
    def merge(a: dict, b: dict) -> dict:
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out
    return FramingConfig.model_validate(merge(cfg.model_dump(mode="json"), patch))


@pytest.fixture(scope="module")
def real(tmp_media_dir: Path):
    if not ensure_face_models()["yunet"]["available"] or face_photo("astronaut.png") is None \
            or face_photo("armstrong.jpg") is None:
        pytest.skip("face model or portraits unavailable")
    return lambda kind: analysed(kind, tmp_media_dir)


def _check(cfg: FramingConfig, vision, media, **kw):
    return analyze_protection(cfg, vision, source_w=media.width, source_h=media.height, duration=media.duration,
                              **kw)


def test_manual_centre_crop_hides_streamer_with_fixes(real) -> None:
    _, media, vision = real("streamer")
    cfg = default_framing_config(1920, 1080, "manual")
    warnings = _check(cfg, vision, media)
    codes = {(w.code, w.severity) for w in warnings}
    assert ("face_outside", "warning") in codes and ("streamer_hidden", "critical") in codes
    assert ("content_outside", "warning") in codes
    face = next(w for w in warnings if w.code == "face_outside")
    assert face.track_id == vision.tracks[0].id and face.affected_fraction > 0.9
    assert face.visible_fraction < 0.05 and face.time_ranges[0][0] == 0.0
    actions = {s.action for s in face.suggestions}
    assert {"switch_mode"} <= actions and face.suggestions[-1].config_patch == {"mode": "full_frame"}
    for fix in face.suggestions:  # every one-click fix must actually make the streamer visible
        fixed = _check(apply_patch(cfg, fix.config_patch), vision, media)
        assert not [w for w in fixed if w.code in FACE_CODES and w.severity != "info"], fix.label


@pytest.mark.parametrize("kind", ["streamer", "podcast", "two"])
def test_full_frame_never_warns_about_faces(real, kind: str) -> None:
    _, media, vision = real(kind)
    warnings = _check(default_framing_config(1920, 1080, "full_frame"), vision, media)
    assert not blocking_warnings(warnings)
    assert not [w for w in warnings if w.code in FACE_CODES]


def test_smart_on_podcast_is_safe(real) -> None:
    _, media, vision = real("podcast")
    warnings = _check(default_framing_config(1920, 1080, "smart"), vision, media)
    assert not blocking_warnings(warnings), [w.code for w in warnings]


def test_two_people_single_follow_warns_unless_explicit(real) -> None:
    _, media, vision = real("two")
    cfg = default_framing_config(1920, 1080, "smart")
    warnings = _check(cfg, vision, media)
    other = [w for w in warnings if w.code == "face_outside" and w.severity == "warning"]
    assert len(other) == 1
    assert "enable_auto_switch" in {s.action for s in other[0].suggestions}
    cfg.smart.primary_track_id = vision.tracks[0].id  # the user chose whom to follow
    warnings = _check(cfg, vision, media)
    assert not blocking_warnings(warnings)
    assert any(w.code == "face_outside" and w.severity == "info" for w in warnings)


def test_missing_and_empty_vision() -> None:
    cfg = default_framing_config(1920, 1080, "manual")
    (w,) = analyze_protection(cfg, None, source_w=1920, source_h=1080, duration=12)
    assert (w.code, w.severity, w.time_ranges) == ("vision_missing", "info", [(0.0, 12.0)]) and w.suggestions
    empty = ClipVisionAnalysis(detector="yunet", sample_fps=4, source_width=1920, source_height=1080,
                               clip_start=0, clip_end=12)
    (w,) = analyze_protection(cfg, empty, source_w=1920, source_h=1080, duration=12)
    assert w.code == "no_faces_detected" and w.severity == "info"


def _static_vision(face: Rect, **track_kw) -> ClipVisionAnalysis:
    boxes = [FaceBox(t=i * 0.25, rect=face) for i in range(40)]
    track = FaceTrack(id=1, label="Person 1", boxes=boxes, coverage=1.0, mean_area=face.area,
                      mean_center=(face.cx, face.cy), prominence=0.2, is_primary=True, **track_kw)
    return ClipVisionAnalysis(detector="yunet", sample_fps=4, source_width=1920, source_height=1080,
                              clip_start=0, clip_end=10, tracks=[track])


def test_head_cut_detected_and_move_fix() -> None:
    cfg = default_framing_config(1920, 1080, "manual")
    crop = cfg.manual.crop
    face = Rect(x=crop.x + crop.w - 0.03, y=0.3, w=0.06, h=0.11)  # half the face outside the right edge
    warnings = analyze_protection(cfg, _static_vision(face), source_w=1920, source_h=1080, duration=10)
    cut = next(w for w in warnings if w.code == "head_cut")
    assert 0.45 <= cut.visible_fraction <= 0.6
    move = next(s for s in cut.suggestions if s.action == "move_crop")
    fixed = analyze_protection(apply_patch(cfg, move.config_patch), _static_vision(face), source_w=1920,
                               source_h=1080, duration=10)
    assert not blocking_warnings(fixed)


def test_pip_overlay_occluding_face_is_detected() -> None:
    cfg = default_framing_config(1920, 1080, "pip")
    cfg.pip.main = Rect(x=0.341797, y=0.0, w=0.316406, h=1.0)
    cfg.pip.main_fit = "cover"
    face = Rect(x=0.37, y=0.07, w=0.05, h=0.09)  # lands under the top-left overlay on the canvas
    warnings = analyze_protection(cfg, _static_vision(face), source_w=1920, source_h=1080, duration=10)
    assert any(w.code == "face_outside" for w in warnings)
    cfg.pip.overlay_dest = Rect(x=0.55, y=0.7, w=0.4, h=0.2)
    warnings = analyze_protection(cfg, _static_vision(face), source_w=1920, source_h=1080, duration=10)
    assert not [w for w in warnings if w.code in FACE_CODES]


def test_upscale_quality_info() -> None:
    cfg = default_framing_config(1920, 1080, "facecam_gameplay")
    cfg.facecam_gameplay.facecam = Rect(x=0.8, y=0.8, w=0.12, h=0.12)
    face = Rect(x=0.83, y=0.82, w=0.04, h=0.07)
    warnings = analyze_protection(cfg, _static_vision(face), source_w=1920, source_h=1080, duration=10)
    up = next(w for w in warnings if w.code == "upscale_quality")
    assert up.severity == "info" and up.suggestions


@dataclass
class _FakeLayout:
    block: Rect
    font_size: float = 72.0
    lines: list = field(default_factory=list)


def _install_fake_text_layout(monkeypatch: pytest.MonkeyPatch, block: Rect) -> list:
    calls: list = []
    mod = types.ModuleType("app.modules.subtitles.text_layout")

    def layout_caption(caption, style, canvas_w, canvas_h, *, avoid_rects=None, custom_fonts=()):
        calls.append((caption.id, canvas_w, canvas_h, avoid_rects))
        return _FakeLayout(block=block)

    mod.layout_caption = layout_caption
    monkeypatch.setitem(sys.modules, "app.modules.subtitles.text_layout", mod)
    return calls


def _captions() -> list[Caption]:
    return [Caption(start=1.0, end=2.5, words=[CaptionWord(text="hello", start=1.0, end=1.5),
                                               CaptionWord(text="there", start=1.5, end=2.5)])]


def test_subtitle_overlap_face(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = default_framing_config(1920, 1080, "full_frame")
    face = Rect(x=0.45, y=0.4, w=0.1, h=0.18)  # full_frame: canvas y ~ 0.47-0.53
    calls = _install_fake_text_layout(monkeypatch, Rect(x=0.1, y=0.45, w=0.8, h=0.1))
    warnings = analyze_protection(cfg, _static_vision(face), source_w=1920, source_h=1080, duration=10,
                                  subtitle_style=SubtitleStyle(), captions=_captions())
    sub = next(w for w in warnings if w.code == "subtitle_overlap_face")
    assert sub.severity == "warning" and sub.time_ranges == [(1.0, 2.5)] and sub.suggestions
    assert calls and calls[0][1:3] == (1080, 1920) and calls[0][3]  # faces passed as avoid_rects
    _install_fake_text_layout(monkeypatch, Rect(x=0.1, y=0.8, w=0.8, h=0.08))
    warnings = analyze_protection(cfg, _static_vision(face), source_w=1920, source_h=1080, duration=10,
                                  subtitle_style=SubtitleStyle(), captions=_captions())
    assert not any(w.code == "subtitle_overlap_face" for w in warnings)


def test_subtitle_check_skipped_without_layout_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "app.modules.subtitles.text_layout", None)
    warnings = analyze_protection(default_framing_config(1920, 1080, "full_frame"),
                                  _static_vision(Rect(x=0.45, y=0.4, w=0.1, h=0.18)), source_w=1920,
                                  source_h=1080, duration=10, subtitle_style=SubtitleStyle(), captions=_captions())
    assert not any(w.code == "subtitle_overlap_face" for w in warnings)


def test_time_ranges_merge() -> None:
    assert time_ranges([0.0, 0.25, 0.5, 2.0], 0.25, 10) == [(0.0, 0.62), (1.88, 2.12)]
