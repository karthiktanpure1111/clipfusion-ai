"""Raw per-window signals for highlight detection.

:class:`SignalContext` pre-computes prefix sums over the transcript sentences, the 10 Hz
loudness envelope, silences, scene cuts and motion so the raw signals of a candidate window
(:func:`raw_signals`) cost O(log n) plus one partial sort of the window's loudness values.
All signals are real measurements of the source; per-video normalisation happens in
:mod:`app.modules.highlights.scoring`.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass

import numpy as np

from app.modules.highlights.context import ContextSignals, build_context, window_context
from app.modules.highlights.lexicon import (
    LEADING_CONJUNCTIONS,
    all_groups,
    count_numbers,
    find_cues,
    first_word,
)
from app.modules.highlights.sentences import Sentence, SentenceIndex, build_sentences
from app.schemas.media import (
    AudioAnalysis,
    MediaInfo,
    RetentionPoint,
    SceneCut,
    TranscriptData,
    VisualActivity,
)

LEXICAL_GROUPS: tuple[str, ...] = tuple(all_groups())
QA_MAX_GAP_S = 5.0
_YOU_RE = re.compile(r"\byou\b", re.IGNORECASE)


@dataclass
class Window:
    """A candidate clip window (source seconds) for a requested target duration."""

    start: float
    end: float
    target: float

    @property
    def duration(self) -> float:
        return max(1e-6, self.end - self.start)


def _prefix(values: list[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    return np.concatenate([[0.0], np.cumsum(arr)]) if arr.size else np.zeros(1)


def hook_score(s: Sentence) -> float:
    """0..1 strength of ``s`` as an opening line (question, exclamation, hook phrases, brevity)."""
    text = s.text
    score = 0.0
    if s.is_question:
        score += 0.35
    if s.is_exclamation:
        score += 0.25
    if find_cues(text, "hook"):
        score += 0.3
    if find_cues(text, "strong_statement") or find_cues(text, "surprise"):
        score += 0.2
    if count_numbers(text):
        score += 0.1
    if _YOU_RE.search(text):
        score += 0.1
    wc = s.word_count
    if 3 <= wc <= 16:
        score += 0.15
    elif wc > 30:
        score -= 0.15
    if first_word(text) in LEADING_CONJUNCTIONS:
        score -= 0.3
    return min(1.0, max(0.0, score))


class SignalContext:
    """Pre-computed, query-efficient view of all analysis signals of one source."""

    def __init__(self, *, transcript: TranscriptData, audio: AudioAnalysis, scenes: list[SceneCut],
                 visual: VisualActivity, media: MediaInfo,
                 retention: list[RetentionPoint] | None = None) -> None:
        self.audio = audio
        self.transcript = transcript
        self.duration = float(media.duration or transcript.duration or len(audio.rms_db) / (audio.sample_hz or 10))
        self.sentences = build_sentences(transcript)
        self.index = SentenceIndex(self.sentences)
        self.word_starts = sorted(w.start for w in transcript.words())
        self.word_count_total = len(self.word_starts) or sum(s.word_count for s in self.sentences)
        self._init_sentences()
        self._init_audio(audio)
        self.cuts = sorted(c.time for c in scenes)
        self.motion = np.asarray(visual.motion, dtype=np.float64)
        self.motion_hz = float(visual.sample_hz or 1.0)
        self.motion_prefix = _prefix(self.motion)
        self.has_visual = self.motion.size > 1 or bool(self.cuts)
        self.retention = sorted(retention, key=lambda p: p.ratio) if retention else None

    # ----------------------------------------------------------- setup
    def _init_sentences(self) -> None:
        ss = self.sentences
        n = len(ss)
        self.sent_dur_prefix = _prefix([s.end - s.start for s in ss])
        self.lex_prefix: dict[str, np.ndarray] = {}
        self.lex_hits: list[dict[str, list[str]]] = [{} for _ in ss]
        for group in LEXICAL_GROUPS:
            counts = []
            for i, s in enumerate(ss):
                hits = find_cues(s.text, group)
                if hits:
                    self.lex_hits[i][group] = hits
                counts.append(len(hits))
            self.lex_prefix[group] = _prefix(counts)
        self.exclam_prefix = _prefix([1.0 if s.is_exclamation else 0.0 for s in ss])
        self.numbers_prefix = _prefix([count_numbers(s.text) for s in ss])
        self.qa_flags = [
            1.0 if (s.is_question and i + 1 < n and not ss[i + 1].is_question
                    and ss[i + 1].start - s.end <= QA_MAX_GAP_S and ss[i + 1].word_count >= 3) else 0.0
            for i, s in enumerate(ss)
        ]
        self.qa_prefix = _prefix(self.qa_flags)
        self.hooks = [hook_score(s) for s in ss]
        self.context: ContextSignals = build_context(ss)

    def _init_audio(self, audio: AudioAnalysis) -> None:
        self.db = np.asarray(audio.rms_db, dtype=np.float64)
        self.audio_hz = float(audio.sample_hz or 10.0)
        self.has_audio = self.db.size > 0
        self.median_db = float(np.median(self.db)) if self.has_audio else 0.0
        self.peaks = sorted(audio.peak_times)
        mask = np.zeros(self.db.size, dtype=np.float64)
        for a, b in audio.silences:
            mask[max(0, int(a * self.audio_hz)):max(0, int(math.ceil(b * self.audio_hz)))] = 1.0
        self.silence_prefix = _prefix(mask)
        self.silence_starts = [a for a, _ in sorted(audio.silences)]
        self.silences = sorted(audio.silences)

    # --------------------------------------------------------- queries
    def sentence_range(self, start: float, end: float) -> tuple[int, int] | None:
        """Inclusive index range of the sentences lying entirely inside ``[start, end]``."""
        if not self.sentences:
            return None
        first = self.index.next_starting_at_or_after(start - 1e-6)
        if first is None:
            return None
        last = first - 1
        while last + 1 < len(self.sentences) and self.sentences[last + 1].end <= end + 1e-6:
            last += 1
        return (first, last) if last >= first else None

    def audio_slice(self, start: float, end: float) -> tuple[int, int]:
        a = min(self.db.size, max(0, int(start * self.audio_hz)))
        b = min(self.db.size, max(a, int(math.ceil(end * self.audio_hz))))
        return a, b

    def peaks_in(self, start: float, end: float) -> list[float]:
        return self.peaks[bisect.bisect_left(self.peaks, start):bisect.bisect_right(self.peaks, end)]

    def cuts_in(self, start: float, end: float, margin: float = 0.2) -> list[float]:
        return self.cuts[bisect.bisect_right(self.cuts, start + margin):bisect.bisect_left(self.cuts, end - margin)]

    def db_at(self, t: float) -> float:
        if not self.has_audio:
            return 0.0
        i = min(self.db.size - 1, max(0, int(t * self.audio_hz)))
        return float(self.db[max(0, i - 1):i + 2].max())

    def in_silence(self, t: float) -> bool:
        i = bisect.bisect_right(self.silence_starts, t) - 1
        return i >= 0 and self.silences[i][0] <= t <= self.silences[i][1]

    def speech_mode(self) -> bool:
        """True when the source has enough speech for sentence-based windows."""
        if self.word_count_total < 20 or not self.sentences:
            return False
        covered = float(self.sent_dur_prefix[-1])
        return covered >= 0.1 * max(self.duration, 1e-6) or covered >= 60.0

    def completeness(self, w: Window, rng: tuple[int, int] | None) -> float:
        """0..1: does the window start/end on natural boundaries?"""
        cut_start = self.index.containing(w.start)
        cut_end = self.index.containing(w.end)
        inside_start = cut_start is not None and w.start > self.sentences[cut_start].start + 0.05
        inside_end = cut_end is not None and w.end < self.sentences[cut_end].end - 0.05
        if rng is None:
            score = 0.5
            score += 0.25 if (self.in_silence(w.start) or self.cuts_in(w.start - 1.0, w.start + 1.0, 0)) else 0
            score += 0.25 if (self.in_silence(w.end) or self.cuts_in(w.end - 1.0, w.end + 1.0, 0)) else 0
        else:
            _, last = rng
            score = 1.0     # leading conjunctions / context dependency: see the "standalone" feature
            if not self.sentences[last].terminal:
                score -= 0.2
        score -= 0.3 * inside_start + 0.3 * inside_end
        return min(1.0, max(0.0, score))

    def retention_mean(self, start: float, end: float) -> float | None:
        """Mean REAL audience watch ratio (YouTube Analytics) over the window, if provided."""
        if not self.retention or self.duration <= 0:
            return None
        r0, r1 = start / self.duration, end / self.duration
        inside = [p.audience_watch_ratio for p in self.retention if r0 <= p.ratio <= r1]
        if inside:
            return float(np.mean(inside))
        ratios = [p.ratio for p in self.retention]
        values = [p.audience_watch_ratio for p in self.retention]
        return float(np.interp((r0 + r1) / 2, ratios, values))


def raw_signals(ctx: SignalContext, w: Window) -> dict[str, float]:
    """Un-normalised measurements of window ``w`` (counts, rates, dB, fractions)."""
    d = w.duration
    out: dict[str, float] = {"duration": d}
    if ctx.has_audio:
        a, b = ctx.audio_slice(w.start, w.end)
        seg = ctx.db[a:b]
        if seg.size:
            k = max(1, int(math.ceil(0.2 * seg.size)))
            out["loud_top20"] = float(np.partition(seg, seg.size - k)[seg.size - k:].mean())
            out["silence_frac"] = float(ctx.silence_prefix[b] - ctx.silence_prefix[a]) / seg.size
        else:
            out["loud_top20"], out["silence_frac"] = ctx.median_db, 1.0
        out["peaks"] = float(len(ctx.peaks_in(w.start, w.end)))
    words = bisect.bisect_left(ctx.word_starts, w.end) - bisect.bisect_left(ctx.word_starts, w.start)
    rng = ctx.sentence_range(w.start, w.end)
    out["words_per_s"] = words / d
    if rng is not None:
        first, last = rng
        out["speech_frac"] = min(1.0, float(ctx.sent_dur_prefix[last + 1] - ctx.sent_dur_prefix[first]) / d)
        if not ctx.word_starts:
            out["words_per_s"] = sum(s.word_count for s in ctx.sentences[first:last + 1]) / d
        for group in LEXICAL_GROUPS:
            out[f"lex_{group}"] = float(ctx.lex_prefix[group][last + 1] - ctx.lex_prefix[group][first])
        out["exclam"] = float(ctx.exclam_prefix[last + 1] - ctx.exclam_prefix[first])
        out["numbers"] = float(ctx.numbers_prefix[last + 1] - ctx.numbers_prefix[first])
        out["qa"] = float(ctx.qa_prefix[last] - ctx.qa_prefix[first])  # question must be answered inside
        out["hook"] = ctx.hooks[first] * (1.0 - 0.5 * float(ctx.context.dependency[first]))
        out.update(window_context(ctx.context, first, last))
        gaps = [ctx.sentences[i + 1].start - ctx.sentences[i].end for i in range(first, last)]
        out["max_gap"] = max(gaps) if gaps else 0.0
    else:
        out["speech_frac"] = 0.0
        for group in LEXICAL_GROUPS:
            out[f"lex_{group}"] = 0.0
        out["exclam"] = out["numbers"] = out["qa"] = out["hook"] = out["max_gap"] = 0.0
    out["completeness"] = ctx.completeness(w, rng)
    out["cuts"] = float(len(ctx.cuts_in(w.start, w.end)))
    if ctx.motion.size:
        a = min(ctx.motion.size, max(0, int(w.start * ctx.motion_hz)))
        b = min(ctx.motion.size, max(a + 1, int(math.ceil(w.end * ctx.motion_hz))))
        out["motion"] = float(ctx.motion_prefix[b] - ctx.motion_prefix[a]) / max(1, b - a)
    ret = ctx.retention_mean(w.start, w.end)
    if ret is not None:
        out["retention"] = ret
    return out
