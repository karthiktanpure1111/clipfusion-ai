"""Clip boundary refinement and overlap suppression.

``refine_boundaries`` turns a rough window into a natural clip: it snaps to sentence
starts/ends, trims leading/trailing dead air (keeping ``PAD`` seconds), respects length
limits and the media duration, and never leaves a boundary inside a word.

``suppress_overlaps`` is a greedy temporal non-maximum suppression.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence

from app.modules.highlights.sentences import Sentence, SentenceIndex, build_sentences
from app.schemas.media import AudioAnalysis, HighlightCandidate, TranscriptData, TranscriptWord

PAD = 0.15                 # padding kept around speech when trimming silence
WORD_EPS = 1e-3
DUPLICATE_START_S = 2.0


# ------------------------------------------------------------------- utilities
def temporal_iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Intersection-over-union of two time intervals (0 when disjoint or degenerate)."""
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def overlap_of_shorter(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Fraction of the shorter interval that the other one repeats (IoU hides this when the
    lengths differ: a 15 s clip half contained in a 30 s clip has an IoU of only 0.2)."""
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    shorter = min(a_end - a_start, b_end - b_start)
    return inter / shorter if shorter > 0 else 0.0


def _closest(options: list[float], start: float, end: float, *, moving_start: bool,
             target: float | None, max_len: float) -> float:
    """Pick the boundary option giving a length closest to ``target`` (first option wins ties)."""
    valid = [o for o in options if (end - o if moving_start else o - start) > 0]
    if not valid:
        return options[0]
    if target is None:
        return valid[0]

    def cost(o: float) -> float:
        length = end - o if moving_start else o - start
        return abs(length - target) + (1000.0 if length > max_len else 0.0)

    return min(valid, key=cost)


def _snap_start(t: float, end: float, ss: list[Sentence], idx: SentenceIndex,
                target: float | None, max_len: float) -> float:
    i = idx.containing(t)
    if i is not None:
        s = ss[i]
        if t <= s.start + WORD_EPS:
            return s.start
        include, exclude = s.start, (ss[i + 1].start if i + 1 < len(ss) and ss[i + 1].start < end else None)
        if exclude is None:
            return include
        half_rule = include if t - s.start <= 0.5 * (s.end - s.start) else exclude
        if target is None:
            return half_rule
        return _closest([half_rule, include if half_rule == exclude else exclude], t, end,
                        moving_start=True, target=target, max_len=max_len)
    j = idx.next_starting_at_or_after(t)
    return ss[j].start if j is not None and ss[j].start < end else t


def _snap_end(t: float, start: float, ss: list[Sentence], idx: SentenceIndex,
              target: float | None, max_len: float) -> float:
    i = idx.containing(t)
    if i is not None:
        s = ss[i]
        if t >= s.end - WORD_EPS:
            return s.end
        include, exclude = s.end, (ss[i - 1].end if i > 0 and ss[i - 1].end > start else None)
        if exclude is None:
            return include
        half_rule = include if s.end - t <= 0.5 * (s.end - s.start) else exclude
        if target is None:
            return half_rule
        return _closest([half_rule, include if half_rule == exclude else exclude], start, t,
                        moving_start=False, target=target, max_len=max_len)
    j = idx.last_ending_at_or_before(t)
    return ss[j].end if j is not None and ss[j].end > start else t


def _fit_sentences(start: float, end: float, ss: list[Sentence], idx: SentenceIndex,
                   min_len: float, max_len: float) -> tuple[float, float]:
    """Add/drop whole sentences until ``min_len <= length <= max_len`` (when possible)."""
    while end - start < min_len:
        j = idx.next_starting_at_or_after(end - WORD_EPS)
        if j is not None and ss[j].start >= end - WORD_EPS and ss[j].end - start <= max_len:
            end = ss[j].end
            continue
        k = idx.last_ending_at_or_before(start + WORD_EPS)
        if k is not None and end - ss[k].start <= max_len:
            start = ss[k].start
            continue
        break
    while end - start > max_len:
        k = idx.last_ending_at_or_before(end - 0.01)
        if k is None or ss[k].end <= start + min_len:
            break
        end = ss[k].end
    return start, end


def _trim_silence(start: float, end: float, words: list[TranscriptWord], starts: list[float],
                  audio: AudioAnalysis, duration: float, max_len: float) -> tuple[float, float]:
    """Keep exactly ``PAD`` seconds before the first / after the last word (never reaching into
    a neighbouring word); without word timings, trim energy-based silences instead."""
    lo = bisect.bisect_left(starts, start - 0.01)
    inside = [w for w in words[lo:bisect.bisect_right(starts, end)] if w.end <= end + 0.01]
    if inside:
        first = lo + next(k for k, w in enumerate(words[lo:]) if w is inside[0])
        last = first + len(inside) - 1
        prev_end = words[first - 1].end if first > 0 else 0.0
        next_start = words[last + 1].start if last + 1 < len(words) else duration
        core_start, core_end = inside[0].start, inside[-1].end
        start = max(core_start - PAD, prev_end, 0.0)
        end = min(core_end + PAD, max(next_start, core_end), duration)
        excess = (end - start) - max_len
        if excess > 0:  # give up padding before cutting speech
            take = min(excess, end - core_end)
            end, excess = end - take, excess - take
            start += max(0.0, min(excess, core_start - start))
        return start, end
    for a, b in audio.silences:
        if a <= start < b < end:
            start = max(start, b - PAD)
        if start < a < end <= b:
            end = min(end, a + PAD)
    return start, end


def _enforce_lengths(start: float, end: float, duration: float, min_len: float,
                     max_len: float) -> tuple[float, float]:
    if end - start > max_len:
        end = start + max_len
    if end - start < min_len:
        end = min(duration, start + min_len)
        start = max(0.0, min(start, end - min_len))
    return start, end


def _word_at(words: list[TranscriptWord], starts: list[float], t: float) -> int | None:
    i = bisect.bisect_right(starts, t) - 1
    if 0 <= i < len(words) and words[i].start + WORD_EPS < t < words[i].end - WORD_EPS:
        return i
    return None


def _avoid_word_cuts(start: float, end: float, words: list[TranscriptWord], starts: list[float],
                     duration: float, max_len: float) -> tuple[float, float]:
    i = _word_at(words, starts, start)
    if i is not None:
        prev_end = words[i - 1].end if i > 0 else 0.0
        start = max(0.0, min(words[i].start, max(prev_end, words[i].start - 0.05)))
    j = _word_at(words, starts, end)
    if j is not None:
        w = words[j]
        next_start = words[j + 1].start if j + 1 < len(words) else duration
        extended = min(duration, max(w.end, min(next_start, w.end + 0.05)))
        if extended - start <= max_len:
            end = extended
        else:
            prev_end = words[j - 1].end if j > 0 else start
            end = max(start, min(w.start, max(prev_end, w.start - 0.05)))
    return start, end


# ------------------------------------------------------------------- public
def refine_boundaries(start: float, end: float, *, transcript: TranscriptData, audio: AudioAnalysis,
                      media_duration: float, target: float | None = None,
                      min_len: float = 8.0, max_len: float = 180.0) -> tuple[float, float]:
    """Refine a candidate window ``[start, end]`` (source seconds) into natural clip boundaries.

    Snaps the start to a sentence start and the end to a sentence end (choosing the option
    closest to ``target`` when given), adds/drops whole sentences to respect
    ``min_len``/``max_len``, trims leading/trailing silence leaving 0.15 s of padding, clamps
    to ``[0, media_duration]`` and finally guarantees neither boundary falls inside a word."""
    duration = float(media_duration) if media_duration and media_duration > 0 else max(start, end)
    start, end = sorted((min(max(0.0, start), duration), min(max(0.0, end), duration)))
    min_len = min(min_len, duration)
    max_len = max(min_len, max_len)
    words = sorted(transcript.words(), key=lambda w: w.start)
    starts = [w.start for w in words]
    ss = build_sentences(transcript)
    if ss:
        idx = SentenceIndex(ss)
        new_start = _snap_start(start, end, ss, idx, target, max_len)
        new_end = _snap_end(end, new_start, ss, idx, target, max_len)
        if new_end > new_start:
            start, end = new_start, new_end
        start, end = _fit_sentences(start, end, ss, idx, min_len, max_len)
    start, end = _trim_silence(start, end, words, starts, audio, duration, max_len)
    start, end = _enforce_lengths(max(0.0, start), min(duration, end), duration, min_len, max_len)
    start, end = _avoid_word_cuts(start, end, words, starts, duration, max_len)
    return round(start, 3), round(max(start, end), 3)


def suppress_overlaps(cands: Sequence[HighlightCandidate], max_iou: float, *,
                      limit: int | None = None) -> list[HighlightCandidate]:
    """Greedy NMS: keep candidates by descending score, dropping any that shares more than
    ``max_iou`` of its own (or the kept clip's) duration with an already kept one, or whose start
    lies within 2 s of a kept start (duplicate). Returns the survivors sorted by score (desc), at most ``limit`` of them."""
    kept: list[HighlightCandidate] = []
    for c in sorted(cands, key=lambda c: (-c.score, c.start, c.end)):
        if any(abs(c.start - k.start) < DUPLICATE_START_S or overlap_of_shorter(c.start, c.end, k.start, k.end) > max_iou
               for k in kept):
            continue
        kept.append(c)
        if limit is not None and len(kept) >= limit:
            break
    return kept
