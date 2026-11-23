"""Optional Claude ranking of highlight moments.

Claude reads the timestamped transcript (``[mm:ss] sentence`` lines, chunked when longer
than ``settings.llm_max_transcript_chars``) plus the strongest signal-based candidates and
returns moments ``{start, end, category, score, reason, hook}`` via structured output.
Only used when ``settings.llm_configured`` and ``options.use_llm``; every failure falls back
to the signal-based ranking (see :func:`app.modules.highlights.llm_client.guarded`).
"""

from __future__ import annotations

import logging
import math
from typing import get_args

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.config import get_settings
from app.modules.analysis.timecode import format_timestamp
from app.modules.highlights import llm_client
from app.modules.highlights.sentences import Sentence
from app.schemas.media import HighlightCandidate, HighlightCategory

log = logging.getLogger(__name__)

CATEGORIES: list[str] = list(get_args(HighlightCategory))
MAX_SIGNAL_CANDIDATES = 20

SYSTEM_PROMPT = (
    "You are an experienced short-form video editor. You pick moments from long videos (streams, "
    "podcasts, interviews, tutorials) that work as stand-alone YouTube Shorts and Instagram Reels.\n"
    "A good moment is self-contained (a viewer needs no earlier context), starts on the first sentence "
    "of the moment and ends right after its payoff (punchline, answer, result, reaction), and has a "
    "strong opening line.\n"
    "Rules:\n"
    "- Use only the transcript you are given. Never invent words, events or context.\n"
    "- start and end are seconds from the beginning of the video, taken from the line timestamps "
    "([12:41] = 761 s). Start at the timestamp of the moment's first line; end at the timestamp of the "
    "first line after the moment (or the video end).\n"
    "- Keep each moment close to one of the requested target durations.\n"
    "- score is your 0-100 estimate of how engaging the moment is as a short. It is an estimate, not a "
    "prediction of views.\n"
    "- reason: one concrete sentence quoting or naming what happens.\n"
    "- hook: the moment's best opening line, quoted from the transcript.\n"
    "- Moments must not overlap each other."
)


class LLMMoment(BaseModel):
    """One moment proposed by Claude (validated, source seconds)."""

    model_config = ConfigDict(extra="ignore")

    start: float
    end: float
    category: HighlightCategory = "other"
    score: float
    reason: str = ""
    hook: str = ""

    @field_validator("score")
    @classmethod
    def _clamp_score(cls, v: float) -> float:
        return min(100.0, max(0.0, float(v)))

    @field_validator("category", mode="before")
    @classmethod
    def _known_category(cls, v: object) -> object:
        return v if v in CATEGORIES else "other"


class LLMResponse(BaseModel):
    moments: list[LLMMoment]


RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "hook": {"type": "string"},
                },
                "required": ["start", "end", "category", "score", "reason", "hook"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}


def transcript_lines(sentences: list[Sentence]) -> list[tuple[float, str]]:
    """``(start, "[mm:ss] text")`` per sentence."""
    return [(s.start, f"[{format_timestamp(s.start)}] {' '.join(s.text.split())}") for s in sentences if s.text.strip()]


def chunk_lines(lines: list[tuple[float, str]], max_chars: int) -> list[list[tuple[float, str]]]:
    """Split transcript lines into consecutive chunks of at most ``max_chars`` characters."""
    chunks: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    size = 0
    for item in lines:
        length = len(item[1]) + 1
        if current and size + length > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        chunks.append(current)
    return chunks


def _candidate_lines(cands: list[HighlightCandidate]) -> str:
    rows = []
    for c in cands[:MAX_SIGNAL_CANDIDATES]:
        why = "; ".join(c.reasons) or "no specific cue"
        rows.append(f"- [{format_timestamp(c.start)}-{format_timestamp(c.end)}] ({c.start:.1f}-{c.end:.1f} s) "
                    f"signal score {c.score:.0f}, {c.category}: {why}")
    return "\n".join(rows) or "- (none)"


def _prompt(lines: list[tuple[float, str]], cands: list[HighlightCandidate], *, duration: float,
            targets: list[float], wanted: int, source_title: str | None, part: tuple[int, int]) -> str:
    header = [
        f"Video title: {source_title or '(unknown)'}",
        f"Video duration: {format_timestamp(duration)} ({duration:.0f} s)",
        f"Target clip durations (seconds): {', '.join(f'{t:g}' for t in targets)}",
        f"Return up to {wanted} moments, best first.",
    ]
    if part[1] > 1:
        header.append(f"This is transcript part {part[0]} of {part[1]}; only pick moments inside this part.")
    return "\n".join(header + [
        "",
        "Candidates found by audio/visual/transcript signal analysis (confirm, adjust or ignore them):",
        _candidate_lines(cands),
        "",
        "Transcript (one line per sentence, [mm:ss] = start time):",
        "\n".join(text for _, text in lines),
    ])


def _plausible(m: LLMMoment, duration: float, targets: list[float]) -> bool:
    length = m.end - m.start
    return 0 <= m.start < m.end <= duration + 1.0 and 3.0 <= length <= 2.0 * max(targets)


def fetch_llm_moments(*, sentences: list[Sentence], candidates: list[HighlightCandidate], duration: float,
                      targets: list[float], max_clips: int, source_title: str | None) -> list[LLMMoment]:
    """Ask Claude for moments; raises on API/refusal/validation errors (callers use ``guarded``)."""
    lines = transcript_lines(sentences)
    if not lines:
        return []
    chunks = chunk_lines(lines, max(2000, get_settings().llm_max_transcript_chars))
    wanted_total = max(max_clips * 2, 5)
    moments: list[LLMMoment] = []
    for n, chunk in enumerate(chunks, start=1):
        lo = chunk[0][0]
        hi = chunks[n][0][0] if n < len(chunks) else duration
        in_chunk = [c for c in candidates if lo <= c.start < hi]
        wanted = max(3, math.ceil(wanted_total / len(chunks)))
        prompt = _prompt(chunk, in_chunk, duration=duration, targets=targets, wanted=wanted,
                         source_title=source_title, part=(n, len(chunks)))
        data = llm_client.request_structured(system=SYSTEM_PROMPT, prompt=prompt, schema=RESPONSE_SCHEMA)
        parsed = LLMResponse.model_validate(data)
        moments.extend(m for m in parsed.moments if _plausible(m, duration, targets))
    log.info("Claude proposed %d usable moments (%d transcript chunks)", len(moments), len(chunks))
    return moments
