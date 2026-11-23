"""Resumable chunked upload API models."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.sources.schemas import AnalysisRequest


class UploadInit(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    size: int = Field(gt=0)
    mime_type: str | None = Field(default=None, max_length=100)


class UploadOut(BaseModel):
    upload_id: uuid.UUID
    filename: str
    size: int
    chunk_size: int
    total_chunks: int
    received_chunks: list[int]
    status: str  # uploading | assembling | completed | aborted
    expires_at: datetime


class ChunkOut(BaseModel):
    received_chunks: list[int]
    complete: bool


class UploadComplete(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    rights_confirmed: bool = False
    rights_statement: str | None = Field(default=None, max_length=2000)
    video_source_id: uuid.UUID | None = None  # attach to a YouTube-link video (or one whose original was deleted)
    attribution: str | None = Field(default=None, max_length=500)
    auto_analyze: bool = True               # API v2: processing starts automatically
    analysis: AnalysisRequest | None = None
