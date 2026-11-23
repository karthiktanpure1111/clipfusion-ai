"""Sentence units built from word-level transcripts.

A sentence ends at a word with terminal punctuation (``. ! ?``) or before a long pause:
``PAUSE_S`` normally, ``PAUSE_PUNCTUATED_S`` when the transcript is reliably punctuated (then a
missing full stop means the sentence goes on), and ``PAUSE_DANGLING_S`` after a word no sentence
ends on ("no way I | cannot believe"). Very long unpunctuated runs (common in gaming streams) are split at the
comma closest to their middle, else at their largest internal pause, so every target clip
duration can be assembled from whole units. Segments without word timings become one unit.
"""

from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass, field

from app.modules.transcription.cleanup import ends_sentence, is_dangling
from app.schemas.media import TranscriptData, TranscriptWord

PAUSE_S = 0.7
PAUSE_PUNCTUATED_S = 1.4
PAUSE_DANGLING_S = 2.5
WORDS_PER_SENTENCE_PUNCTUATED = 25   # at least one sentence end per this many words = punctuated
MAX_SENTENCE_S = 15.0


@dataclass
class Sentence:
    """A transcript sentence (source seconds)."""

    start: float
    end: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)
    terminal: bool = False          # ended by punctuation (vs. by a pause / split)

    @property
    def word_count(self) -> int:
        return len(self.words) if self.words else len(self.text.split())

    @property
    def is_question(self) -> bool:
        return self.text.rstrip().rstrip("\"')]").endswith("?")

    @property
    def is_exclamation(self) -> bool:
        return self.text.rstrip().rstrip("\"')]").endswith("!")


def _join(words: list[TranscriptWord]) -> str:
    return " ".join(w.word.strip() for w in words if w.word.strip())


def _make(words: list[TranscriptWord]) -> Sentence:
    return Sentence(start=words[0].start, end=max(w.end for w in words), text=_join(words), words=list(words),
                    terminal=ends_sentence(words[-1].word))


def _split_long(s: Sentence, max_len: float) -> list[Sentence]:
    if s.end - s.start <= max_len or len(s.words) < 2:
        return [s]
    mid = (s.start + s.end) / 2
    commas = [i for i, w in enumerate(s.words[:-1]) if w.word.strip().endswith((",", ";", ":"))]
    if commas:
        cut = min(commas, key=lambda i: abs(s.words[i].end - mid))
    else:
        cut = max(range(len(s.words) - 1), key=lambda i: (s.words[i + 1].start - s.words[i].end,
                                                         -abs(s.words[i].end - mid)))
    left, right = _make(s.words[:cut + 1]), _make(s.words[cut + 1:])
    right.terminal = s.terminal
    return _split_long(left, max_len) + _split_long(right, max_len)


def _default_pause(transcript: TranscriptData) -> float:
    words = transcript.words()
    ends = sum(ends_sentence(w.word) for w in words)
    return PAUSE_PUNCTUATED_S if ends >= 3 and len(words) <= ends * WORDS_PER_SENTENCE_PUNCTUATED else PAUSE_S


def build_sentences(transcript: TranscriptData, *, pause: float | None = None,
                    max_len: float = MAX_SENTENCE_S) -> list[Sentence]:
    """Split ``transcript`` into time-ordered :class:`Sentence` units."""
    if pause is None:
        pause = _default_pause(transcript)
    english = (transcript.language or "en").lower().startswith("en")
    out: list[Sentence] = []
    current: list[TranscriptWord] = []

    def flush() -> None:
        if current:
            out.extend(_split_long(_make(current), max_len))
            current.clear()

    for seg in transcript.segments:
        if not seg.words:
            flush()
            if seg.text.strip():
                out.append(Sentence(start=seg.start, end=max(seg.end, seg.start), text=seg.text.strip(),
                                    terminal=ends_sentence(seg.text)))
            continue
        for w in seg.words:
            if not w.word.strip():
                continue
            if current:
                dangling = english and is_dangling(current[-1].word)
                if w.start - current[-1].end > (max(pause, PAUSE_DANGLING_S) if dangling else pause):
                    flush()
            current.append(w)
            if ends_sentence(w.word):
                flush()
    flush()
    out.sort(key=lambda s: (s.start, s.end))
    return out


class SentenceIndex:
    """Fast lookups over a sorted sentence list (bisect on starts/ends)."""

    def __init__(self, sentences: list[Sentence]) -> None:
        self.sentences = sentences
        self.starts = [s.start for s in sentences]
        self.ends = list(itertools.accumulate((s.end for s in sentences), max))  # running max: bisectable

    def containing(self, t: float) -> int | None:
        """Index of the sentence whose span contains ``t`` (None when ``t`` is in a gap)."""
        i = bisect.bisect_right(self.starts, t) - 1
        if 0 <= i < len(self.sentences) and self.sentences[i].start <= t <= self.sentences[i].end:
            return i
        return None

    def next_starting_at_or_after(self, t: float) -> int | None:
        i = bisect.bisect_left(self.starts, t)
        return i if i < len(self.sentences) else None

    def last_ending_at_or_before(self, t: float) -> int | None:
        i = bisect.bisect_right(self.ends, t) - 1
        return i if i >= 0 else None
