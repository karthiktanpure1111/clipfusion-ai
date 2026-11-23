"""default_framing_config / compile_layout unit cases and the exported TS fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.config import REPO_DIR
from app.core.errors import ValidationFailed
from app.modules.framing.geometry import fit_rects, rect_at, to_px
from app.modules.framing.layout import compile_layout, default_framing_config, full_frame_dest, panel_dests
from app.schemas.framing import FULL_FRAME, FramingConfig, Keyframe, Rect

SIZES = [(1920, 1080), (1280, 720), (1080, 1920), (1440, 1080)]
MODES = ["smart", "manual", "facecam_gameplay", "pip", "full_frame"]


def _px_aspect(r: Rect, sw: int, sh: int) -> float:
    return (r.w * sw) / (r.h * sh)


@pytest.mark.parametrize("sw,sh", SIZES)
def test_manual_default_is_centred_output_aspect(sw: int, sh: int) -> None:
    crop = default_framing_config(sw, sh, "manual").manual.crop
    assert abs(_px_aspect(crop, sw, sh) - 9 / 16) < 1e-3 or crop.w == 1.0
    assert abs(crop.cx - 0.5) < 1e-5 and abs(crop.cy - 0.5) < 1e-5
    assert crop.w <= 1 and crop.h <= 1
    if sw / sh > 9 / 16:
        assert crop.h == 1.0


def test_manual_default_1080p_values() -> None:
    crop = default_framing_config(1920, 1080, "manual").manual.crop
    assert (crop.x, crop.y, crop.w, crop.h) == (0.341797, 0.0, 0.316406, 1.0)


@pytest.mark.parametrize("sw,sh", SIZES)
def test_panel_sources_match_panel_aspects(sw: int, sh: int) -> None:
    cfg = default_framing_config(sw, sh, "facecam_gameplay")
    face_dest, game_dest = panel_dests(cfg.facecam_gameplay, cfg.output_height)
    for src, dest in ((cfg.facecam_gameplay.facecam, face_dest), (cfg.facecam_gameplay.gameplay, game_dest)):
        want = (dest.w * 1080) / (dest.h * 1920)
        assert abs(_px_aspect(src, sw, sh) - want) < 0.01
    pip = default_framing_config(sw, sh, "pip").pip
    want = (pip.overlay_dest.w * 1080) / (pip.overlay_dest.h * 1920)
    assert abs(_px_aspect(pip.overlay_source, sw, sh) - want) < 0.01


def test_unknown_mode_and_bad_source() -> None:
    with pytest.raises(ValidationFailed):
        default_framing_config(1920, 1080, "zoom")
    with pytest.raises(ValidationFailed):
        compile_layout(FramingConfig(), 0, 1080)


def test_compile_smart_without_and_with_keyframes() -> None:
    cfg = default_framing_config(1920, 1080, "smart")
    layout = compile_layout(cfg, 1920, 1080)
    (main,) = layout.layers
    assert main.id == "main" and main.fit == "cover" and main.dest == FULL_FRAME and not main.keyframes
    assert main.source == default_framing_config(1920, 1080, "manual").manual.crop
    keys = [Keyframe(t=0, rect=Rect(x=0.1, y=0, w=0.316406, h=1)),
            Keyframe(t=2, rect=Rect(x=0.5, y=0, w=0.316406, h=1), cut=True)]
    cfg.smart.keyframes = keys
    main = compile_layout(cfg, 1920, 1080).layers[0]
    assert rect_at(main, 1.99).x == 0.1 and rect_at(main, 2.0).x == 0.5  # hard cut


def test_compile_manual_uses_fit_and_keyframes() -> None:
    cfg = default_framing_config(1920, 1080, "manual")
    cfg.manual.fit = "contain"
    cfg.manual.crop = Rect(x=0.2, y=0.1, w=0.4, h=0.5)
    layout = compile_layout(cfg, 1920, 1080)
    assert layout.layers[0].fit == "contain" and layout.layers[0].source == cfg.manual.crop
    assert layout.background_source == cfg.manual.crop


@pytest.mark.parametrize("position", ["top", "bottom"])
def test_compile_facecam_gameplay_bands(position: str) -> None:
    cfg = default_framing_config(1920, 1080, "facecam_gameplay")
    cfg.facecam_gameplay.facecam_position = position
    cfg.facecam_gameplay.split = 0.4
    cfg.facecam_gameplay.gap = 48
    layout = compile_layout(cfg, 1920, 1080)
    face, game = layout.layers
    assert (face.id, face.role, face.z) == ("facecam", "face", 0)
    assert (game.id, game.role, game.z) == ("gameplay", "content", 1)
    assert face.dest.h == pytest.approx(0.4) and game.dest.h == pytest.approx(1 - 0.4 - 48 / 1920)
    if position == "top":
        assert face.dest.y == 0 and game.dest.y == pytest.approx(0.4 + 48 / 1920)
    else:
        assert game.dest.y == 0 and face.dest.y == pytest.approx(0.6)
    assert layout.background_source == cfg.facecam_gameplay.gameplay


def test_compile_pip_overlay_styling() -> None:
    cfg = default_framing_config(1920, 1080, "pip")
    layout = compile_layout(cfg, 1920, 1080)
    main, overlay = layout.layers
    assert main.fit == "contain" and main.dest == FULL_FRAME and overlay.z > main.z
    assert overlay.role == "face" and overlay.dest == cfg.pip.overlay_dest
    assert overlay.radius == 28 and overlay.border_width == 4 and overlay.shadow


@pytest.mark.parametrize("sw,sh", SIZES)
def test_full_frame_never_crops(sw: int, sh: int) -> None:
    cfg = default_framing_config(sw, sh, "full_frame")
    layout = compile_layout(cfg, sw, sh)
    (main,) = layout.layers
    crop, draw = fit_rects(to_px(main.source, sw, sh), to_px(main.dest, 1080, 1920), main.fit)
    assert crop == (0, 0, sw, sh)  # the whole frame is drawn
    assert draw[2] <= 1080 + 1e-6 and draw[3] <= 1920 + 1e-6
    assert layout.background_source == FULL_FRAME


def test_full_frame_scale_and_vertical_position() -> None:
    cfg = default_framing_config(1920, 1080, "full_frame")
    cfg.full_frame.scale = 0.8
    cfg.full_frame.vertical_position = 0.0
    dest = full_frame_dest(cfg, 1920, 1080)
    assert dest.w == 0.8 and dest.x == pytest.approx(0.1) and dest.y == 0.0
    assert dest.h == pytest.approx(0.8 * 1080 * 1080 / 1920 / 1920, abs=1e-6)
    cfg.full_frame.vertical_position = 1.0
    dest = full_frame_dest(cfg, 1920, 1080)
    assert dest.y + dest.h == pytest.approx(1.0)


def test_exported_fixtures_are_current() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import export_framing_fixtures as exporter

    path = REPO_DIR / "shared" / "fixtures" / "framing_cases.json"
    if not path.exists():
        pytest.fail("run backend/scripts/export_framing_fixtures.py")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(exporter.build()))
    assert on_disk == fresh, "fixtures are stale: run backend/scripts/export_framing_fixtures.py"
    assert {c["name"] for c in fresh["compile_layout"]} >= {f"default_{m}" for m in MODES}
    assert any(k["cut"] for case in fresh["rect_at"] for k in case["layer"]["keyframes"])
