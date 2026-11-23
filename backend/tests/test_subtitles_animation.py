"""caption_frame_state: the 8 animation types, overrides and the shared TS fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.modules.subtitles.animation import caption_frame_state, ease_out_back
from app.schemas.subtitles import AnimationStyle, Caption, CaptionStyleOverride, CaptionWord, SubtitleStyle

WHITE, HL, EMPH = "#FFFFFF", "#FFD400", "#FF0033"
FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "caption_states.json"


def _caption(**kw) -> Caption:
    words = [CaptionWord(text="one", start=0.0, end=0.4), CaptionWord(text="two", start=0.5, end=0.9, emphasis=True),
             CaptionWord(text="three", start=1.0, end=1.5)]
    return Caption(id="c1", start=0.0, end=2.0, words=words, **kw)


def _style(kind: str, **anim) -> SubtitleStyle:
    return SubtitleStyle(text_color=WHITE, highlight_color=HL, emphasis_color=EMPH,
                         animation=AnimationStyle(type=kind, **anim))


def _colors(state: dict) -> list[str]:
    return [w["color"] for w in state["words"]]


def test_visibility_window_is_half_open() -> None:
    c, st = _caption(), _style("static")
    assert not caption_frame_state(c, st, -0.001)["visible"]
    assert caption_frame_state(c, st, 0.0)["visible"]
    hidden = caption_frame_state(c, st, 2.0)
    assert not hidden["visible"] and hidden["opacity"] == 0 and not any(w["visible"] for w in hidden["words"])


def test_static_and_important_words() -> None:
    c = _caption()
    assert _colors(caption_frame_state(c, _style("static"), 0.6)) == [WHITE] * 3
    assert _colors(caption_frame_state(c, _style("important_words"), 0.1)) == [WHITE, EMPH, WHITE]


def test_word_highlight_uses_active_word_only() -> None:
    c, st = _caption(), _style("word_highlight", highlight_scale=1.2)
    s = caption_frame_state(c, st, 0.6)
    assert _colors(s) == [WHITE, HL, WHITE]
    assert [w["scale"] for w in s["words"]] == [1.0, 1.2, 1.0]
    gap = caption_frame_state(c, st, 0.95)  # between words: no active word
    assert _colors(gap) == [WHITE] * 3


def test_karaoke_colours_spoken_words() -> None:
    c, st = _caption(), _style("karaoke")
    assert _colors(caption_frame_state(c, st, 0.2)) == [HL, WHITE, WHITE]
    assert _colors(caption_frame_state(c, st, 0.95)) == [HL, HL, WHITE]
    assert _colors(caption_frame_state(c, st, 1.9)) == [HL, HL, HL]


def test_pop_scale_eases_out_back_and_respects_speed() -> None:
    c = _caption()
    st = _style("pop")
    assert caption_frame_state(c, st, 0.0)["scale"] == pytest.approx(0.6)
    mid = caption_frame_state(c, st, 0.075)["scale"]
    assert mid == pytest.approx(0.6 + 0.4 * ease_out_back(0.5), abs=1e-6) and mid > 0.9
    assert caption_frame_state(c, st, 0.15)["scale"] == pytest.approx(1.0)
    assert _colors(caption_frame_state(c, st, 0.2)) == [HL, WHITE, WHITE]
    fast = _style("pop", speed=2.0)
    assert caption_frame_state(c, fast, 0.075)["scale"] == pytest.approx(1.0)
    assert max(caption_frame_state(c, st, t / 100)["scale"] for t in range(16)) > 1.0  # overshoot


def test_fade_in_and_out() -> None:
    c, st = _caption(), _style("fade")
    assert caption_frame_state(c, st, 0.0)["opacity"] == 0.0
    assert caption_frame_state(c, st, 0.125)["opacity"] == pytest.approx(0.5)
    assert caption_frame_state(c, st, 1.0)["opacity"] == 1.0
    assert caption_frame_state(c, st, 2.0 - 0.075)["opacity"] == pytest.approx(0.5)
    assert caption_frame_state(c, _style("fade", speed=0.5), 0.25)["opacity"] == pytest.approx(0.5)


def test_typewriter_reveals_words_with_short_fade() -> None:
    c, st = _caption(), _style("typewriter")
    s = caption_frame_state(c, st, 0.54)
    assert [w["visible"] for w in s["words"]] == [True, True, False]
    assert s["words"][1]["opacity"] == pytest.approx(0.5)
    assert s["words"][0]["opacity"] == 1.0 and s["words"][2]["opacity"] == 0.0


def test_dynamic_emphasis_pops_active_word_and_colours_emphasis() -> None:
    c, st = _caption(), _style("dynamic_emphasis", highlight_scale=1.3)
    s = caption_frame_state(c, st, 0.05)
    assert _colors(s) == [HL, EMPH, WHITE]
    assert s["words"][0]["scale"] == pytest.approx(1.15)
    assert caption_frame_state(c, st, 0.2)["words"][0]["scale"] == pytest.approx(1.3)
    assert _colors(caption_frame_state(c, st, 0.6))[1] == HL  # active wins over emphasis


def test_per_word_overrides_and_caption_override() -> None:
    c = _caption()
    c.words[0].color = "#00ff00"
    c.words[2].highlight = True
    c.words[2].scale = 1.5
    s = caption_frame_state(c, _style("karaoke"), 0.2)
    assert _colors(s) == ["#00FF00", WHITE, HL]
    assert s["words"][2]["font_scale"] == 1.5
    styled = _caption(style_override=CaptionStyleOverride(text_color="#112233", highlight_color="#ABCDEF"))
    assert _colors(caption_frame_state(styled, _style("word_highlight"), 0.6)) == ["#112233", "#ABCDEF", "#112233"]


def test_highlight_box_renders_active_word_in_text_colour() -> None:
    c = _caption()
    s = caption_frame_state(c, _style("word_highlight", highlight_box=True), 0.6)
    assert s["words"][1]["box"] == HL and s["words"][1]["color"] == WHITE
    assert s["words"][0]["box"] is None
    assert caption_frame_state(c, _style("static", highlight_box=True), 0.6)["words"][1]["box"] is None


def test_states_are_deterministic() -> None:
    c, st = _caption(), _style("dynamic_emphasis")
    assert caption_frame_state(c, st, 0.37) == caption_frame_state(c.model_copy(deep=True), st, 0.37)


def test_shared_fixtures_match_engine() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from export_caption_fixtures import ANIMATIONS, build_fixtures

    on_disk = json.loads(FIXTURES.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(build_fixtures()))
    assert on_disk["cases"] == fresh["cases"], "run scripts/export_caption_fixtures.py"
    kinds = {c["style"]["animation"]["type"] for c in on_disk["cases"]}
    assert kinds == set(ANIMATIONS)
