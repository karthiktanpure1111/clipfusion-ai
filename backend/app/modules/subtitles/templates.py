"""Built-in subtitle templates (Subtitle Studio presets).

``TEMPLATES`` maps a stable key to ``(display name, SubtitleStyle)``. The UI additionally
offers "custom" (the user's own style). Use :func:`get_template` to obtain an independent
copy that can be modified safely.
"""

from __future__ import annotations

from app.core.errors import NotFoundError
from app.schemas.subtitles import (
    AnimationStyle,
    SafeArea,
    ShadowStyle,
    SubtitlePosition,
    SubtitleStyle,
)

_GAMING = SubtitleStyle(
    font_family="Bebas Neue", font_weight=400, font_size=104, uppercase=True, letter_spacing=1.5,
    text_color="#FFFFFF", highlight_color="#FFE600", emphasis_color="#00E5FF",
    outline_color="#000000", outline_width=7,
    shadow=ShadowStyle(enabled=True, color="#000000", opacity=0.75, blur=6, offset_x=0, offset_y=6),
    line_spacing=1.0, position=SubtitlePosition(preset="lower_middle"), max_width=0.86, max_lines=2,
    words_per_caption=3, max_caption_duration=2.0,
    animation=AnimationStyle(type="dynamic_emphasis", speed=1.0, highlight_scale=1.22),
)

_PODCAST = SubtitleStyle(
    font_family="Montserrat", font_weight=700, font_size=66, text_color="#FFFFFF",
    highlight_color="#7CFFB2", emphasis_color="#FFD166", outline_color="#000000", outline_width=0,
    background_mode="line", background_color="#000000", background_opacity=0.38,
    background_padding=16, background_radius=12,
    shadow=ShadowStyle(enabled=True, color="#000000", opacity=0.35, blur=10, offset_x=0, offset_y=3),
    line_spacing=1.2, position=SubtitlePosition(preset="lower_middle"), max_width=0.84, max_lines=2,
    words_per_caption=5, max_caption_duration=3.0,
    animation=AnimationStyle(type="word_highlight", speed=1.0, highlight_scale=1.04),
)

_MINIMAL = SubtitleStyle(
    font_family="Inter", font_weight=600, font_size=52, text_color="#FFFFFF",
    highlight_color="#FFFFFF", emphasis_color="#FFFFFF", outline_color="#000000", outline_width=2,
    shadow=ShadowStyle(enabled=True, color="#000000", opacity=0.55, blur=8, offset_x=0, offset_y=2),
    line_spacing=1.2, position=SubtitlePosition(preset="bottom"), max_width=0.8, max_lines=2,
    words_per_caption=6, max_caption_duration=3.5,
    animation=AnimationStyle(type="static", speed=1.0, highlight_scale=1.0),
)

_BOLD_VIRAL = SubtitleStyle(
    font_family="Montserrat", font_weight=900, font_size=86, uppercase=True, text_color="#FFFFFF",
    highlight_color="#FFD400", emphasis_color="#FF2D55", outline_color="#000000", outline_width=10,
    shadow=ShadowStyle(enabled=True, color="#000000", opacity=0.6, blur=4, offset_x=0, offset_y=5),
    line_spacing=1.05, position=SubtitlePosition(preset="center"), max_width=0.86, max_lines=2,
    words_per_caption=3, max_caption_duration=2.2,
    animation=AnimationStyle(type="pop", speed=1.0, highlight_scale=1.15),
)

_PROFESSIONAL = SubtitleStyle(
    font_family="Roboto", font_weight=700, font_size=56, text_color="#FFFFFF",
    highlight_color="#4FC3F7", emphasis_color="#4FC3F7", outline_color="#000000", outline_width=0,
    background_mode="box", background_color="#111317", background_opacity=0.72,
    background_padding=22, background_radius=18,
    shadow=ShadowStyle(enabled=False),
    line_spacing=1.25, position=SubtitlePosition(preset="bottom"), max_width=0.82, max_lines=2,
    words_per_caption=7, max_caption_duration=4.0,
    animation=AnimationStyle(type="fade", speed=1.0, highlight_scale=1.0),
    safe_area=SafeArea(bottom=0.16),
)

_REACTION = SubtitleStyle(
    font_family="Bangers", font_weight=400, font_size=96, letter_spacing=2.0, uppercase=True,
    text_color="#FFFFFF", highlight_color="#FF3CAC", emphasis_color="#FFE600",
    outline_color="#1A0033", outline_width=8,
    shadow=ShadowStyle(enabled=True, color="#1A0033", opacity=0.7, blur=6, offset_x=3, offset_y=6),
    line_spacing=1.05, position=SubtitlePosition(preset="lower_middle"), max_width=0.86, max_lines=2,
    words_per_caption=3, max_caption_duration=2.0,
    animation=AnimationStyle(type="karaoke", speed=1.0, highlight_scale=1.1),
)

TEMPLATES: dict[str, tuple[str, SubtitleStyle]] = {
    "gaming": ("Gaming", _GAMING),
    "podcast": ("Podcast", _PODCAST),
    "minimal": ("Minimal", _MINIMAL),
    "bold_viral": ("Bold Viral", _BOLD_VIRAL),
    "professional": ("Professional", _PROFESSIONAL),
    "reaction": ("Reaction", _REACTION),
}

DEFAULT_TEMPLATE = "bold_viral"


def get_template(key: str) -> SubtitleStyle:
    """Independent (deep) copy of a template's style; raises NotFoundError for unknown keys."""
    try:
        return TEMPLATES[key][1].model_copy(deep=True)
    except KeyError:
        raise NotFoundError(f"Unknown subtitle template '{key}'", code="template_not_found") from None


def list_templates() -> list[dict]:
    """``[{"key", "name", "style"}]`` for the API (style as JSON-compatible dict)."""
    return [{"key": k, "name": name, "style": style.model_dump(mode="json")} for k, (name, style) in TEMPLATES.items()]
