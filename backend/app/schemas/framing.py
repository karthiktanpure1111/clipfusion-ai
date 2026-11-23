"""Framing contract — shared by the smart-crop engine, renderer, API and the frontend editor.

Coordinate systems (normative, mirrored in ``frontend/src/lib/framing/*``):

* **Source rects** are normalised to the source frame: ``x, y, w, h`` in ``[0, 1]`` where
  ``(0, 0)`` is the top-left of the *displayed* (rotation-corrected) frame.
* **Canvas rects** (``dest``) are normalised to the output canvas (1080x1920 by default).
* **Keyframe times** are clip-relative seconds. Between keyframes rects are linearly
  interpolated (x, y, w, h independently); before the first / after the last keyframe the
  nearest keyframe is held. A keyframe with ``cut=True`` is a hard cut: no interpolation
  from the previous keyframe (used for speaker switches).

Fit modes for drawing a source rect S into a destination rect D (pixels):

* ``cover``  — crop S around its centre to D's aspect ratio, then scale to fill D exactly.
* ``contain``— scale S uniformly to fit inside D, centred; uncovered parts of D show the
  background.

Background: the ``background_source`` region (default: the full frame) is drawn ``cover``
into the full canvas, gaussian-blurred with sigma ``blur`` output-pixels (at 1080 px canvas
width; scale proportionally for other sizes), then darkened: ``rgb *= (1 - dim)``.
``color`` fills ``color``; ``image`` draws the asset ``cover``.

Layers are drawn in ascending ``z``. ``radius`` rounds corners (output px), ``border_width``
strokes inside the rect.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FramingModeLiteral = Literal["smart", "manual", "facecam_gameplay", "pip", "full_frame"]
Fit = Literal["cover", "contain"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Rect(_Model):
    x: float = Field(ge=-0.001, le=1.001)
    y: float = Field(ge=-0.001, le=1.001)
    w: float = Field(gt=0, le=1.001)
    h: float = Field(gt=0, le=1.001)

    def clamp(self) -> Rect:
        w = min(max(self.w, 1e-4), 1.0)
        h = min(max(self.h, 1e-4), 1.0)
        x = min(max(self.x, 0.0), 1.0 - w)
        y = min(max(self.y, 0.0), 1.0 - h)
        return Rect(x=x, y=y, w=w, h=h)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h

    def intersection_area(self, other: Rect) -> float:
        ix = max(0.0, min(self.x + self.w, other.x + other.w) - max(self.x, other.x))
        iy = max(0.0, min(self.y + self.h, other.y + other.h) - max(self.y, other.y))
        return ix * iy


FULL_FRAME = Rect(x=0, y=0, w=1, h=1)


class Keyframe(_Model):
    t: float = Field(ge=0)
    rect: Rect
    cut: bool = False


class Background(_Model):
    type: Literal["blur", "color", "image"] = "blur"
    color: str = "#0F0F12"
    blur: float = Field(default=40.0, ge=0, le=200)  # gaussian sigma in output px @1080 width
    dim: float = Field(default=0.35, ge=0, le=1)
    image_asset_id: uuid.UUID | None = None


class SmartCropConfig(_Model):
    """Mode 1 — AI smart crop. ``keyframes`` are computed by the backend from face tracks."""

    primary_track_id: int | None = None       # None = most prominent person (auto)
    auto_switch: bool = False                 # follow the active speaker (with hysteresis)
    zoom: float = Field(default=1.0, ge=1.0, le=3.0)  # 1 = crop height equals source height
    smoothing: float = Field(default=0.85, ge=0, le=1)  # higher = calmer camera
    deadzone: float = Field(default=0.08, ge=0, le=0.4)  # fraction of crop width before panning
    headroom: float = Field(default=0.12, ge=0, le=0.5)  # space above the head when zoomed
    min_shot_seconds: float = Field(default=2.5, ge=0.5, le=30)
    keyframes: list[Keyframe] = Field(default_factory=list)


class ManualCropConfig(_Model):
    """Mode 2 — the user-chosen source region. With ``lock_aspect`` the editor keeps the
    rect at the output aspect ratio so ``cover`` never removes anything the user selected."""

    crop: Rect = Field(default_factory=lambda: Rect(x=0.341, y=0.0, w=0.3164, h=1.0))
    fit: Fit = "cover"
    lock_aspect: bool = True
    keyframes: list[Keyframe] = Field(default_factory=list)  # optional animated manual crop


class FacecamGameplayConfig(_Model):
    """Mode 3 — stacked layout: facecam panel + gameplay panel (also used for two-person
    podcasts: one speaker per panel)."""

    facecam: Rect = Field(default_factory=lambda: Rect(x=0.0, y=0.0, w=0.25, h=0.3))
    gameplay: Rect = Field(default_factory=lambda: Rect(x=0.2, y=0.0, w=0.6, h=1.0))
    facecam_position: Literal["top", "bottom"] = "top"
    split: float = Field(default=0.35, ge=0.15, le=0.7)  # fraction of canvas height for the facecam panel
    gap: float = Field(default=0.0, ge=0, le=60)          # output px between panels
    facecam_fit: Fit = "cover"
    gameplay_fit: Fit = "cover"


class PipConfig(_Model):
    """Mode 4 — main video full canvas + draggable/resizable facecam overlay."""

    main: Rect = Field(default_factory=lambda: Rect(x=0, y=0, w=1, h=1))
    main_fit: Fit = "contain"
    overlay_source: Rect = Field(default_factory=lambda: Rect(x=0.0, y=0.0, w=0.25, h=0.3))
    overlay_dest: Rect = Field(default_factory=lambda: Rect(x=0.06, y=0.06, w=0.42, h=0.24))
    overlay_fit: Fit = "cover"
    overlay_radius: float = Field(default=28.0, ge=0, le=400)
    overlay_border_width: float = Field(default=4.0, ge=0, le=40)
    overlay_border_color: str = "#FFFFFF"
    overlay_shadow: bool = True


class FullFrameConfig(_Model):
    """Mode 5 — never crops: the whole frame is fitted ``contain`` on the background."""

    scale: float = Field(default=1.0, ge=0.5, le=1.0)      # shrink inside the canvas width
    vertical_position: float = Field(default=0.5, ge=0, le=1)  # 0 = top, 0.5 = centre, 1 = bottom


class FramingConfig(_Model):
    version: int = 1
    mode: FramingModeLiteral = "full_frame"
    output_width: int = 1080
    output_height: int = 1920
    background: Background = Field(default_factory=Background)
    smart: SmartCropConfig = Field(default_factory=SmartCropConfig)
    manual: ManualCropConfig = Field(default_factory=ManualCropConfig)
    facecam_gameplay: FacecamGameplayConfig = Field(default_factory=FacecamGameplayConfig)
    pip: PipConfig = Field(default_factory=PipConfig)
    full_frame: FullFrameConfig = Field(default_factory=FullFrameConfig)

    @field_validator("output_width", "output_height")
    @classmethod
    def _even(cls, v: int) -> int:
        if v < 240 or v > 4096:
            raise ValueError("output dimension out of range")
        return v - (v % 2)


# ----------------------------------------------------------- compiled layout
class CompiledLayer(_Model):
    id: str                       # "main" | "facecam" | "gameplay" | "overlay"
    role: Literal["main", "face", "content"] = "main"
    source: Rect = Field(default_factory=lambda: FULL_FRAME)
    keyframes: list[Keyframe] = Field(default_factory=list)  # when non-empty overrides ``source``
    dest: Rect = Field(default_factory=lambda: FULL_FRAME)
    fit: Fit = "cover"
    radius: float = 0.0
    border_width: float = 0.0
    border_color: str = "#FFFFFF"
    shadow: bool = False
    z: int = 0


class CompiledLayout(_Model):
    width: int = 1080
    height: int = 1920
    background: Background = Field(default_factory=Background)
    background_source: Rect = Field(default_factory=lambda: FULL_FRAME)
    layers: list[CompiledLayer] = Field(default_factory=list)


# --------------------------------------------------------------- vision data
class FaceBox(_Model):
    t: float                      # clip-relative seconds
    rect: Rect                    # normalised source rect of the face
    score: float = 1.0


class FaceTrack(_Model):
    id: int
    label: str | None = None
    boxes: list[FaceBox] = Field(default_factory=list)
    coverage: float = 0.0         # fraction of sampled frames where this face was found
    mean_area: float = 0.0        # mean normalised face area
    mean_center: tuple[float, float] = (0.5, 0.5)
    prominence: float = 0.0       # ranking score used to pick the primary person
    is_primary: bool = False
    is_facecam: bool = False      # small, mostly static face near an edge/corner (webcam overlay)
    facecam_rect: Rect | None = None  # estimated webcam overlay region in the source
    speaking_score: float = 0.0   # 0..1 mouth-region activity during detected speech


class ActivityGrid(_Model):
    cols: int
    rows: int
    values: list[float]           # row-major, sums to 1: where motion/visual activity happens


class SpeakerSegment(_Model):
    start: float
    end: float
    track_id: int


class ClipVisionAnalysis(_Model):
    version: int = 1
    detector: str                 # yunet | mediapipe | haar
    sample_fps: float
    source_width: int
    source_height: int
    clip_start: float             # source seconds analysed
    clip_end: float
    tracks: list[FaceTrack] = Field(default_factory=list)
    activity: ActivityGrid | None = None
    speaker_timeline: list[SpeakerSegment] = Field(default_factory=list)
    layout_hint: Literal["single_speaker", "multi_speaker", "facecam_gameplay", "no_faces", "screen_content"] = "no_faces"
    suggested_mode: FramingModeLiteral = "full_frame"


# ---------------------------------------------------------- crop protection
WarningCode = Literal[
    "face_outside",           # a tracked face is (mostly) outside every visible region
    "head_cut",               # a face is partially cut by the frame edge
    "streamer_hidden",        # the primary person / facecam is not visible
    "content_outside",        # most on-screen activity is outside the visible regions
    "subtitle_overlap_face",  # caption block covers a visible face
    "no_faces_detected",      # informational: nothing to protect / analysis missing
    "vision_missing",         # face analysis has not been run yet
    "upscale_quality",        # a layer is upscaled heavily (soft image)
]


class FramingSuggestion(_Model):
    action: Literal["switch_mode", "set_config", "expand_crop", "move_crop", "enable_auto_switch"]
    label: str                    # human readable, shown as a one-click fix
    config_patch: dict = Field(default_factory=dict)  # partial FramingConfig to deep-merge


class FramingWarning(_Model):
    code: WarningCode
    severity: Literal["info", "warning", "critical"] = "warning"
    message: str
    track_id: int | None = None
    visible_fraction: float | None = None   # mean visible fraction of the face area (0..1)
    affected_fraction: float | None = None  # fraction of the clip duration affected (0..1)
    time_ranges: list[tuple[float, float]] = Field(default_factory=list)
    suggestions: list[FramingSuggestion] = Field(default_factory=list)


def blocking_warnings(warnings: list[FramingWarning]) -> list[FramingWarning]:
    """Warnings that must be acknowledged (or fixed) before a render is allowed."""
    return [w for w in warnings if w.severity in ("warning", "critical")]
