"""HTTP models of the rendering API (docs/API.md, section Rendering)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.framing import FramingConfig


class RenderRequest(BaseModel):
    acknowledge_warnings: bool = False


class RenderStateOut(BaseModel):
    render_status: str
    render_is_current: bool
    job: Any | None = None
    output_url: str | None = None
    output_size: int | None = None
    output_duration: float | None = None
    rendered_at: datetime | None = None
    render_error: str | None = None


class DownloadOut(BaseModel):
    url: str
    filename: str


class PreviewFrameIn(BaseModel):
    t: float = Field(ge=0)
    framing: FramingConfig | None = None
