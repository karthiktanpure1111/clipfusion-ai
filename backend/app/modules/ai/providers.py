"""Built-in providers: local faster-whisper, signal-only ranking, extractive metadata, and the
optional Claude ranker / metadata writer (only active when ``ANTHROPIC_API_KEY`` is set)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.modules.analysis.progress import ProgressFn
from app.schemas.media import ClipMetadata, HighlightCandidate, TranscriptData

if TYPE_CHECKING:
    from app.modules.highlights.llm import LLMMoment
    from app.modules.highlights.sentences import Sentence


class FasterWhisperProvider:
    """Local Whisper (CTranslate2) on CUDA when usable, else CPU. Free, offline."""

    name = "faster_whisper"

    def available(self) -> bool:
        from app.modules.transcription import service

        return service.whisper_available()

    def transcribe(self, audio: Path, *, language: str | None, mode: str | None, model_name: str | None,
                   progress: ProgressFn | None, on_plan: Callable[[Any], None] | None = None) -> TranscriptData:
        from app.modules.transcription import service  # attribute lookup at call time (tests patch it)

        return service.transcribe(audio, model_name=model_name, language=language, progress=progress, mode=mode,
                                  on_plan=on_plan)


class SignalsRanker:
    """No second opinion: the transparent signal score is final."""

    name = "signals"

    def active(self, use_llm: bool) -> bool:
        return False

    def rank(self, **_: Any) -> list[LLMMoment] | None:
        return None


class ClaudeRanker:
    """Claude re-ranking of the candidates (existing behaviour; needs ANTHROPIC_API_KEY)."""

    name = "claude"

    def active(self, use_llm: bool) -> bool:
        from app.modules.highlights import llm_client

        return llm_client.llm_active(use_llm)

    def rank(self, *, sentences: list[Sentence], candidates: list[HighlightCandidate], duration: float,
             targets: list[float], max_clips: int, source_title: str | None) -> list[LLMMoment] | None:
        from app.modules.highlights import llm, llm_client

        return llm_client.guarded(
            lambda: llm.fetch_llm_moments(sentences=sentences, candidates=candidates, duration=duration,
                                          targets=targets, max_clips=max_clips, source_title=source_title),
            purpose="highlight ranking")


class ExtractiveMetadata:
    """Metadata extracted from the clip transcript itself (no text is invented)."""

    name = "extractive"

    def active(self, use_llm: bool) -> bool:
        return False      # the extractive writer is the fallback the generator always has

    def generate(self, **_: Any) -> ClipMetadata | None:
        return None


class ClaudeMetadata:
    """Claude-written metadata (existing behaviour; needs ANTHROPIC_API_KEY)."""

    name = "claude"

    def active(self, use_llm: bool) -> bool:
        from app.modules.highlights import llm_client

        return llm_client.llm_active(use_llm)

    def generate(self, *, transcript_text: str, source_title: str | None, category: str | None,
                 hook: str | None) -> ClipMetadata | None:
        from app.modules.highlights import llm_client
        from app.modules.metadata.generator import llm_metadata

        return llm_client.guarded(
            lambda: llm_metadata(transcript_text=transcript_text, source_title=source_title, category=category,
                                 hook=hook),
            purpose="metadata generation")
