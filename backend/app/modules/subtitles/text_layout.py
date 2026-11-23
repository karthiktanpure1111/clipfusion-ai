"""Caption layout (normative steps in ``app/schemas/subtitles.py``), measured with Pillow.

Measurement rules shared with the frontend preview:

* Sizes are defined for a 1080 px wide canvas and multiplied by ``k = canvas_w / 1080``.
* Display text = word text (upper-cased when ``style.uppercase``). Width of a text run =
  font advance (with kerning) + ``letter_spacing * k`` per character (CSS semantics: the
  spacing follows every character, including the last). A space is one character.
* A word with a per-word ``scale`` is measured at ``font_size * scale``.
* Greedy line breaking within ``max_width * W``. If the caption needs more than
  ``max_lines`` lines (or a single word is wider than the line), the font shrinks uniformly
  in 5 % steps down to 60 %. If it still does not fit, overflowing words are appended to
  the last line and the font shrinks further just enough for the widest line to fit inside
  the safe area, so text is never clipped.
* Line height = ``font_size * line_spacing``; baseline = line top
  + ``(line_height - (ascent + descent)) / 2 + ascent`` (font hhea metrics).
* Lines are aligned left/centre/right inside the block (block width = widest line).
* The block is centred on the position and clamped inside the safe area; ``avoid_rects``
  (visible faces, canvas-normalised) move it to the first free y of ``.82, .70, .30, .15``.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from app.modules.subtitles.animation import effective_style
from app.modules.subtitles.fonts import resolve_font
from app.schemas.framing import Rect
from app.schemas.render import FontFile
from app.schemas.subtitles import Caption, SubtitleStyle

REFERENCE_WIDTH = 1080.0
SHRINK_STEP = 0.05
MIN_SHRINK = 0.60
AVOID_CANDIDATES_Y = (0.82, 0.70, 0.30, 0.15)
_EPS = 1e-6


@dataclass
class WordBox:
    """Geometry of one word in canvas pixels (``h`` = line height)."""

    index: int
    text: str
    line: int
    x: float
    y: float
    w: float
    h: float
    font_size: float
    baseline: float


@dataclass
class LayoutLine:
    index: int
    x: float
    y: float
    w: float
    h: float
    baseline: float
    words: list[int] = field(default_factory=list)


@dataclass
class CaptionLayout:
    """Result of :func:`layout_caption`. ``block`` is the normalised canvas rect of the text
    block (without background padding); pixel geometry is in ``block_px``/``lines``/``words``."""

    block: Rect
    font_size: float
    lines: list[LayoutLine]
    words: list[WordBox] = field(default_factory=list)
    block_px: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    line_height: float = 0.0
    letter_spacing: float = 0.0
    font_path: Path | None = None
    canvas_w: int = 1080
    canvas_h: int = 1920
    scale_k: float = 1.0
    shrink: float = 1.0
    position_y: float = 0.5
    moved_for_faces: bool = False


@lru_cache(maxsize=512)
def _font(path: str, size: float, thread_id: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


def load_font(path: str, size: float) -> ImageFont.FreeTypeFont:
    """Cached FreeType font (sizes quantised to 1/4 px). FreeType faces are not thread-safe,
    so each thread (concurrent render jobs) gets its own instances."""
    return _font(path, max(1.0, round(size * 4) / 4), threading.get_ident())


def text_width(font: ImageFont.FreeTypeFont, text: str, letter_spacing: float) -> float:
    """Advance width of ``text`` including ``letter_spacing`` after every character."""
    return font.getlength(text) + letter_spacing * len(text) if text else 0.0


def char_offsets(font: ImageFont.FreeTypeFont, text: str, letter_spacing: float) -> list[float]:
    """x offset of every character (kerning-preserving) for letter-spaced drawing."""
    return [font.getlength(text[:i]) + letter_spacing * i for i in range(len(text))]


def display_words(caption: Caption, style: SubtitleStyle) -> list[str]:
    return [w.text.upper() if style.uppercase else w.text for w in caption.words]


@dataclass
class _Measure:
    font_size: float
    widths: list[float]
    space: float
    lines: list[list[int]]
    line_widths: list[float]


def _break_lines(widths: list[float], space: float, max_w: float) -> list[list[int]]:
    lines: list[list[int]] = []
    cur: list[int] = []
    cur_w = 0.0
    for i, w in enumerate(widths):
        if cur and cur_w + space + w > max_w + _EPS:
            lines.append(cur)
            cur, cur_w = [i], w
        else:
            cur_w = w if not cur else cur_w + space + w
            cur.append(i)
    if cur:
        lines.append(cur)
    return lines


def _line_widths(lines: list[list[int]], widths: list[float], space: float) -> list[float]:
    return [sum(widths[i] for i in ln) + space * (len(ln) - 1) for ln in lines]


def _measure(font_path: str, size: float, texts: list[str], scales: list[float], ls: float,
             max_w: float, max_lines: int, force: bool = False) -> _Measure:
    base = load_font(font_path, size)
    widths = [text_width(load_font(font_path, size * s) if s != 1.0 else base, t, ls) for t, s in zip(texts, scales,
                                                                                                    strict=True)]
    space = text_width(base, " ", ls)
    lines = _break_lines(widths, space, max_w)
    if force and len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [[i for ln in lines[max_lines - 1:] for i in ln]]
    return _Measure(size, widths, space, lines, _line_widths(lines, widths, space))


def _fits(m: _Measure, max_w: float, max_lines: int) -> bool:
    return len(m.lines) <= max_lines and all(w <= max_w + _EPS for w in m.line_widths)


def _fit_text(font_path: str, size: float, texts: list[str], scales: list[float], ls: float, max_w: float,
              max_lines: int, avail_w: float) -> _Measure:
    steps = round((1.0 - MIN_SHRINK) / SHRINK_STEP)
    for i in range(steps + 1):
        m = _measure(font_path, size * (1.0 - SHRINK_STEP * i), texts, scales, ls, max_w, max_lines)
        if _fits(m, max_w, max_lines):
            return m
    m = _measure(font_path, size * MIN_SHRINK, texts, scales, ls, max_w, max_lines, force=True)
    widest = max(m.line_widths, default=0.0)
    if widest > avail_w > 0:
        f = m.font_size * avail_w / widest
        for _ in range(4):  # letter spacing does not scale; converge in a few steps
            trial = _measure(font_path, f, texts, scales, ls, float("inf"), max_lines)
            trial.lines, trial.line_widths = m.lines, _line_widths(m.lines, trial.widths, trial.space)
            widest = max(trial.line_widths, default=0.0)
            if widest <= avail_w + _EPS:
                return trial
            f *= avail_w / widest
        return trial
    return m


def _clamp_axis(start: float, size: float, lo: float, hi: float) -> float:
    if hi - lo < size:
        return (lo + hi) / 2 - size / 2
    return min(max(start, lo), hi - size)


def _place(style: SubtitleStyle, cx: float, cy: float, bw: float, bh: float, W: int, H: int) -> tuple[float, float]:
    sa = style.safe_area
    x0 = _clamp_axis(cx - bw / 2, bw, sa.left * W, W - sa.right * W)
    y0 = _clamp_axis(cy - bh / 2, bh, sa.top * H, H - sa.bottom * H)
    return x0, y0


def _norm_rect(x: float, y: float, w: float, h: float, W: int, H: int) -> Rect:
    nw = min(max(w / W, 1e-4), 1.0)
    nh = min(max(h / H, 1e-4), 1.0)
    return Rect(x=min(max(x / W, 0.0), 1.0 - nw), y=min(max(y / H, 0.0), 1.0 - nh), w=nw, h=nh)


def _overlaps(block: Rect, rects: Sequence[Rect]) -> bool:
    return any(block.intersection_area(r) > 0 for r in rects)


def _avoid(style: SubtitleStyle, avoid_rects: Sequence[Rect] | None, cx: float, cy: float, bw: float, bh: float,
           W: int, H: int) -> tuple[float, float, bool]:
    x0, y0 = _place(style, cx, cy, bw, bh, W, H)
    if not (style.avoid_faces and avoid_rects) or not _overlaps(_norm_rect(x0, y0, bw, bh, W, H), avoid_rects):
        return x0, y0, False
    for cand in AVOID_CANDIDATES_Y:
        cx0, cy0 = _place(style, cx, cand * H, bw, bh, W, H)
        if not _overlaps(_norm_rect(cx0, cy0, bw, bh, W, H), avoid_rects):
            return cx0, cy0, True
    return x0, y0, False


def layout_caption(caption: Caption, style: SubtitleStyle, canvas_w: int, canvas_h: int, *,
                   avoid_rects: list[Rect] | None = None, custom_fonts: Sequence[FontFile] = ()) -> CaptionLayout:
    """Lay out ``caption`` on a ``canvas_w`` x ``canvas_h`` canvas (see module docstring).

    ``avoid_rects`` (normalised canvas rects, e.g. visible faces) are honoured only when
    ``style.avoid_faces`` is true. The caption's ``style_override`` is applied."""
    st = effective_style(style, caption)
    W, H = int(canvas_w), int(canvas_h)
    k = W / REFERENCE_WIDTH
    font_path = str(resolve_font(st.font_family, st.font_weight, st.italic, custom_fonts))
    texts = display_words(caption, st)
    scales = [w.scale or 1.0 for w in caption.words]
    ls = st.letter_spacing * k
    max_w = st.max_width * W
    avail_w = W * (1.0 - st.safe_area.left - st.safe_area.right)
    m = _fit_text(font_path, st.font_size * k, texts, scales, ls, max_w, st.max_lines, avail_w)
    fs = m.font_size
    lh = fs * st.line_spacing
    bw = max(m.line_widths, default=0.0)
    bh = lh * len(m.lines)
    cx, cy = st.position.resolved_x() * W, st.position.resolved_y() * H
    x0, y0, moved = _avoid(st, avoid_rects, cx, cy, max(bw, 1.0), max(bh, 1.0), W, H)
    ascent, descent = load_font(font_path, fs).getmetrics()
    lines: list[LayoutLine] = []
    words: list[WordBox] = []
    for li, (idx, lw) in enumerate(zip(m.lines, m.line_widths, strict=True)):
        lx = x0 + {"left": 0.0, "center": (bw - lw) / 2, "right": bw - lw}[st.align]
        ly = y0 + li * lh
        baseline = ly + (lh - (ascent + descent)) / 2 + ascent
        lines.append(LayoutLine(index=li, x=lx, y=ly, w=lw, h=lh, baseline=baseline, words=list(idx)))
        wx = lx
        for i in idx:
            words.append(WordBox(index=i, text=texts[i], line=li, x=wx, y=ly, w=m.widths[i], h=lh,
                                 font_size=fs * scales[i], baseline=baseline))
            wx += m.widths[i] + m.space
    words.sort(key=lambda w: w.index)
    block = _norm_rect(x0, y0, max(bw, 1.0), max(bh, 1.0), W, H)
    return CaptionLayout(block=block, font_size=fs, lines=lines, words=words, block_px=(x0, y0, bw, bh),
                         line_height=lh, letter_spacing=ls, font_path=Path(font_path), canvas_w=W, canvas_h=H,
                         scale_k=k, shrink=fs / (st.font_size * k), position_y=(y0 + bh / 2) / H,
                         moved_for_faces=moved)
