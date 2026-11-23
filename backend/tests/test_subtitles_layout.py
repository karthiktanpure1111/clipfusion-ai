"""Caption layout (line breaking, shrink-to-fit, safe area, avoid rects) and the Pillow
caption rasteriser."""

from __future__ import annotations

import numpy as np
import pytest

from app.modules.subtitles.animation import caption_frame_state
from app.modules.subtitles.text_layout import layout_caption, load_font, text_width
from app.modules.subtitles.text_renderer import CaptionRenderer, render_caption_tile
from app.schemas.framing import Rect
from app.schemas.subtitles import (
    AnimationStyle,
    Caption,
    CaptionStyleOverride,
    CaptionWord,
    SafeArea,
    ShadowStyle,
    SubtitlePosition,
    SubtitleStyle,
)

W, H = 1080, 1920


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


def _caption(text: str, **kw) -> Caption:
    words = [CaptionWord(text=t, start=i * 0.3, end=i * 0.3 + 0.25) for i, t in enumerate(text.split())]
    return Caption(id=kw.pop("id", "c"), start=0.0, end=max(len(words) * 0.3, 0.3), words=words, **kw)


def _style(**kw) -> SubtitleStyle:
    base = {"font_family": "Inter", "font_weight": 700, "shadow": ShadowStyle(enabled=False)}
    base.update(kw)
    return SubtitleStyle(**base)


def test_single_line_centred_at_preset_position() -> None:
    lay = layout_caption(_caption("hello world"), _style(position=SubtitlePosition(preset="center")), W, H)
    assert len(lay.lines) == 1 and lay.font_size == pytest.approx(72)
    x, y, w, h = lay.block_px
    assert x + w / 2 == pytest.approx(W / 2, abs=0.5)
    assert y + h / 2 == pytest.approx(0.5 * H, abs=0.5)
    assert h == pytest.approx(72 * 1.15)
    assert lay.block.cy == pytest.approx(0.5, abs=1e-3)
    # words are laid out left to right on the baseline with a space between them
    a, b = lay.words
    assert b.x > a.x + a.w and a.baseline == b.baseline


def test_greedy_breaking_respects_max_width() -> None:
    style = _style(max_width=0.5, max_lines=4)
    lay = layout_caption(_caption("the quick brown fox jumps over the lazy dog"), style, W, H)
    assert len(lay.lines) > 1
    assert all(ln.w <= 0.5 * W + 1e-6 for ln in lay.lines)
    assert [i for ln in lay.lines for i in ln.words] == list(range(9))
    assert lay.shrink == 1.0


def test_overflow_shrinks_uniformly_down_to_sixty_percent() -> None:
    style = _style(max_width=0.6, max_lines=2, font_size=90)
    lay = layout_caption(_caption("this caption is a little too long for two lines"), style, W, H)
    assert len(lay.lines) == 2
    assert lay.shrink == pytest.approx(0.7)  # 5 % steps
    assert all(ln.w <= 0.6 * W + 1e-6 for ln in lay.lines)
    huge = "word " * 40
    lay2 = layout_caption(_caption(huge), style, W, H)
    assert len(lay2.lines) == 2  # never more than max_lines
    assert max(ln.w for ln in lay2.lines) <= W * (1 - 0.06 - 0.08) + 1e-6  # never clipped


def test_letter_spacing_and_uppercase_change_measurement() -> None:
    base = layout_caption(_caption("spacing"), _style(), W, H)
    spaced = layout_caption(_caption("spacing"), _style(letter_spacing=10), W, H)
    assert spaced.words[0].w == pytest.approx(base.words[0].w + 10 * len("spacing"))
    upper = layout_caption(_caption("spacing"), _style(uppercase=True), W, H)
    assert upper.words[0].text == "SPACING" and upper.words[0].w > base.words[0].w
    font = load_font(str(base.font_path), base.font_size)
    assert text_width(font, "ab", 2.0) == pytest.approx(font.getlength("ab") + 4.0)


def test_per_word_scale_and_canvas_scaling() -> None:
    c = _caption("big small")
    c.words[0].scale = 2.0
    lay = layout_caption(c, _style(), W, H)
    assert lay.words[0].font_size == pytest.approx(2 * lay.words[1].font_size)
    half = layout_caption(_caption("hello"), _style(), 540, 960)
    full = layout_caption(_caption("hello"), _style(), W, H)
    assert half.font_size == pytest.approx(full.font_size / 2)
    assert half.block.y == pytest.approx(full.block.y, abs=2e-3)


def test_block_is_clamped_inside_safe_area() -> None:
    style = _style(position=SubtitlePosition(preset="custom", x=0.0, y=1.0), safe_area=SafeArea(bottom=0.14, left=0.06))
    lay = layout_caption(_caption("bottom left corner"), style, W, H)
    x, y, w, h = lay.block_px
    assert x == pytest.approx(0.06 * W)
    assert y + h == pytest.approx(H - 0.14 * H)
    top = layout_caption(_caption("top"), _style(position=SubtitlePosition(preset="top")), W, H)
    assert top.block.cy == pytest.approx(0.12, abs=1e-3)


def test_caption_override_font_size_and_position() -> None:
    c = _caption("override", style_override=CaptionStyleOverride(font_size=40, position=SubtitlePosition(preset="top")))
    lay = layout_caption(c, _style(), W, H)
    assert lay.font_size == pytest.approx(40)
    assert lay.block.cy == pytest.approx(0.12, abs=1e-3)


def test_avoid_rects_moves_block_to_first_free_candidate() -> None:
    style = _style(position=SubtitlePosition(preset="bottom"))
    c = _caption("avoid my face")
    face_low = Rect(x=0.2, y=0.75, w=0.6, h=0.12)
    lay = layout_caption(c, style, W, H, avoid_rects=[face_low])
    assert lay.moved_for_faces and lay.block.cy == pytest.approx(0.70, abs=1e-3)
    both = [face_low, Rect(x=0.2, y=0.6, w=0.6, h=0.15)]
    lay = layout_caption(c, style, W, H, avoid_rects=both)
    assert lay.block.cy == pytest.approx(0.30, abs=1e-3)
    blocked = [Rect(x=0.0, y=0.0, w=1.0, h=1.0)]
    lay = layout_caption(c, style, W, H, avoid_rects=blocked)
    assert not lay.moved_for_faces and lay.block.cy == pytest.approx(0.82, abs=1e-3)
    off = layout_caption(c, _style(position=SubtitlePosition(preset="bottom"), avoid_faces=False), W, H,
                         avoid_rects=[face_low])
    assert not off.moved_for_faces and off.block.cy == pytest.approx(0.82, abs=1e-3)


def _tile_pixels(style: SubtitleStyle, t: float = 0.1, text: str = "Hello there") -> tuple:
    c = _caption(text)
    lay = layout_caption(c, style, W, H)
    tile = render_caption_tile(lay, caption_frame_state(c, style, t), style)
    assert tile is not None
    return tile, lay


def test_tile_contains_fill_outline_and_shadow_colours() -> None:
    style = _style(text_color="#FF0000", outline_color="#0000FF", outline_width=6,
                   shadow=ShadowStyle(enabled=True, color="#00FF00", opacity=1.0, blur=10, offset_y=20),
                   animation=AnimationStyle(type="static"))
    tile, lay = _tile_pixels(style)
    frame = np.zeros((H, W, 3), np.uint8)
    tile.composite_onto(frame)
    red = (frame[..., 2] > 200) & (frame[..., 1] < 60) & (frame[..., 0] < 60)
    blue = (frame[..., 0] > 200) & (frame[..., 2] < 60)
    green = (frame[..., 1] > 40) & (frame[..., 2] < 40) & (frame[..., 0] < 40)
    assert red.sum() > 500 and blue.sum() > 500 and green.sum() > 500
    ys, _ = np.nonzero(green)
    assert ys.max() > lay.block_px[1] + lay.block_px[3]  # shadow offset downwards


def test_box_background_and_highlight_box() -> None:
    style = _style(background_mode="box", background_color="#FFFFFF", background_opacity=1.0, outline_width=0,
                   text_color="#000000", animation=AnimationStyle(type="word_highlight", highlight_box=True),
                   highlight_color="#00FF00")
    tile, lay = _tile_pixels(style, t=0.1)
    frame = np.zeros((H, W, 3), np.uint8)
    tile.composite_onto(frame)
    x, y, w, h = (int(v) for v in lay.block_px)
    pad = int(style.background_padding)
    assert (frame[y - pad + 3, x + w // 2] > 240).all()  # inside the padded box, above the text
    wb = lay.words[0]
    gx = int(wb.x - 0.1 * wb.font_size)
    region = frame[int(wb.y):int(wb.y + wb.h), max(gx, 0):int(wb.x + wb.w)]
    assert ((region[..., 1] > 200) & (region[..., 2] < 60)).sum() > 200  # green highlight box


def test_caption_opacity_applies_at_composite_time_and_cache_hits() -> None:
    style = _style(animation=AnimationStyle(type="fade"))
    c = _caption("fade me")
    lay = layout_caption(c, style, W, H)
    r = CaptionRenderer()
    s1, s2 = caption_frame_state(c, style, 0.05), caption_frame_state(c, style, 0.2)
    t1, t2 = r.render(c, lay, s1, style), r.render(c, lay, s2, style)
    assert t1 is t2 and r.hits == 1 and r.misses == 1
    dim, full = np.zeros((H, W, 3), np.uint8), np.zeros((H, W, 3), np.uint8)
    t1.composite_onto(dim, s1["opacity"])
    t1.composite_onto(full, 1.0)
    assert 0 < dim.astype(int).sum() < full.astype(int).sum() * 0.5


def test_scaled_active_word_pushes_neighbours() -> None:
    style = _style(animation=AnimationStyle(type="word_highlight", highlight_scale=1.5), outline_width=0)
    c = _caption("aa bb cc")
    lay = layout_caption(c, style, W, H)
    from app.modules.subtitles.text_renderer import reflow_lines

    state = caption_frame_state(c, style, 0.35)  # "bb" active
    lefts = reflow_lines(lay, state, "center")
    gap_before = lay.words[1].x - (lay.words[0].x + lay.words[0].w)
    assert lefts[1] - (lefts[0] + lay.words[0].w) == pytest.approx(gap_before)
    assert lefts[2] - (lefts[1] + 1.5 * lay.words[1].w) == pytest.approx(gap_before)
    assert lefts[0] < lay.words[0].x  # line re-centred


def test_pop_scale_resamples_cached_tile_around_block_centre() -> None:
    style = _style(animation=AnimationStyle(type="pop"))
    c = _caption("pop goes the caption")
    lay = layout_caption(c, style, W, H)
    r = CaptionRenderer()
    small = r.render(c, lay, caption_frame_state(c, style, 0.0), style)  # scale 0.6
    full = r.render(c, lay, caption_frame_state(c, style, 0.2), style)   # scale 1.0 (cached base)
    assert r.misses == 2 and r.hits == 1
    assert small.alpha.shape[1] == pytest.approx(full.alpha.shape[1] * 0.6, abs=1)
    bx, by, bw, bh = lay.block_px
    centre_small = small.x + small.alpha.shape[1] / 2
    assert centre_small == pytest.approx(full.x + full.alpha.shape[1] / 2, abs=3)
    assert abs(centre_small - (bx + bw / 2)) < 0.1 * bw
