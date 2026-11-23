"""Framing-mode compilation: ``FramingConfig`` -> ``CompiledLayout`` (normative, see
ARCHITECTURE.md section 7 and app/schemas/framing.py). Mirrored by the frontend preview;
``scripts/export_framing_fixtures.py`` exports cases so both implementations stay identical.

Compilation rules (canvas W x H = output_width x output_height):

* smart       — ``main``: source = centred crop at the output aspect (in pixels), keyframes =
                ``smart.keyframes``, dest = full canvas, cover.
* manual      — ``main``: source = ``manual.crop``, keyframes = ``manual.keyframes``, dest = full
                canvas, fit = ``manual.fit``.
* facecam_gameplay — ``facecam`` (role face, z 0) dest = band of height ``split`` at the top or
                bottom; ``gameplay`` (role content, z 1) dest = the remaining band minus ``gap``
                output px (the gap sits between the panels).
* pip         — ``main`` (z 0) dest = full canvas, fit = ``main_fit``; ``overlay`` (role face, z 1)
                source = ``overlay_source``, dest = ``overlay_dest`` + radius/border/shadow.
* full_frame  — ``main``: source = full frame, contain; dest width = ``scale`` (centred),
                dest height = the contained picture height ``scale * W * src_h / src_w`` (capped
                at the canvas), top = ``vertical_position * (1 - dest height)``.

``background_source`` = the main/gameplay layer source (full frame for full_frame).
All normalised numbers are rounded to 6 decimals.
"""

from __future__ import annotations

from app.core.errors import ValidationFailed
from app.schemas.framing import (
    FULL_FRAME,
    CompiledLayer,
    CompiledLayout,
    FacecamGameplayConfig,
    FramingConfig,
    PipConfig,
    Rect,
)

_MODES = ("smart", "manual", "facecam_gameplay", "pip", "full_frame")


def _r(v: float) -> float:
    return round(float(v), 6)


def make_rect(x: float, y: float, w: float, h: float) -> Rect:
    """Rounded, clamped normalised rect."""
    w = min(max(w, 1e-4), 1.0)
    h = min(max(h, 1e-4), 1.0)
    x = min(max(x, 0.0), 1.0 - w)
    y = min(max(y, 0.0), 1.0 - h)
    return Rect(x=_r(x), y=_r(y), w=_r(w), h=_r(h))


def _check_source(source_w: int, source_h: int) -> None:
    if source_w <= 0 or source_h <= 0:
        raise ValidationFailed("Source dimensions must be positive")


def aspect_rect(source_w: int, source_h: int, aspect: float, *, cx: float = 0.5, cy: float = 0.5,
                height: float = 1.0) -> Rect:
    """Normalised source rect whose PIXEL aspect (w/h) is ``aspect``, at most ``height`` of
    the source tall (shrunk to fit the width), centred on ``(cx, cy)`` and clamped inside."""
    _check_source(source_w, source_h)
    h_px = source_h * min(max(height, 1e-4), 1.0)
    w_px = h_px * aspect
    if w_px > source_w:
        w_px = float(source_w)
        h_px = w_px / aspect
    w, h = w_px / source_w, h_px / source_h
    return make_rect(cx - w / 2, cy - h / 2, w, h)


def centered_crop(source_w: int, source_h: int, aspect: float) -> Rect:
    """Largest centred source rect with the given pixel aspect."""
    return aspect_rect(source_w, source_h, aspect)


def panel_dests(fg: FacecamGameplayConfig, canvas_h: int) -> tuple[Rect, Rect]:
    """``(facecam_dest, gameplay_dest)`` canvas rects of the stacked layout."""
    split = fg.split
    gap = min(fg.gap / canvas_h, max(0.0, 1.0 - split - 0.05))
    rest = 1.0 - split - gap
    if fg.facecam_position == "top":
        return make_rect(0, 0, 1, split), make_rect(0, split + gap, 1, rest)
    return make_rect(0, 1.0 - split, 1, split), make_rect(0, 0, 1, rest)


def panel_aspects(config: FramingConfig, fg: FacecamGameplayConfig | None = None) -> tuple[float, float]:
    """Pixel aspect ratios ``(facecam_panel, gameplay_panel)`` of the stacked layout."""
    fg = fg or config.facecam_gameplay
    fd, gd = panel_dests(fg, config.output_height)
    W, H = config.output_width, config.output_height
    return (fd.w * W) / (fd.h * H), (gd.w * W) / (gd.h * H)


def full_frame_dest(config: FramingConfig, source_w: int, source_h: int) -> Rect:
    """Dest rect of the full_frame picture (see module docstring)."""
    ff = config.full_frame
    W, H = config.output_width, config.output_height
    h = min(1.0, ff.scale * W * source_h / source_w / H)
    return make_rect((1.0 - ff.scale) / 2, ff.vertical_position * (1.0 - h), ff.scale, h)


def default_framing_config(source_w: int, source_h: int, mode: str = "full_frame") -> FramingConfig:
    """A sensible starting config for ``mode`` sized for the source: manual crop = centred rect
    at the output aspect; facecam/pip sources sized to their panel aspect (top-left corner);
    gameplay = centred region at the gameplay panel aspect."""
    _check_source(source_w, source_h)
    if mode not in _MODES:
        raise ValidationFailed(f"Unknown framing mode '{mode}'")
    cfg = FramingConfig(mode=mode)
    out_aspect = cfg.output_width / cfg.output_height
    manual = cfg.manual.model_copy(update={"crop": centered_crop(source_w, source_h, out_aspect)})
    face_aspect, game_aspect = panel_aspects(cfg)
    fg = cfg.facecam_gameplay.model_copy(update={
        "facecam": aspect_rect(source_w, source_h, face_aspect, cx=0.0, cy=0.0, height=0.3),
        "gameplay": aspect_rect(source_w, source_h, game_aspect),
    })
    pip_dest = cfg.pip.overlay_dest
    overlay_aspect = (pip_dest.w * cfg.output_width) / (pip_dest.h * cfg.output_height)
    pip = cfg.pip.model_copy(update={
        "overlay_source": aspect_rect(source_w, source_h, overlay_aspect, cx=0.0, cy=0.0, height=0.3),
    })
    return cfg.model_copy(update={"manual": manual, "facecam_gameplay": fg, "pip": pip})


# ---------------------------------------------------------------- compile
def _smart_layers(config: FramingConfig, source_w: int, source_h: int) -> list[CompiledLayer]:
    src = centered_crop(source_w, source_h, config.output_width / config.output_height)
    return [CompiledLayer(id="main", role="main", source=src, keyframes=list(config.smart.keyframes),
                          dest=FULL_FRAME, fit="cover", z=0)]


def _manual_layers(config: FramingConfig) -> list[CompiledLayer]:
    m = config.manual
    return [CompiledLayer(id="main", role="main", source=m.crop, keyframes=list(m.keyframes),
                          dest=FULL_FRAME, fit=m.fit, z=0)]


def _facecam_layers(config: FramingConfig) -> list[CompiledLayer]:
    fg = config.facecam_gameplay
    face_dest, game_dest = panel_dests(fg, config.output_height)
    return [
        CompiledLayer(id="facecam", role="face", source=fg.facecam, dest=face_dest, fit=fg.facecam_fit, z=0),
        CompiledLayer(id="gameplay", role="content", source=fg.gameplay, dest=game_dest, fit=fg.gameplay_fit, z=1),
    ]


def _pip_layers(pip: PipConfig) -> list[CompiledLayer]:
    return [
        CompiledLayer(id="main", role="main", source=pip.main, dest=FULL_FRAME, fit=pip.main_fit, z=0),
        CompiledLayer(id="overlay", role="face", source=pip.overlay_source, dest=pip.overlay_dest,
                      fit=pip.overlay_fit, radius=pip.overlay_radius, border_width=pip.overlay_border_width,
                      border_color=pip.overlay_border_color, shadow=pip.overlay_shadow, z=1),
    ]


def _full_frame_layers(config: FramingConfig, source_w: int, source_h: int) -> list[CompiledLayer]:
    return [CompiledLayer(id="main", role="main", source=FULL_FRAME,
                          dest=full_frame_dest(config, source_w, source_h), fit="contain", z=0)]


def compile_layout(config: FramingConfig, source_w: int, source_h: int) -> CompiledLayout:
    """Compile ``config`` into drawable layers for a ``source_w`` x ``source_h`` source."""
    _check_source(source_w, source_h)
    mode = config.mode
    if mode == "smart":
        layers = _smart_layers(config, source_w, source_h)
    elif mode == "manual":
        layers = _manual_layers(config)
    elif mode == "facecam_gameplay":
        layers = _facecam_layers(config)
    elif mode == "pip":
        layers = _pip_layers(config.pip)
    else:
        layers = _full_frame_layers(config, source_w, source_h)
    if mode == "full_frame":
        background_source = FULL_FRAME
    else:
        bg_layer = next(layer for layer in layers if layer.id in ("main", "gameplay"))
        background_source = bg_layer.source
    return CompiledLayout(width=config.output_width, height=config.output_height,
                          background=config.background, background_source=background_source,
                          layers=layers)
