"""Point-sampled visibility of source regions through a compiled layout.

A source point is visible at time ``t`` when it lies inside a layer's visible source rect
(:func:`geometry.visible_source_rect_px`) AND its canvas position is not covered by a layer
drawn above it (higher ``z``). The visible fraction of a face is the fraction of a regular
grid of points over the face box that is visible through ANY layer (union over layers).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.modules.framing.geometry import fit_rects, rect_at, to_px, visible_source_rect_px
from app.schemas.framing import CompiledLayout, Rect

GRID = 16  # samples per face side


@dataclass(frozen=True)
class LayerGeom:
    z: int
    layer_id: str
    visible: Rect                                   # normalised source rect
    crop: tuple[float, float, float, float]         # source px
    draw: tuple[float, float, float, float]         # canvas px


def layer_geometry(layout: CompiledLayout, t: float, source_w: int, source_h: int) -> list[LayerGeom]:
    """Geometry of every layer at clip time ``t``, sorted by z."""
    W, H = layout.width, layout.height
    out = []
    for layer in sorted(layout.layers, key=lambda layer: layer.z):
        src = to_px(rect_at(layer, t), source_w, source_h)
        crop, draw = fit_rects(src, to_px(layer.dest, W, H), layer.fit)
        vis = visible_source_rect_px(layer, t, source_w, source_h, W, H)
        out.append(LayerGeom(z=layer.z, layer_id=layer.id, visible=vis, crop=crop, draw=draw))
    return out


def points_visible(px: np.ndarray, py: np.ndarray, geoms: list[LayerGeom], source_w: int,
                   source_h: int) -> np.ndarray:
    """Boolean mask: normalised source points ``(px, py)`` visible through any layer."""
    visible = np.zeros(px.shape, bool)
    for i, g in enumerate(geoms):
        v = g.visible
        inside = (px >= v.x) & (px <= v.x + v.w) & (py >= v.y) & (py <= v.y + v.h)
        if not inside.any():
            continue
        cx, cy, cw, ch = g.crop
        dx, dy, dw, dh = g.draw
        if cw <= 0 or ch <= 0 or dw <= 0 or dh <= 0:
            continue
        canvas_x = dx + (px * source_w - cx) * dw / cw
        canvas_y = dy + (py * source_h - cy) * dh / ch
        occluded = np.zeros(px.shape, bool)
        for above in geoms[i + 1:]:
            if above.z <= g.z:
                continue
            ax, ay, aw, ah = above.draw
            occluded |= (canvas_x >= ax) & (canvas_x <= ax + aw) & (canvas_y >= ay) & (canvas_y <= ay + ah)
        visible |= inside & ~occluded
    return visible


def face_grid(face: Rect, n: int = GRID) -> tuple[np.ndarray, np.ndarray]:
    xs = face.x + (np.arange(n) + 0.5) / n * face.w
    ys = face.y + (np.arange(n) + 0.5) / n * face.h
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel()


def visible_fraction(face: Rect, geoms: list[LayerGeom], source_w: int, source_h: int) -> float:
    """Fraction (0..1) of the face box visible on the canvas."""
    px, py = face_grid(face)
    return float(points_visible(px, py, geoms, source_w, source_h).mean())


def activity_outside(arr: np.ndarray, geoms: list[LayerGeom], source_w: int, source_h: int,
                     per_cell: int = 3) -> float:
    """Share of the activity energy (``arr`` rows x cols, sums to 1) outside every visible region."""
    rows, cols = arr.shape
    offs = (np.arange(per_cell) + 0.5) / per_cell
    cx = (np.arange(cols)[:, None] + offs[None, :]).ravel() / cols
    cy = (np.arange(rows)[:, None] + offs[None, :]).ravel() / rows
    gx, gy = np.meshgrid(cx, cy)
    vis = points_visible(gx, gy, geoms, source_w, source_h).astype(np.float64)
    cell_vis = vis.reshape(rows, per_cell, cols, per_cell).mean(axis=(1, 3))
    return float((arr * (1.0 - cell_vis)).sum())
