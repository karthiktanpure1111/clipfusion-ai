"""Boundary refinement (never cuts words, sentence-aligned, silence-trimmed) and NMS."""

from __future__ import annotations

import random

from app.modules.clipping.boundaries import refine_boundaries, suppress_overlaps, temporal_iou
from app.modules.highlights.sentences import build_sentences
from app.schemas.media import AudioAnalysis, HighlightCandidate, TranscriptData
from tests.test_highlights_detector import build_transcript

LINES = [
    (1.0, "Welcome back to the show everyone."),
    (4.0, "Today we look at three big ideas."),
    (7.5, "The first idea is simple."),
    (15.0, "After a long pause we continue with the second idea."),   # 5+ s of dead air before
    (19.5, "It changes everything you know about editing."),
    (23.0, "And the third idea is the best one."),
    (26.5, "Thanks for watching!"),
]
DURATION = 30.0


def _transcript() -> TranscriptData:
    return build_transcript(LINES)


def _audio() -> AudioAnalysis:
    return AudioAnalysis(silences=[(9.6, 14.9), (28.0, 30.0)])


def _inside_word(t: float, transcript: TranscriptData) -> bool:
    return any(w.start + 1e-3 < t < w.end - 1e-3 for w in transcript.words())


def test_snaps_to_sentence_boundaries() -> None:
    tr = _transcript()
    sentences = build_sentences(tr)
    start, end = refine_boundaries(4.3, 13.0, transcript=tr, audio=_audio(), media_duration=DURATION,
                                   min_len=3.0)
    assert abs(start - (sentences[1].start - 0.15)) < 1e-6   # inside sentence 2 near its start -> include it
    assert abs(end - (sentences[2].end + 0.15)) < 1e-6        # ends after sentence 3 (+ padding)
    assert not _inside_word(start, tr) and not _inside_word(end, tr)


def test_trims_leading_silence_with_padding() -> None:
    tr = _transcript()
    start, end = refine_boundaries(9.8, 25.0, transcript=tr, audio=_audio(), media_duration=DURATION, min_len=3.0)
    first_word = next(w for w in tr.words() if w.start >= 14.9)
    assert abs(start - (first_word.start - 0.15)) < 1e-6
    assert end > start


def test_respects_min_max_and_media_duration() -> None:
    tr = _transcript()
    start, end = refine_boundaries(26.6, 29.0, transcript=tr, audio=_audio(), media_duration=DURATION,
                                   min_len=8.0, max_len=20.0)
    assert end - start >= 8.0 - 1e-6 and 0 <= start and end <= DURATION
    start, end = refine_boundaries(0.0, 30.0, transcript=tr, audio=_audio(), media_duration=DURATION,
                                   min_len=5.0, max_len=12.0)
    assert end - start <= 12.0 + 1e-6
    start, end = refine_boundaries(-5.0, 50.0, transcript=tr, audio=_audio(), media_duration=DURATION)
    assert start >= 0.0 and end <= DURATION


def test_random_windows_never_cut_words() -> None:
    tr = _transcript()
    rng = random.Random(3)
    for _ in range(300):
        a = rng.uniform(0, DURATION)
        b = min(DURATION, a + rng.uniform(1, 25))
        target = rng.choice([None, 10.0, 15.0])
        start, end = refine_boundaries(a, b, transcript=tr, audio=_audio(), media_duration=DURATION,
                                       target=target, min_len=4.0, max_len=20.0)
        assert 0.0 <= start <= end <= DURATION
        assert not _inside_word(start, tr), (a, b, start)
        assert not _inside_word(end, tr), (a, b, end)


def test_without_transcript_uses_audio_silences() -> None:
    empty = TranscriptData(model="none", segments=[])
    start, end = refine_boundaries(10.0, 29.0, transcript=empty, audio=_audio(), media_duration=DURATION,
                                   min_len=5.0)
    assert abs(start - (14.9 - 0.15)) < 1e-6 and abs(end - (28.0 + 0.15)) < 1e-6


def _cand(start: float, end: float, score: float) -> HighlightCandidate:
    return HighlightCandidate(start=start, end=end, score=score)


def test_suppress_overlaps_is_greedy_nms() -> None:
    cands = [_cand(0, 30, 50), _cand(5, 35, 80), _cand(31, 61, 70), _cand(36.5, 66, 65), _cand(100, 130, 10),
             _cand(101, 125, 9)]
    kept = suppress_overlaps(cands, 0.25)
    assert [c.score for c in kept] == [80, 70, 10]
    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            assert temporal_iou(a.start, a.end, b.start, b.end) <= 0.25
    assert [c.score for c in suppress_overlaps(cands, 0.25, limit=2)] == [80, 70]
    assert temporal_iou(0, 10, 5, 15) == 5 / 15 and temporal_iou(0, 1, 2, 3) == 0.0


def test_a_short_clip_half_inside_a_longer_one_is_a_duplicate() -> None:
    long = HighlightCandidate(start=27.8, end=54.6, score=69)
    short = HighlightCandidate(start=20.1, end=35.0, score=57)   # 7 of its 15 s repeat the long clip
    touching = HighlightCandidate(start=51.5, end=70.0, score=50)
    assert temporal_iou(short.start, short.end, long.start, long.end) < 0.25
    assert suppress_overlaps([long, short, touching], 0.25) == [long, touching]
