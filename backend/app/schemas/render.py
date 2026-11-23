"""Render contract — what the worker hands to ``app.modules.rendering.renderer.render_clip``."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.framing import CompiledLayout, FaceTrack
from app.schemas.media import MediaInfo
from app.schemas.subtitles import Caption, SubtitleStyle


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)


class WatermarkOptions(_Model):
    enabled: bool = False
    asset_id: uuid.UUID | None = None       # PNG/JPEG/WebP watermark image (UserAsset kind=watermark)
    text: str | None = Field(default=None, max_length=80)  # used when no image asset
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"] = "top_right"
    opacity: float = Field(default=0.7, ge=0.05, le=1)
    scale: float = Field(default=0.18, ge=0.03, le=0.6)    # watermark width as a fraction of canvas width
    margin: float = Field(default=0.04, ge=0, le=0.2)


class RenderOptions(_Model):
    normalize_audio: bool = True
    target_lufs: float = Field(default=-14.0, ge=-30, le=-6)
    remove_silence: bool = False
    min_silence: float = Field(default=0.6, ge=0.2, le=5)     # only silences longer than this are cut
    silence_padding: float = Field(default=0.12, ge=0, le=1)  # keep this much around speech
    intro_asset_id: uuid.UUID | None = None
    outro_asset_id: uuid.UUID | None = None
    watermark: WatermarkOptions = Field(default_factory=WatermarkOptions)
    burn_subtitles: bool = True
    video_crf: int = Field(default=18, ge=12, le=32)
    video_preset: Literal["ultrafast", "veryfast", "faster", "fast", "medium", "slow"] = "medium"
    max_fps: float = Field(default=60.0, ge=15, le=60)
    audio_bitrate_kbps: int = Field(default=192, ge=96, le=320)
    encoder: Literal["auto", "nvenc", "libx264"] = "auto"   # auto = NVENC when FFmpeg can use it


class FontFile(_Model):
    family: str
    weight: int = 400
    italic: bool = False
    path: Path


class RenderSpec(_Model):
    source_path: Path
    start: float                                  # source seconds
    end: float
    media: MediaInfo
    layout: CompiledLayout                        # compiled from FramingConfig (keyframes clip-relative)
    subtitles_enabled: bool = True
    captions: list[Caption] = Field(default_factory=list)
    style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    face_tracks: list[FaceTrack] = Field(default_factory=list)  # for subtitle avoid_faces
    options: RenderOptions = Field(default_factory=RenderOptions)
    silences: list[tuple[float, float]] = Field(default_factory=list)  # source seconds (for remove_silence)
    intro_path: Path | None = None
    outro_path: Path | None = None
    watermark_path: Path | None = None
    background_image_path: Path | None = None
    custom_fonts: list[FontFile] = Field(default_factory=list)
    output_path: Path
    thumbnail_path: Path | None = None


class RenderResult(_Model):
    output_path: Path
    thumbnail_path: Path | None = None
    duration: float
    width: int
    height: int
    size_bytes: int
    fps: float
    kept_ranges: list[tuple[float, float]] = Field(default_factory=list)  # source ranges kept (silence removal)
