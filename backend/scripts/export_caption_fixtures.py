"""Export caption animation states to shared/fixtures/caption_states.json.

The frontend Subtitle Studio preview (``frontend/src/lib/subtitles``) unit-tests its
TypeScript port of ``caption_frame_state`` against these fixtures so the live preview and
the server render animate identically.

Usage:  .venv/Scripts/python.exe scripts/export_caption_fixtures.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DEFAULT_OUT = BACKEND.parent / "shared" / "fixtures" / "caption_states.json"
ANIMATIONS = ["static", "word_highlight", "karaoke", "pop", "fade", "typewriter", "important_words",
              "dynamic_emphasis"]
TIMES = [-0.01, 0.0, 0.04, 0.1, 0.16, 0.25, 0.32, 0.36, 0.5, 0.62, 1.05, 1.15, 1.6, 1.68, 1.75, 1.79, 1.8, 2.0]


def _caption(**word_overrides: dict) -> dict:
    words = [
        {"text": "This", "start": 0.0, "end": 0.3},
        {"text": "is", "start": 0.35, "end": 0.6},
        {"text": "really", "start": 0.6, "end": 1.0, "emphasis": True},
        {"text": "important!", "start": 1.1, "end": 1.6},
    ]
    for idx, patch in word_overrides.items():
        words[int(idx.lstrip("w"))].update(patch)
    return {"id": "fixture", "start": 0.0, "end": 1.8, "words": words}


def _cases() -> list[dict]:
    cases: list[dict] = []
    for kind in ANIMATIONS:
        cases.append({"name": f"{kind}", "animation": {"type": kind}, "caption": _caption()})
    for kind in ("pop", "fade", "typewriter", "dynamic_emphasis", "word_highlight"):
        cases.append({"name": f"{kind}_speed_2", "animation": {"type": kind, "speed": 2.0, "highlight_scale": 1.3},
                      "caption": _caption()})
    for kind in ("word_highlight", "karaoke", "pop", "dynamic_emphasis", "static"):
        cases.append({"name": f"{kind}_highlight_box", "animation": {"type": kind, "highlight_box": True},
                      "caption": _caption(w1={"color": "#00FF00"})})
    overrides = _caption(w0={"color": "#00FF00"}, w1={"highlight": True}, w3={"scale": 1.5, "emphasis": True})
    for kind in ANIMATIONS:
        cases.append({"name": f"{kind}_word_overrides", "animation": {"type": kind}, "caption": overrides})
    styled = _caption()
    styled["style_override"] = {"text_color": "#112233", "highlight_color": "#ABCDEF"}
    for kind in ("word_highlight", "karaoke", "static"):
        cases.append({"name": f"{kind}_caption_override", "animation": {"type": kind}, "caption": styled})
    return cases


def build_fixtures() -> dict:
    """Compute the fixture document (pure; also used by the backend tests)."""
    from app.modules.subtitles.animation import caption_frame_state
    from app.schemas.subtitles import Caption, SubtitleStyle

    out_cases = []
    for case in _cases():
        style = SubtitleStyle(text_color="#FFFFFF", highlight_color="#FFD400", emphasis_color="#FF0033",
                              animation=case["animation"])
        caption = Caption.model_validate(case["caption"])
        out_cases.append({
            "name": case["name"],
            "style": style.model_dump(mode="json"),
            "caption": caption.model_dump(mode="json"),
            "times": TIMES,
            "states": [caption_frame_state(caption, style, t) for t in TIMES],
        })
    return {
        "version": 1,
        "generated_by": "backend/scripts/export_caption_fixtures.py",
        "description": "caption_frame_state(caption, style, t) outputs; floats rounded to 6 decimals "
                       "(compare with a 1e-6 tolerance). "
                       "Semantics: backend/app/modules/subtitles/animation.py + app/schemas/subtitles.py.",
        "cases": out_cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    doc = build_fixtures()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(c["states"]) for c in doc["cases"])
    print(f"Wrote {args.out} ({len(doc['cases'])} cases, {n} states)")


if __name__ == "__main__":
    main()
