"""Provider interfaces for the AI steps of the pipeline.

A provider is a small object with a ``name`` and one method; the pipeline only talks to these
interfaces (via :mod:`app.modules.ai.registry`), so adding e.g. a hosted transcription or
video-understanding service means writing one module that implements the matching protocol
and registering it -- nothing in the pipeline changes.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.modules.analysis.progress import ProgressFn
from app.schemas.media import ClipMetadata, HighlightCandidate, TranscriptData

if TYPE_CHECKING:
    from app.modules.highlights.llm import LLMMoment
    from app.modules.highlights.sentences import Sentence


@runtime_checkable
class TranscriptionProvider(Protocol):
    """Speech -> word-timed transcript in the spoken language (no translation)."""

    name: str

    def available(self) -> bool: ...

    def transcribe(self, audio: Path, *, language: str | None, mode: str | None, model_name: str | None,
                   progress: ProgressFn | None,
                   on_plan: Callable[[Any], None] | None = None) -> TranscriptData: ...


@runtime_checkable
class HighlightRanker(Protocol):
    """Optional second opinion on the signal-based candidates (returns moments to blend)."""

    name: str

    def active(self, use_llm: bool) -> bool: ...

    def rank(self, *, sentences: list[Sentence], candidates: list[HighlightCandidate], duration: float,
             targets: list[float], max_clips: int, source_title: str | None) -> list[LLMMoment] | None: ...


@runtime_checkable
class MetadataProvider(Protocol):
    """Clip transcript -> titles, descriptions, captions, hashtags. ``None`` = use the fallback."""

    name: str

    def active(self, use_llm: bool) -> bool: ...

    def generate(self, *, transcript_text: str, source_title: str | None, category: str | None,
                 hook: str | None) -> ClipMetadata | None: ...
