"""Subtitle contract — shared by caption building, the Pillow renderer, the API and the
frontend Subtitle Studio (``frontend/src/lib/subtitles/*`` mirrors these semantics).

Units: sizes are output pixels for a 1080 px wide canvas (scale ``* canvas_width / 1080``
for other sizes). Positions are normalised canvas coordinates. Caption/word times are
clip-relative seconds.

Layout (normative):
1. Caption text = words joined by single spaces (``uppercase`` applied at draw time).
2. Greedy line breaking: add words to the current line while the measured line width
   (including ``letter_spacing`` per character) <= ``max_width * canvas_width``; never
   exceed ``max_lines`` (overflowing words shrink the font uniformly until they fit, down to
   60% of ``font_size``).
3. Line height = ``font_size * line_spacing``. Block height = lines * line height.
4. The block is centred at ``(position.x * W, position.y * H)`` then clamped inside the
   ``safe_area`` margins. Preset y values: top .12, upper_middle .30, center .50,
   lower_middle .70, bottom .82.
5. If ``avoid_faces`` and the block intersects a visible face (canvas space) at the
   caption's start, middle or end time, try y = .82, .70, .30, .15 in order and use the
   first non-overlapping candidate; otherwise keep the requested position.

Animation semantics at clip time ``t`` for a caption ``[start, end)`` (``speed`` divides
all durations below):
* static           — all words ``text_color``.
* word_highlight   — all words shown; the active word (start <= t < end) uses
                     ``highlight_color`` and is scaled by ``highlight_scale``.
* karaoke          — words with ``start <= t`` use ``highlight_color``, later words ``text_color``.
* pop              — caption scale eases out from 0.6 to 1.0 over 0.15 s from caption start
                     (easeOutBack), active word highlighted.
* fade             — opacity 0->1 over 0.25 s after start and 1->0 over the last 0.15 s.
* typewriter       — only words with ``start <= t`` are drawn (layout is computed for the
                     full caption so lines don't jump); each word fades in over 0.08 s.
* important_words  — static, but words with ``emphasis`` use ``emphasis_color``.
* dynamic_emphasis — active word pops (1.0 -> highlight_scale over 0.1 s) in
                     ``highlight_color``; ``emphasis`` words use ``emphasis_color``.
Per-word overrides always win: ``color`` replaces the base colour, ``highlight`` forces
``highlight_color``, ``scale`` multiplies the font size for that word.
Optional ``highlight_box`` draws a rounded rectangle in ``highlight_color`` behind the
active word and renders that word in ``text_color``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX = r"^#[0-9A-Fa-f]{6}$"
PositionPreset = Literal["top", "upper_middle", "center", "lower_middle", "bottom", "custom"]
AnimationType = Literal["static", "word_highlight", "karaoke", "pop", "fade", "typewriter",
                        "important_words", "dynamic_emphasis"]

POSITION_PRESET_Y: dict[str, float] = {
    "top": 0.12, "upper_middle": 0.30, "center": 0.50, "lower_middle": 0.70, "bottom": 0.82,
}
FONT_SIZE_PRESETS: dict[str, int] = {"small": 48, "medium": 64, "large": 80, "xl": 100}


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ShadowStyle(_Model):
    enabled: bool = True
    color: str = Field(default="#000000", pattern=HEX)
    opacity: float = Field(default=0.6, ge=0, le=1)
    blur: float = Field(default=8.0, ge=0, le=60)
    offset_x: float = Field(default=0.0, ge=-40, le=40)
    offset_y: float = Field(default=4.0, ge=-40, le=40)


class SubtitlePosition(_Model):
    preset: PositionPreset = "lower_middle"
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.70, ge=0, le=1)

    def resolved_y(self) -> float:
        return self.y if self.preset == "custom" else POSITION_PRESET_Y[self.preset]

    def resolved_x(self) -> float:
        return self.x if self.preset == "custom" else 0.5


class AnimationStyle(_Model):
    type: AnimationType = "word_highlight"
    speed: float = Field(default=1.0, ge=0.25, le=3.0)
    highlight_scale: float = Field(default=1.12, ge=1.0, le=1.6)
    highlight_box: bool = False


class SafeArea(_Model):
    top: float = Field(default=0.08, ge=0, le=0.4)
    bottom: float = Field(default=0.14, ge=0, le=0.4)  # Shorts/Reels UI covers the bottom
    left: float = Field(default=0.06, ge=0, le=0.3)
    right: float = Field(default=0.08, ge=0, le=0.3)


class SubtitleStyle(_Model):
    version: int = 1
    font_family: str = "Montserrat"
    font_weight: int = Field(default=800, ge=100, le=900)  # "Bold" toggle = >= 700
    italic: bool = False
    font_size: float = Field(default=72, ge=16, le=220)
    text_color: str = Field(default="#FFFFFF", pattern=HEX)
    highlight_color: str = Field(default="#FFD400", pattern=HEX)
    emphasis_color: str = Field(default="#FF0033", pattern=HEX)
    outline_color: str = Field(default="#000000", pattern=HEX)
    outline_width: float = Field(default=6.0, ge=0, le=24)
    background_mode: Literal["none", "box", "line"] = "none"  # box = one box, line = box per line
    background_color: str = Field(default="#000000", pattern=HEX)
    background_opacity: float = Field(default=0.0, ge=0, le=1)
    background_padding: float = Field(default=18.0, ge=0, le=80)
    background_radius: float = Field(default=14.0, ge=0, le=80)
    shadow: ShadowStyle = Field(default_factory=ShadowStyle)
    letter_spacing: float = Field(default=0.0, ge=-5, le=30)
    line_spacing: float = Field(default=1.15, ge=0.8, le=2.5)
    align: Literal["left", "center", "right"] = "center"
    uppercase: bool = False
    position: SubtitlePosition = Field(default_factory=SubtitlePosition)
    max_width: float = Field(default=0.84, ge=0.3, le=1.0)
    max_lines: int = Field(default=2, ge=1, le=4)
    words_per_caption: int = Field(default=4, ge=1, le=12)
    max_caption_duration: float = Field(default=3.0, ge=0.5, le=10)
    animation: AnimationStyle = Field(default_factory=AnimationStyle)
    safe_area: SafeArea = Field(default_factory=SafeArea)
    avoid_faces: bool = True


class CaptionStyleOverride(_Model):
    text_color: str | None = Field(default=None, pattern=HEX)
    highlight_color: str | None = Field(default=None, pattern=HEX)
    font_size: float | None = Field(default=None, ge=16, le=220)
    position: SubtitlePosition | None = None


class CaptionWord(_Model):
    text: str = Field(min_length=1, max_length=80)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    color: str | None = Field(default=None, pattern=HEX)
    scale: float | None = Field(default=None, ge=0.5, le=2.5)
    highlight: bool = False
    emphasis: bool = False


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


class Caption(_Model):
    id: str = Field(default_factory=_short_id)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    words: list[CaptionWord] = Field(default_factory=list)
    style_override: CaptionStyleOverride | None = None

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:  # noqa: ANN001
        start = info.data.get("start", 0.0)
        return max(v, start + 0.05)
