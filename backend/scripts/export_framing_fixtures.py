"""Export framing geometry fixtures for the TypeScript preview (``frontend/src/lib/framing``).

Writes ``shared/fixtures/framing_cases.json`` with inputs and the backend's outputs for
``default_framing_config``, ``compile_layout``, ``rect_at`` (incl. cut keyframes),
``fit_rects``, ``visible_source_rect_px`` and ``source_to_canvas`` so the frontend can assert
identical results (compare numbers with ``tolerance``).

Usage::

    cd backend && .venv/Scripts/python.exe scripts/export_framing_fixtures.py [output.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import REPO_DIR  # noqa: E402
from app.modules.framing.geometry import (  # noqa: E402
    fit_rects,
    rect_at,
    source_to_canvas,
    visible_source_rect_px,
)
from app.modules.framing.layout import compile_layout, default_framing_config  # noqa: E402
from app.schemas.framing import CompiledLayer, FramingConfig, Keyframe, Rect  # noqa: E402

SOURCES = [(1920, 1080), (1280, 720), (1080, 1920), (1440, 1080)]
MODES = ["smart", "manual", "facecam_gameplay", "pip", "full_frame"]
DEFAULT_OUT = REPO_DIR / "shared" / "fixtures" / "framing_cases.json"


def _r(v: float) -> float:
    return round(float(v), 9)


def _px(t: tuple[float, float, float, float]) -> list[float]:
    return [_r(v) for v in t]


def _rect(r: Rect | None) -> dict | None:
    return None if r is None else {"x": _r(r.x), "y": _r(r.y), "w": _r(r.w), "h": _r(r.h)}


SMART_KEYS = [
    Keyframe(t=0.0, rect=Rect(x=0.1, y=0.0, w=0.316406, h=1.0)),
    Keyframe(t=2.0, rect=Rect(x=0.3, y=0.0, w=0.316406, h=1.0)),
    Keyframe(t=3.0, rect=Rect(x=0.6, y=0.0, w=0.316406, h=1.0), cut=True),
    Keyframe(t=5.0, rect=Rect(x=0.5, y=0.1, w=0.25, h=0.8)),
]


def _variants(sw: int, sh: int) -> list[tuple[str, FramingConfig]]:
    out = [(f"default_{m}", default_framing_config(sw, sh, m)) for m in MODES]
    base = default_framing_config(sw, sh, "facecam_gameplay")
    fg = base.facecam_gameplay.model_copy(update={"facecam_position": "bottom", "split": 0.4, "gap": 24.0,
                                                  "gameplay_fit": "contain"})
    out.append(("facecam_bottom_gap_contain", base.model_copy(update={"facecam_gameplay": fg})))
    ff = default_framing_config(sw, sh, "full_frame")
    out.append(("full_frame_scaled_top", ff.model_copy(update={
        "full_frame": ff.full_frame.model_copy(update={"scale": 0.8, "vertical_position": 0.2})})))
    man = default_framing_config(sw, sh, "manual")
    out.append(("manual_contain_keyframes", man.model_copy(update={"manual": man.manual.model_copy(update={
        "fit": "contain", "crop": Rect(x=0.1, y=0.1, w=0.5, h=0.6), "keyframes": SMART_KEYS[:2]})})))
    smart = default_framing_config(sw, sh, "smart")
    out.append(("smart_with_cut_keyframes", smart.model_copy(update={
        "smart": smart.smart.model_copy(update={"keyframes": SMART_KEYS})})))
    pip = default_framing_config(sw, sh, "pip")
    out.append(("pip_cover_custom", pip.model_copy(update={"pip": pip.pip.model_copy(update={
        "main": Rect(x=0.2, y=0.0, w=0.5, h=1.0), "main_fit": "cover",
        "overlay_dest": Rect(x=0.5, y=0.65, w=0.44, h=0.2), "overlay_border_width": 6.0})})))
    return out


def _layout_cases() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    defaults, layouts, visible, to_canvas = [], [], [], []
    face = Rect(x=0.45, y=0.3, w=0.08, h=0.14)
    for sw, sh in SOURCES:
        for mode in MODES:
            cfg = default_framing_config(sw, sh, mode)
            defaults.append({"source_w": sw, "source_h": sh, "mode": mode, "expected": cfg.model_dump(mode="json")})
        for name, cfg in _variants(sw, sh):
            layout = compile_layout(cfg, sw, sh)
            layouts.append({"name": name, "source_w": sw, "source_h": sh, "config": cfg.model_dump(mode="json"),
                            "expected": layout.model_dump(mode="json")})
            for layer in layout.layers:
                for t in (0.0, 2.5, 3.0):
                    args = {"layer": layer.model_dump(mode="json"), "t": t, "source_w": sw, "source_h": sh,
                            "canvas_w": layout.width, "canvas_h": layout.height}
                    visible.append({**args, "expected": _rect(visible_source_rect_px(
                        layer, t, sw, sh, layout.width, layout.height))})
                    to_canvas.append({**args, "face": _rect(face), "expected": _rect(source_to_canvas(
                        face, layer, t, sw, sh, layout.width, layout.height))})
    return defaults, layouts, visible, to_canvas


def _rect_at_cases() -> list[dict]:
    layers = [
        CompiledLayer(id="main", keyframes=SMART_KEYS),
        CompiledLayer(id="main", source=Rect(x=0.2, y=0.1, w=0.3, h=0.5)),
        CompiledLayer(id="main", keyframes=[Keyframe(t=1.0, rect=Rect(x=0.0, y=0.0, w=0.5, h=0.5), cut=True)]),
    ]
    times = [0.0, 0.5, 1.0, 1.999, 2.0, 2.5, 2.999, 3.0, 3.5, 4.0, 5.0, 9.0]
    return [{"layer": layer.model_dump(mode="json"),
             "cases": [{"t": t, "expected": _rect(rect_at(layer, t))} for t in times]} for layer in layers]


def _fit_cases() -> list[dict]:
    srcs = [(0, 0, 1920, 1080), (100, 50, 607.5, 1080), (10, 20, 400, 300), (0, 0, 1080, 1920), (5, 5, 0, 10)]
    dsts = [(0, 0, 1080, 1920), (0, 0, 1080, 672), (0, 700, 1080, 1220), (60, 60, 453.6, 460.8)]
    out = []
    for src in srcs:
        for dst in dsts:
            for fit in ("cover", "contain"):
                crop, draw = fit_rects(src, dst, fit)
                out.append({"src": list(src), "dst": list(dst), "fit": fit,
                            "expected": {"crop": _px(crop), "draw": _px(draw)}})
    return out


def build() -> dict:
    """All fixture groups."""
    defaults, layouts, visible, to_canvas = _layout_cases()
    return {
        "version": 1,
        "generated_by": "backend/scripts/export_framing_fixtures.py",
        "tolerance": 1e-6,
        "default_framing_config": defaults,
        "compile_layout": layouts,
        "rect_at": _rect_at_cases(),
        "fit_rects": _fit_cases(),
        "visible_source_rect_px": visible,
        "source_to_canvas": to_canvas,
    }


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    print(f"wrote {out} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
