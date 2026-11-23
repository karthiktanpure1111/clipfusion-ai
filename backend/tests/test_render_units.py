"""Rendering building blocks: timeline, compositor, face mapping, audio graph."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from app.core.errors import ValidationFailed
from app.modules.rendering.audio import AudioPiece, build_audio_command
from app.modules.rendering.caption_track import face_rect_at, visible_faces
from app.modules.rendering.compositor import Compositor, warp_region
from app.modules.rendering.renderer import decode_size
from app.modules.rendering.timeline import output_fps, plan_frames, removed_silences
from app.modules.rendering.video_io import fps_fraction
from app.schemas.framing import Background, CompiledLayer, CompiledLayout, FaceBox, FaceTrack, Keyframe, Rect
from app.schemas.media import MediaInfo
from app.schemas.render import WatermarkOptions


def test_output_fps_and_fractions() -> None:
    assert output_fps(29.97, 60) == Fraction(30000, 1001)
    assert output_fps(60.0, 30) == Fraction(30)
    assert output_fps(0.0, 60) == Fraction(30)
    assert fps_fraction(23.976) == Fraction(24000, 1001)
    assert fps_fraction(25.0) == Fraction(25)


def test_removed_silences_filters_shrinks_and_converts_to_clip_time() -> None:
    sil = [(9.0, 9.5), (11.0, 12.0), (12.5, 12.6), (14.0, 20.0), (3.0, 4.0)]
    out = removed_silences(sil, clip_start=10.0, clip_end=16.0, min_silence=0.6, padding=0.1)
    assert out == [(pytest.approx(1.1), pytest.approx(1.9)), (pytest.approx(4.1), pytest.approx(5.9))]
    assert removed_silences([(1.0, 1.5)], clip_start=0, clip_end=5, min_silence=0.6, padding=0.1) == []


def test_plan_frames_snaps_to_grid() -> None:
    plan = plan_frames(6.0, Fraction(30), [(2.12, 3.38)])
    assert plan.total_frames == 180
    assert plan.ranges == [(0, 64), (102, 180)]
    assert plan.output_frames == 142
    assert plan.kept_clip_ranges()[1][0] == pytest.approx(3.4)
    assert plan_frames(2.0, Fraction(30), [(0.0, 2.0)]).ranges == [(0, 60)]  # all silent: keep everything
    assert sum(plan.kept_mask()) == 142


def _frame(w: int = 320, h: int = 180) -> np.ndarray:
    """Left half red, right half blue (BGR)."""
    f = np.zeros((h, w, 3), np.uint8)
    f[:, : w // 2] = (0, 0, 255)
    f[:, w // 2:] = (255, 0, 0)
    return f


def test_contain_layer_on_colour_background() -> None:
    layout = CompiledLayout(width=360, height=640, background=Background(type="color", color="#00FF00"),
                            layers=[CompiledLayer(id="main", fit="contain")])
    canvas = Compositor(layout, 320, 180).compose(_frame(), 0.0)
    assert canvas.shape == (640, 360, 3)
    assert tuple(canvas[10, 180]) == (0, 255, 0)            # letterbox shows the background
    assert tuple(canvas[320, 40]) == (0, 0, 255)            # red half
    assert tuple(canvas[320, 320]) == (255, 0, 0)           # blue half
    band = np.nonzero((canvas[:, 180] != (0, 255, 0)).any(axis=1))[0]
    assert band.min() == pytest.approx(320 - 101, abs=2) and band.max() == pytest.approx(320 + 100, abs=2)


def test_blur_background_is_blurred_and_dimmed() -> None:
    layout = CompiledLayout(width=360, height=640, background=Background(type="blur", blur=40, dim=0.5),
                            layers=[CompiledLayer(id="main", fit="contain", dest=Rect(x=0.25, y=0.4, w=0.5, h=0.2))])
    canvas = Compositor(layout, 320, 180).compose(_frame(), 0.0)
    top = canvas[5:100].astype(int)
    assert top[..., 2].max() <= 128 + 2 and top[..., 0].max() <= 128 + 2   # dimmed by 50 %
    row = canvas[50, :, 2].astype(int)
    assert (np.diff(row) != 0).sum() > 20                    # smooth gradient, not a hard edge


def test_cover_with_keyframes_radius_and_border() -> None:
    kf = [Keyframe(t=0.0, rect=Rect(x=0.0, y=0.0, w=0.25, h=1.0)),
          Keyframe(t=1.0, rect=Rect(x=0.75, y=0.0, w=0.25, h=1.0))]
    layer = CompiledLayer(id="main", fit="cover", keyframes=kf, dest=Rect(x=0.1, y=0.1, w=0.8, h=0.5), radius=60,
                          border_width=8, border_color="#FFFFFF")
    layout = CompiledLayout(width=360, height=640, background=Background(type="color", color="#000000"),
                            layers=[layer])
    comp = Compositor(layout, 320, 180)
    start, end = comp.compose(_frame(), 0.0), comp.compose(_frame(), 1.0)
    centre = (int(0.35 * 640), 180)
    assert start[centre][2] > 200 and end[centre][0] > 200    # pans from the red to the blue half
    assert tuple(start[int(0.1 * 640) + 1, 36 + 1]) == (0, 0, 0)  # rounded corner stays background
    assert (start[int(0.35 * 640), 36 + 1] > 240).all()       # border (8 px @1080 = 2.7 px here)


def test_warp_region_subpixel_and_downscale() -> None:
    frame = np.tile(np.arange(400, dtype=np.uint8)[None, :, None] // 2, (100, 1, 3))
    patch, x, y = warp_region(frame, (0.0, 0.0, 400.0, 100.0), (0.0, 0.0, 100.0, 25.0), 200, 200)
    assert patch.shape == (25, 100, 3) and (x, y) == (0, 0)
    assert abs(int(patch[10, 50, 0]) - 100) <= 2
    assert warp_region(frame, (0, 0, 10, 10), (500, 500, 10, 10), 200, 200) is None


def test_watermark_text_and_image(tmp_path: Path) -> None:
    import cv2

    layout = CompiledLayout(width=360, height=640, background=Background(type="color", color="#000000"),
                            layers=[CompiledLayer(id="main", fit="contain")])
    comp = Compositor(layout, 320, 180)
    comp.set_watermark(WatermarkOptions(enabled=True, text="@clipfusion", position="top_right", opacity=1.0), None)
    assert comp.watermark is not None and comp.watermark.x > 180 and comp.watermark.y < 60
    png = tmp_path / "wm.png"
    img = np.zeros((40, 80, 4), np.uint8)
    img[..., 1], img[..., 3] = 255, 255
    cv2.imwrite(str(png), img)
    comp.set_watermark(WatermarkOptions(enabled=True, position="bottom_left", scale=0.5, opacity=0.5), png)
    canvas = comp.compose(_frame(), 0)
    comp.draw_watermark(canvas)
    wm = comp.watermark
    assert wm.alpha.shape[1] == 180 and wm.x == round(0.04 * 360)
    assert canvas[wm.y + 5, wm.x + 5, 1] == pytest.approx(128, abs=2)
    with pytest.raises(ValidationFailed):
        comp.set_watermark(WatermarkOptions(enabled=True), tmp_path / "missing.png")


def test_background_image_required() -> None:
    layout = CompiledLayout(background=Background(type="image"), layers=[CompiledLayer(id="main")])
    with pytest.raises(ValidationFailed):
        Compositor(layout, 320, 180)


def test_decode_size_tracks_needed_resolution() -> None:
    media = MediaInfo(duration=10, width=3840, height=2160, fps=30, has_audio=True)
    full = CompiledLayout(layers=[CompiledLayer(id="main", fit="contain")])
    assert decode_size(full, media) == (1134, 638)  # 1080/3840 * 1.05 of 4K
    crop = CompiledLayout(layers=[CompiledLayer(id="main", source=Rect(x=0.4, y=0, w=0.15, h=0.5), fit="cover")])
    assert decode_size(crop, media) == (3840, 2160)


def test_face_interpolation_and_canvas_mapping() -> None:
    track = FaceTrack(id=1, boxes=[FaceBox(t=0.0, rect=Rect(x=0.4, y=0.1, w=0.1, h=0.2)),
                                   FaceBox(t=1.0, rect=Rect(x=0.6, y=0.1, w=0.1, h=0.2))])
    assert face_rect_at(track, 0.5).x == pytest.approx(0.5)
    assert face_rect_at(track, 1.5).x == pytest.approx(0.6)
    assert face_rect_at(track, 5.0) is None
    layout = CompiledLayout(layers=[CompiledLayer(id="main", fit="contain")])
    rects = visible_faces([track], layout, 0.5, 1920, 1080)
    assert len(rects) == 1 and rects[0].x == pytest.approx(0.5) and 0.3 < rects[0].y < 0.4
    hidden = CompiledLayout(layers=[CompiledLayer(id="main", source=Rect(x=0.0, y=0, w=0.2, h=1), fit="cover")])
    assert visible_faces([track], hidden, 0.5, 1920, 1080) == []


def test_audio_command_graph(tmp_path: Path) -> None:
    src = tmp_path / "a.mp4"
    pieces = [AudioPiece(duration=1.5), AudioPiece(duration=3.0, path=src, ranges=[(10.0, 11.0), (12.0, 14.0)])]
    cmd = build_audio_command(pieces, tmp_path / "o.m4a", normalize=True, target_lufs=-14, bitrate_kbps=192)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-ss") + 1] == "9.500000"
    assert "anullsrc" in graph and "asplit=2" in graph and "concat=n=2:v=0:a=1" in graph
    assert "atrim=start=0.500000:end=1.500000" in graph and "atrim=start=2.500000:end=4.500000" in graph
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in graph and "apad=whole_dur=3.000000" in graph
    silent = build_audio_command([AudioPiece(duration=2.0)], tmp_path / "s.m4a", normalize=True, target_lufs=-14,
                                 bitrate_kbps=128)
    assert "loudnorm" not in " ".join(silent)
