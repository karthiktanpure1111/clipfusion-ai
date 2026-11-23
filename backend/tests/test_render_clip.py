"""End-to-end renders on real FFmpeg-generated media (no mocks)."""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.core.errors import ValidationFailed
from app.core.ffmpeg import ffmpeg_bin, ffprobe_bin
from app.modules.analysis.probe import probe_media
from app.modules.framing.geometry import fit_rects, to_px
from app.modules.rendering.renderer import render_clip, render_preview_frame
from app.modules.rendering.video_io import read_single_frame
from app.modules.subtitles.text_layout import layout_caption
from app.schemas.framing import Background, CompiledLayer, CompiledLayout, Keyframe, Rect
from app.schemas.render import RenderOptions, RenderSpec, WatermarkOptions
from app.schemas.subtitles import Caption, CaptionWord, ShadowStyle, SubtitlePosition, SubtitleStyle
from tests.helpers.media import make_test_video

W, H = 1080, 1920
FRAME = 1 / 30


@pytest.fixture(scope="module", autouse=True)
def _subtitle_fonts() -> None:
    """Caption rendering needs the OFL fonts (downloaded once; skipped when offline)."""
    from app.modules.subtitles.fonts import ensure_builtin_fonts, font_catalogue

    def ready() -> bool:
        return any(f["available"] and f["source"] == "google" for f in font_catalogue())

    if not ready():
        ensure_builtin_fonts()
    if not ready():
        pytest.skip("subtitle fonts are not installed and could not be downloaded")


@pytest.fixture(scope="module")
def media_dir(tmp_media_dir: Path) -> Path:
    d = tmp_media_dir / "render"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture(scope="module")
def src720(media_dir: Path) -> Path:
    return make_test_video(media_dir / "src720.mp4", duration=6.0, width=1280, height=720, fps=30)


def _streams(path: Path) -> dict:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, check=True).stdout
    data = json.loads(out)
    return {s["codec_type"]: s for s in data["streams"]} | {"format": data["format"]}


def _assert_mp4(path: Path, expected: float) -> dict:
    s = _streams(path)
    v, a = s["video"], s["audio"]
    assert (v["codec_name"], v["profile"], v["pix_fmt"]) == ("h264", "High", "yuv420p")
    assert (v["width"], v["height"]) == (W, H)
    assert v["r_frame_rate"] == "30/1"
    assert a["codec_name"] == "aac" and a["sample_rate"] == "48000" and a["channels"] == 2
    vd, ad = float(v["duration"]), float(a["duration"])
    assert abs(vd - expected) <= FRAME + 1e-3, (vd, expected)
    assert abs(ad - vd) <= 0.03, (ad, vd)  # audio in sync with video (<= 1 AAC frame)
    head = path.read_bytes()[:4096]
    assert head.find(b"moov") != -1 and head.find(b"moov") < max(head.find(b"mdat"), 4096)  # faststart
    return s


def _captions() -> list[Caption]:
    words = [CaptionWord(text=t, start=0.4 + i * 0.35, end=0.7 + i * 0.35) for i, t in enumerate(
        "burned captions are visible here".split())]
    return [Caption(id="cap1", start=0.4, end=2.4, words=words)]


def _style() -> SubtitleStyle:
    return SubtitleStyle(font_family="Montserrat", font_weight=900, font_size=80, text_color="#FFFFFF",
                         outline_width=8, position=SubtitlePosition(preset="bottom"), shadow=ShadowStyle())


def _contain_layout() -> CompiledLayout:
    return CompiledLayout(background=Background(type="blur", blur=40, dim=0.35),
                          layers=[CompiledLayer(id="main", fit="contain", dest=Rect(x=0, y=0.25, w=1, h=0.5))])


def _spec(src: Path, out: Path, layout: CompiledLayout, **kw) -> RenderSpec:
    options = kw.pop("options", RenderOptions(video_preset="veryfast", video_crf=23))
    return RenderSpec(source_path=src, start=kw.pop("start", 0.5), end=kw.pop("end", 3.5), media=probe_media(src),
                      layout=layout, options=options, output_path=out, **kw)


def test_full_frame_contain_with_burned_captions(src720: Path, media_dir: Path) -> None:
    with_subs = render_clip(_spec(src720, media_dir / "subs.mp4", _contain_layout(), captions=_captions(),
                                  style=_style()))
    without = render_clip(_spec(src720, media_dir / "nosubs.mp4", _contain_layout(), captions=_captions(),
                                style=_style(), subtitles_enabled=False))
    _assert_mp4(with_subs.output_path, 3.0)
    assert (with_subs.width, with_subs.height, with_subs.fps) == (W, H, 30.0)
    assert with_subs.kept_ranges == [(0.5, 3.5)]
    assert with_subs.thumbnail_path and cv2.imread(str(with_subs.thumbnail_path)).shape == (H, W, 3)
    a = read_single_frame(with_subs.output_path, 1.5, W, H).astype(int)
    b = read_single_frame(without.output_path, 1.5, W, H).astype(int)
    lay = layout_caption(_captions()[0], _style(), W, H)
    x, y, w, h = (int(v) for v in lay.block_px)
    caption_diff = np.abs(a[y:y + h, x:x + w] - b[y:y + h, x:x + w]).mean()
    elsewhere = np.abs(a[100:500] - b[100:500]).mean()
    assert caption_diff > 25 and elsewhere < 3, (caption_diff, elsewhere)
    white = (a[y:y + h, x:x + w] > 235).all(axis=2).sum()
    assert white > 2000  # white caption fill burned in
    # outside the caption window nothing is drawn
    late_a = read_single_frame(with_subs.output_path, 2.7, W, H).astype(int)
    late_b = read_single_frame(without.output_path, 2.7, W, H).astype(int)
    assert np.abs(late_a[y:y + h, x:x + w] - late_b[y:y + h, x:x + w]).mean() < 3


def test_cover_crop_with_moving_keyframes_and_watermark(src720: Path, media_dir: Path) -> None:
    kf = [Keyframe(t=0.0, rect=Rect(x=0.0, y=0.0, w=0.3164, h=1.0)),
          Keyframe(t=2.0, rect=Rect(x=0.6836, y=0.0, w=0.3164, h=1.0))]
    layout = CompiledLayout(layers=[CompiledLayer(id="main", fit="cover", keyframes=kf)])
    wm = WatermarkOptions(enabled=True, text="@clipfusion", position="top_right", opacity=1.0, scale=0.3)
    opts = RenderOptions(video_preset="veryfast", video_crf=20, watermark=wm)
    spec = _spec(src720, media_dir / "cover.mp4", layout, start=1.0, end=3.0, options=opts)
    res = render_clip(spec)
    _assert_mp4(res.output_path, 2.0)
    src = read_single_frame(src720, 1.0, 1280, 720)
    first = read_single_frame(res.output_path, 0.0, W, H)
    crop, _ = fit_rects(to_px(kf[0].rect, 1280, 720), (0, 0, W, H), "cover")
    cx, cy, cw, ch = (int(round(v)) for v in crop)
    expected = cv2.resize(src[cy:cy + ch, cx:cx + cw], (W, H), interpolation=cv2.INTER_LINEAR)
    assert np.abs(first[400:1900].astype(int) - expected[400:1900].astype(int)).mean() < 12
    last = read_single_frame(res.output_path, 1.95, W, H)
    assert np.abs(first[400:1900].astype(int) - last[400:1900].astype(int)).mean() > 20  # camera moved
    preview = media_dir / "cover_preview.jpg"
    no_wm = spec.model_copy(update={"options": RenderOptions()})
    render_preview_frame(no_wm, 0.0, preview)
    plain = cv2.imread(str(preview)).astype(int)
    region = (slice(40, 180), slice(W - 420, W - 30))
    diff = np.abs(first[region].astype(int) - plain[region]).max(axis=2)
    assert (diff > 60).sum() > 500  # watermark text burned in the top-right corner
    assert np.abs(first[1000:1100].astype(int) - plain[1000:1100]).mean() < 6


def test_two_band_stacked_layout(src720: Path, media_dir: Path) -> None:
    gap = 24
    face = CompiledLayer(id="facecam", role="face", source=Rect(x=0.0, y=0.0, w=0.25, h=0.3),
                         dest=Rect(x=0, y=0, w=1, h=0.35), fit="cover", z=1)
    game = CompiledLayer(id="gameplay", role="content", source=Rect(x=0.2, y=0.0, w=0.6, h=1.0),
                         dest=Rect(x=0, y=0.35 + gap / H, w=1, h=0.65 - gap / H), fit="cover", z=0)
    layout = CompiledLayout(background=Background(type="color", color="#202020"),
                            background_source=Rect(x=0.2, y=0, w=0.6, h=1), layers=[face, game])
    res = render_clip(_spec(src720, media_dir / "stacked.mp4", layout, start=2.0, end=3.5))
    _assert_mp4(res.output_path, 1.5)
    out = read_single_frame(res.output_path, 0.5, W, H).astype(int)
    src = read_single_frame(src720, 2.5, 1280, 720)
    for layer in (face, game):
        crop, draw = fit_rects(to_px(layer.source, 1280, 720), to_px(layer.dest, W, H), "cover")
        cx, cy, cw, ch = (int(round(v)) for v in crop)
        dx, dy, dw, dh = (int(round(v)) for v in draw)
        expected = cv2.resize(src[cy:cy + ch, cx:cx + cw], (dw, dh), interpolation=cv2.INTER_LINEAR).astype(int)
        got = out[dy + 8:dy + dh - 8, dx + 8:dx + dw - 8]
        assert np.abs(got - expected[8:dh - 8, 8:dw - 8]).mean() < 14, layer.id
    gap_row = out[int(0.35 * H) + gap // 2]
    assert np.abs(gap_row - 0x20).mean() < 6  # the gap shows the background colour


def _silences(path: Path, min_d: float = 0.1) -> list[float]:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af",
                           f"silencedetect=n=-45dB:d={min_d}", "-f", "null", "-"], capture_output=True)
    return [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", proc.stderr.decode("utf-8", "replace"))]


def test_remove_silence_shortens_output(media_dir: Path) -> None:
    src = make_test_video(media_dir / "gappy.mp4", duration=6.0, width=640, height=360, fps=30,
                          silent_ranges=[(2.0, 3.5)])
    opts = RenderOptions(video_preset="veryfast", remove_silence=True, min_silence=0.6, silence_padding=0.12,
                         normalize_audio=False)
    spec = _spec(src, media_dir / "nosilence.mp4", _contain_layout(), start=0.0, end=6.0, options=opts,
                 silences=[(2.0, 3.5), (5.9, 6.0)])
    res = render_clip(spec)
    # removed (2.12, 3.38) -> frames [64, 102) -> 142 of 180 frames remain
    _assert_mp4(res.output_path, 142 / 30)
    assert res.kept_ranges == [(0.0, pytest.approx(64 / 30, abs=1e-3)), (pytest.approx(3.4, abs=1e-3), 6.0)]
    assert _silences(src) and max(_silences(src)) > 1.4
    remaining = _silences(res.output_path)
    assert all(d < 0.4 for d in remaining), remaining  # only the padding around the cut is left


def test_intro_concat_and_silent_source(media_dir: Path) -> None:
    src = make_test_video(media_dir / "mute.mp4", duration=3.0, width=640, height=360, fps=30, with_audio=False)
    intro = media_dir / "intro.mp4"
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100:duration=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(intro)], check=True)
    spec = _spec(src, media_dir / "with_intro.mp4", _contain_layout(), start=0.5, end=2.5, intro_path=intro)
    res = render_clip(spec)
    _assert_mp4(res.output_path, 3.0)
    first = read_single_frame(res.output_path, 0.5, W, H)
    b, g, r = first[H // 2, W // 2]
    assert r > 200 and g < 60 and b < 60  # intro (red) first, contain-fitted
    later = read_single_frame(res.output_path, 2.0, W, H)
    assert not (later[H // 2, W // 2, 2] > 200 and later[H // 2, W // 2, 1] < 60)
    silences = _silences(res.output_path, 0.5)
    assert silences and silences[0] == pytest.approx(2.0, abs=0.1)  # tone during intro, then silent track


def test_validation_errors(src720: Path, media_dir: Path) -> None:
    with pytest.raises(ValidationFailed):
        render_clip(_spec(src720, media_dir / "x.mp4", _contain_layout(), start=2.0, end=2.0))
    with pytest.raises(ValidationFailed):
        render_clip(_spec(src720, media_dir / "x.mp4", CompiledLayout(layers=[])))
    spec = _spec(src720, media_dir / "x.mp4", _contain_layout())
    with pytest.raises(ValidationFailed):
        render_clip(spec.model_copy(update={"source_path": media_dir / "missing.mp4"}))


def test_rotated_source_is_upright(media_dir: Path) -> None:
    raw = make_test_video(media_dir / "landscape.mp4", duration=1.5, width=640, height=360, with_audio=False)
    rotated = media_dir / "rotated.mp4"
    proc = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-display_rotation", "90", "-i", str(raw), "-c", "copy",
                           str(rotated)], capture_output=True)
    media = probe_media(rotated) if proc.returncode == 0 else None
    if media is None or (media.width, media.height) != (360, 640):
        pytest.skip("FFmpeg build cannot tag display rotation")
    layout = CompiledLayout(background=Background(type="color", color="#000000"),
                            layers=[CompiledLayer(id="main", fit="contain")])
    spec = _spec(rotated, media_dir / "rot.mp4", layout, start=0.0, end=1.0)
    out = render_preview_frame(spec, 0.5, media_dir / "rot.jpg")
    img = cv2.imread(str(out)).astype(int)
    assert img.shape == (H, W, 3)
    # an upright portrait frame fills the canvas: no black letterbox bands at the top/bottom
    assert img[:150].mean() > 60 and img[-150:].mean() > 60


def test_render_speed_720p(src720: Path, media_dir: Path) -> None:
    layout = CompiledLayout(background=Background(type="blur"),
                            layers=[CompiledLayer(id="main", fit="contain", dest=Rect(x=0, y=0.3, w=1, h=0.4))])
    opts = RenderOptions(video_preset="veryfast", video_crf=20)
    spec = _spec(src720, media_dir / "speed.mp4", layout, start=0.0, end=6.0, options=opts, captions=_captions(),
                 style=_style())
    t0 = time.perf_counter()
    res = render_clip(spec)
    elapsed = time.perf_counter() - t0
    speed = res.duration / elapsed
    print(f"\nrender speed (720p -> 1080x1920, veryfast, blur bg + captions): {speed:.2f}x real time")
    assert speed > 0.5


def test_progress_reports_and_cancellation_cleans_up(src720: Path, media_dir: Path) -> None:
    calls: list[float] = []
    out = media_dir / "progress.mp4"
    render_clip(_spec(src720, out, _contain_layout(), start=0.0, end=1.5), progress=lambda f, _m: calls.append(f))
    assert calls and calls[-1] == 1.0 and calls == sorted(calls)

    class Cancelled(Exception):
        pass

    def cancel(fraction: float, _msg: str | None) -> None:
        raise Cancelled()

    target = media_dir / "cancelled.mp4"
    with pytest.raises(Cancelled):
        render_clip(_spec(src720, target, _contain_layout(), start=0.0, end=3.0), progress=cancel)
    assert not target.exists()
