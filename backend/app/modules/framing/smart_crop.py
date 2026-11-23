"""AI smart crop: a calm virtual camera that follows the primary person (or the active
speaker) and NEVER lets the followed face leave the crop when it physically fits.

Per shot (one followed track; shots change with HARD CUTS, never fast pans):
1. target = face centre horizontally; vertically (zoom > 1) the face centre sits ~38% from
   the crop top, with at least ``headroom`` (of the crop height) above the head;
2. deadzone: the camera only moves when the target leaves ``deadzone * crop width``;
3. zero-phase exponential smoothing (time constant grows with ``smoothing``);
4. velocity clamp (calmer camera for higher ``smoothing``);
5. containment: the whole head box (face + hair/chin/ears margin) is clamped inside the crop
   whenever it fits — this overrides 2-4, so the face is never cut for smoothness' sake;
6. clamp to the frame.
Missing detections hold the last known position. Keyframes are emitted at 5 Hz plus a
``cut=True`` keyframe at every shot change. Crop size is computed in PIXELS
(height = source_h / zoom, width = height * output_aspect) then normalised per axis.
"""

from __future__ import annotations

import math

import numpy as np

from app.modules.framing.layout import centered_crop, make_rect
from app.modules.framing.tracks import box_at, head_box, primary_track, track_by_id
from app.schemas.framing import FULL_FRAME, ClipVisionAnalysis, FaceTrack, Keyframe, Rect, SmartCropConfig

KEYFRAME_STEP = 0.2
FACE_LEVEL = 0.38  # face centre height in the crop when zoomed (fraction from the top)


def crop_size(source_w: int, source_h: int, zoom: float, output_aspect: float) -> tuple[float, float]:
    """Normalised ``(width, height)`` of the smart crop."""
    h_px = source_h / max(1.0, zoom)
    w_px = h_px * output_aspect
    if w_px > source_w:
        w_px = float(source_w)
        h_px = w_px / output_aspect
    return w_px / source_w, h_px / source_h


def _followed_track(vision: ClipVisionAnalysis, smart: SmartCropConfig) -> FaceTrack | None:
    return track_by_id(vision, smart.primary_track_id) or primary_track(vision)


def shot_plan(vision: ClipVisionAnalysis, smart: SmartCropConfig, duration: float) -> list[tuple[float, float, int]]:
    """``[(start, end, track_id)]`` covering ``[0, duration]``. With ``auto_switch`` the
    speaker timeline is followed, merging shots shorter than ``min_shot_seconds``."""
    main = _followed_track(vision, smart)
    if main is None:
        return []
    known = {t.id for t in vision.tracks}
    timeline = [s for s in vision.speaker_timeline if s.track_id in known and s.end > s.start]
    if not smart.auto_switch or len({s.track_id for s in timeline}) < 2:
        return [(0.0, duration, main.id)]
    shots: list[list] = []
    for seg in sorted(timeline, key=lambda s: s.start):
        start, end = max(0.0, seg.start), min(duration, seg.end)
        if end <= start:
            continue
        if shots and shots[-1][2] == seg.track_id:
            shots[-1][1] = end
        else:
            shots.append([start, end, seg.track_id])
    merged: list[list] = []
    for shot in shots:  # too-short shots are absorbed by the previous shot (no jumpy cuts)
        if merged and (shot[1] - shot[0] < smart.min_shot_seconds or merged[-1][2] == shot[2]):
            merged[-1][1] = shot[1]
        else:
            merged.append(shot)
    if len(merged) > 1 and merged[0][1] - merged[0][0] < smart.min_shot_seconds:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    if not merged:
        return [(0.0, duration, main.id)]
    merged[0][0], merged[-1][1] = 0.0, duration
    for a, b in zip(merged, merged[1:], strict=False):  # contiguous shots
        b[0] = a[1]
    return [(float(a), float(b), int(c)) for a, b, c in merged]


def _faces_for(track: FaceTrack, times: np.ndarray) -> list[Rect | None]:
    """Face box per time, holding the last (or first) known box where it is missing."""
    faces = [box_at(track, float(t)) for t in times]
    first = next((f for f in faces if f is not None), None)
    if first is None:
        return faces
    held: list[Rect | None] = []
    last = first
    for f in faces:
        last = f or last
        held.append(last)
    return held


def _deadzone(targets: np.ndarray, dz: float) -> np.ndarray:
    out = np.empty_like(targets)
    p = targets[0]
    for i, tgt in enumerate(targets):
        if tgt - p > dz:
            p = tgt - dz
        elif p - tgt > dz:
            p = tgt + dz
        out[i] = p
    return out


def _smooth(path: np.ndarray, dt: float, smoothing: float) -> np.ndarray:
    """Zero-phase (forward + backward) exponential smoothing."""
    tau = 0.1 + 1.4 * smoothing
    alpha = 1.0 - math.exp(-dt / tau)
    fwd = path.copy()
    for i in range(1, len(fwd)):
        fwd[i] = fwd[i - 1] + alpha * (fwd[i] - fwd[i - 1])
    out = fwd.copy()
    for i in range(len(out) - 2, -1, -1):
        out[i] = out[i + 1] + alpha * (out[i] - out[i + 1])
    return out


def _clamp_velocity(path: np.ndarray, vmax_step: float) -> np.ndarray:
    out = path.copy()
    for i in range(1, len(out)):
        out[i] = out[i - 1] + float(np.clip(out[i] - out[i - 1], -vmax_step, vmax_step))
    return out


def _axis_path(targets: np.ndarray, lo: np.ndarray, hi: np.ndarray, size: float, smart: SmartCropConfig,
               dt: float) -> np.ndarray:
    """Camera positions along one axis. ``lo``/``hi`` bound the position so the head fits
    (NaN = no constraint); ``size`` is the crop extent on this axis."""
    limit = 1.0 - size
    path = np.clip(targets, 0.0, limit)
    path = _deadzone(path, smart.deadzone * size)
    path = _smooth(path, dt, smart.smoothing)
    vmax = size * (0.35 + 1.2 * (1.0 - smart.smoothing))
    path = _clamp_velocity(path, vmax * dt)
    has = ~np.isnan(lo)
    fits = has & (lo <= hi)
    path[fits] = np.clip(path[fits], lo[fits], hi[fits])
    wide = has & (lo > hi)  # head larger than the crop: centre it
    path[wide] = (lo[wide] + hi[wide]) / 2
    return np.clip(path, 0.0, limit)


def _shot_positions(faces: list[Rect | None], cw: float, ch: float, smart: SmartCropConfig, dt: float
                    ) -> tuple[np.ndarray, np.ndarray]:
    n = len(faces)
    tx, ty = np.full(n, 0.5 - cw / 2), np.full(n, 0.5 - ch / 2)
    lox, hix = np.full(n, np.nan), np.full(n, np.nan)
    loy, hiy = np.full(n, np.nan), np.full(n, np.nan)
    for i, face in enumerate(faces):
        if face is None:
            continue
        head = head_box(face)
        tx[i] = face.cx - cw / 2
        ty[i] = min(face.cy - FACE_LEVEL * ch, head.y - smart.headroom * ch)
        lox[i], hix[i] = head.x + head.w - cw, head.x
        loy[i], hiy[i] = head.y + head.h - ch, head.y
    xs = _axis_path(tx, lox, hix, cw, smart, dt)
    ys = _axis_path(ty, loy, hiy, ch, smart, dt) if ch < 0.999 else np.zeros(n)
    return xs, ys


def _simplify(keys: list[Keyframe]) -> list[Keyframe]:
    """Drop keyframes that repeat both neighbours (static camera) — cuts are kept."""
    out: list[Keyframe] = []
    for i, k in enumerate(keys):
        if 0 < i < len(keys) - 1 and not k.cut and not keys[i + 1].cut \
                and k.rect == keys[i - 1].rect == keys[i + 1].rect:
            continue
        out.append(k)
    return out


def compute_smart_keyframes(vision: ClipVisionAnalysis | None, smart: SmartCropConfig, *, source_w: int,
                            source_h: int, duration: float, output_aspect: float = 9 / 16) -> list[Keyframe]:
    """Keyframes (clip-relative) of the smart-crop source rect. Portrait sources -> full frame;
    no faces -> a static centred crop."""
    if source_w / source_h <= output_aspect * 1.01:
        return [Keyframe(t=0.0, rect=FULL_FRAME)]
    cw, ch = crop_size(source_w, source_h, smart.zoom, output_aspect)
    centred = centered_crop(source_w, source_h, output_aspect) if smart.zoom <= 1 else \
        make_rect(0.5 - cw / 2, 0.5 - ch / 2, cw, ch)
    plan = shot_plan(vision, smart, duration) if vision is not None else []
    if not plan or duration <= 0:
        return [Keyframe(t=0.0, rect=centred)]
    steps = max(1, int(math.ceil(duration / KEYFRAME_STEP - 1e-9)))
    grid = np.minimum(np.arange(steps + 1) * KEYFRAME_STEP, duration)
    keys: list[Keyframe] = []
    for si, (start, end, track_id) in enumerate(plan):
        last = si == len(plan) - 1
        times = grid[(grid >= start - 1e-9) & ((grid < end - 1e-9) | (last & (grid <= end + 1e-9)))]
        times = np.unique(np.concatenate([[start], times]))
        track = track_by_id(vision, track_id)
        if track is None:
            continue
        faces = _faces_for(track, times)
        if all(f is None for f in faces):  # nobody to follow in this shot: hold the previous framing
            rect = keys[-1].rect if keys else centred
            keys.extend(Keyframe(t=round(float(t), 3), rect=rect) for t in times[:1])
            continue
        xs, ys = _shot_positions(faces, cw, ch, smart, KEYFRAME_STEP)
        for i, t in enumerate(times):
            keys.append(Keyframe(t=round(float(t), 3), rect=make_rect(xs[i], ys[i], cw, ch),
                                 cut=bool(i == 0 and si > 0)))
    return _simplify(keys) or [Keyframe(t=0.0, rect=centred)]
