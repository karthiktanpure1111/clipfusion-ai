"""Job API models (``JobOut`` is shared with other modules via :func:`app.modules.jobs.service.job_out`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobOut(BaseModel):
    """A persistent background job as exposed by the API (see docs/API.md, Jobs)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: str
    stage: str
    progress: float = Field(ge=0.0, le=1.0)
    message: str | None = None
    error: str | None = None
    attempts: int
    max_attempts: int
    video_source_id: uuid.UUID | None = None
    clip_id: uuid.UUID | None = None
    queue_item_id: uuid.UUID | None = None
    source_title: str | None = None
    clip_title: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    next_attempt_at: datetime | None = None
