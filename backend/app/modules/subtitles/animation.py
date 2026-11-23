"""Caption animation states (pure + deterministic; mirrored by the frontend preview).

:func:`caption_frame_state` implements the normative semantics documented in
``app/schemas/subtitles.py``. Output (all floats rounded to 6 decimals)::

    {"visible": bool,           # start <= t < end
     "opacity": float,          # caption-level opacity (fade)
     "scale": float,            # caption-level scale around the block centre (pop)
     "words": [{"visible": bool, "color": "#RRGGBB", "scale": float, "opacity": float,
                "box": "#RRGGBB" | None,   # highlight box colour behind the word
                "font_scale": float}]}     # per-word ``scale`` override (already applied by the layout)

Decisions where the contract leaves room (fixtures in ``shared/fixtures/caption_states.json``
pin them down for the TypeScript port):

* The *active* word is the word with ``start <= t < end`` (no word is active in a gap).
* ``pop`` highlights the active word by colour only (no word scale).
* ``dynamic_emphasis``: an active word uses ``highlight_color`` even when it has ``emphasis``.
* ``highlight_box`` applies to the animations that have an active word: ``word_highlight``,
  ``karaoke``, ``pop`` and ``dynamic_emphasis``.
* Colour precedence: highlight box -> ``word.color`` or ``text_color``; otherwise
  ``word.color`` > ``word.highlight`` (forces ``highlight_color``) > animation colour.
* Invisible captions return ``opacity 0`` and every word invisible in ``text_color``.
* Caption ``style_override`` colours/size/position replace the style values (``effective_style``).
"""

from __future__ import annotations

from app.schemas.subtitles import Caption, CaptionWord, SubtitleStyle

EASE_BACK_C1 = 1.70158
EASE_BACK_C3 = EASE_BACK_C1 + 1.0
POP_DURATION = 0.15
POP_FROM = 0.6
FADE_IN = 0.25
FADE_OUT = 0.15
TYPEWRITER_FADE = 0.08
DYNAMIC_POP = 0.1
BOX_ANIMATIONS = frozenset({"word_highlight", "karaoke", "pop", "dynamic_emphasis"})
_ROUND = 6


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def ease_out_back(x: float) -> float:
    """Standard easeOutBack on ``[0, 1]`` (overshoots slightly before settling at 1)."""
    x = _clamp01(x)
    return 1.0 + EASE_BACK_C3 * (x - 1.0) ** 3 + EASE_BACK_C1 * (x - 1.0) ** 2


def effective_style(style: SubtitleStyle, caption: Caption) -> SubtitleStyle:
    """``style`` with the caption's ``style_override`` applied (colours, font size, position)."""
    o = caption.style_override
    if o is None:
        return style
    update: dict = {}
    if o.text_color:
        update["text_color"] = o.text_color
    if o.highlight_color:
        update["highlight_color"] = o.highlight_color
    if o.font_size:
        update["font_size"] = o.font_size
    if o.position is not None:
        update["position"] = o.position
    return style.model_copy(update=update) if update else style


def _r(x: float) -> float:
    return round(float(x), _ROUND)


def _word_state(w: CaptionWord, st: SubtitleStyle, t: float) -> dict:
    anim = st.animation
    kind, speed = anim.type, anim.speed
    hl, emph = st.highlight_color, st.emphasis_color
    active = w.start <= t < w.end
    color, scale, opacity, visible = st.text_color, 1.0, 1.0, True
    if kind == "word_highlight":
        if active:
            color, scale = hl, anim.highlight_scale
    elif kind == "karaoke":
        if w.start <= t:
            color = hl
    elif kind == "pop":
        if active:
            color = hl
    elif kind == "typewriter":
        visible = w.start <= t
        opacity = _clamp01((t - w.start) / (TYPEWRITER_FADE / speed)) if visible else 0.0
    elif kind == "important_words":
        if w.emphasis:
            color = emph
    elif kind == "dynamic_emphasis":
        if active:
            color = hl
            scale = 1.0 + (anim.highlight_scale - 1.0) * _clamp01((t - w.start) / (DYNAMIC_POP / speed))
        elif w.emphasis:
            color = emph
    box = hl if (anim.highlight_box and active and kind in BOX_ANIMATIONS) else None
    if box is not None:
        color = w.color or st.text_color
    else:
        if w.highlight:
            color = hl
        if w.color:
            color = w.color
    return {"visible": visible, "color": color.upper(), "scale": _r(scale), "opacity": _r(opacity),
            "box": box.upper() if box else None, "font_scale": _r(w.scale or 1.0)}


def _caption_level(st: SubtitleStyle, caption: Caption, t: float) -> tuple[float, float]:
    anim = st.animation
    if anim.type == "fade":
        fade_in = (t - caption.start) / (FADE_IN / anim.speed)
        fade_out = (caption.end - t) / (FADE_OUT / anim.speed)
        return _clamp01(min(fade_in, fade_out)), 1.0
    if anim.type == "pop":
        u = (t - caption.start) / (POP_DURATION / anim.speed)
        return 1.0, POP_FROM + (1.0 - POP_FROM) * ease_out_back(u)
    return 1.0, 1.0


def caption_frame_state(caption: Caption, style: SubtitleStyle, t: float) -> dict:
    """Render state of ``caption`` at clip time ``t`` (see module docstring for the shape)."""
    st = effective_style(style, caption)
    if not caption.start <= t < caption.end:
        return {"visible": False, "opacity": 0.0, "scale": 1.0,
                "words": [{"visible": False, "color": st.text_color.upper(), "scale": 1.0, "opacity": 0.0,
                           "box": None, "font_scale": _r(w.scale or 1.0)} for w in caption.words]}
    opacity, scale = _caption_level(st, caption, t)
    return {"visible": True, "opacity": _r(opacity), "scale": _r(scale),
            "words": [_word_state(w, st, t) for w in caption.words]}


def state_key(state: dict) -> tuple:
    """Hashable key of the parts of a state that change the rendered pixels (caption-level
    opacity is applied at composite time and therefore excluded)."""
    return (round(state["scale"], 3),) + tuple(
        (w["visible"], w["color"], round(w["scale"], 3), round(w["opacity"], 2), w["box"]) for w in state["words"]
    )
