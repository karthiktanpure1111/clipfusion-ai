"""Caption building and editing (build/split/merge/retext/normalize)."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailed
from app.modules.subtitles.captions import (
    build_captions,
    merge_captions,
    normalize_captions,
    retext_caption,
    split_caption,
)
from app.schemas.media import TranscriptWord
from app.schemas.subtitles import Caption, CaptionWord, SubtitleStyle


def _words(spec: list[tuple[str, float, float]]) -> list[TranscriptWord]:
    return [TranscriptWord(word=w, start=s, end=e) for w, s, e in spec]


def _even_words(text: str, start: float, step: float = 0.3, dur: float = 0.25) -> list[TranscriptWord]:
    return [TranscriptWord(word=" " + w, start=start + i * step, end=start + i * step + dur)
            for i, w in enumerate(text.split())]


def test_build_captions_chunks_by_word_count_and_makes_times_clip_relative() -> None:
    words = _even_words("one two three four five six seven eight nine", start=10.0)
    caps = build_captions(words, clip_start=10.0, clip_end=14.0, style=SubtitleStyle(words_per_caption=4))
    assert [c.text for c in caps] == ["one two three four", "five six seven eight", "nine"]
    assert caps[0].start == pytest.approx(0.0)
    assert caps[0].words[1].start == pytest.approx(0.3)
    assert all(not w.text.startswith(" ") for c in caps for w in c.words)  # ASR spaces stripped
    # short gaps between captions are bridged; the last caption is held 0.2 s
    assert caps[0].end == pytest.approx(caps[1].start)
    assert caps[-1].end == pytest.approx(2.4 + 0.25 + 0.2)


def test_build_captions_splits_on_sentence_end_pause_and_duration() -> None:
    words = _words([(" Hello", 0.0, 0.3), (" there.", 0.35, 0.7), (" So", 0.8, 1.0), (" anyway", 1.05, 1.4),
                    (" after", 2.3, 2.5), (" pause", 2.55, 2.9)])
    caps = build_captions(words, clip_start=0.0, clip_end=5.0, style=SubtitleStyle(words_per_caption=8))
    assert [c.text for c in caps] == ["Hello there.", "So anyway", "after pause"]
    long_words = _even_words("a b c d e f g h", start=0.0, step=0.5, dur=0.45)
    caps = build_captions(long_words, clip_start=0.0, clip_end=5.0,
                          style=SubtitleStyle(words_per_caption=12, max_caption_duration=1.2))
    assert all(c.words[-1].end - c.words[0].start <= 1.2 + 1e-9 for c in caps)


def test_build_captions_clamps_words_to_clip() -> None:
    words = _words([(" early", 4.6, 5.2), (" inside", 5.3, 5.8), (" late", 7.8, 8.6), (" outside", 9.0, 9.5)])
    caps = build_captions(words, clip_start=5.0, clip_end=8.0, style=SubtitleStyle())
    flat = [w for c in caps for w in c.words]
    assert [w.text for w in flat] == ["early", "inside", "late"]
    assert flat[0].start == 0.0 and flat[-1].end == pytest.approx(3.0)
    assert all(0.0 <= c.start < c.end <= 3.0 for c in caps)
    assert build_captions(words, clip_start=5.0, clip_end=5.0, style=SubtitleStyle()) == []


def _cap(words: list[tuple[str, float, float]], **kw) -> Caption:
    ws = [CaptionWord(text=t, start=s, end=e) for t, s, e in words]
    return Caption(start=kw.pop("start", ws[0].start), end=kw.pop("end", ws[-1].end), words=ws, **kw)


def test_split_and_merge_roundtrip() -> None:
    c = _cap([("a", 0.0, 0.4), ("b", 0.5, 0.9), ("c", 1.0, 1.4)], end=1.6)
    c.words[1].color = "#00FF00"
    a, b = split_caption(c, 1)
    assert a.id == c.id and b.id != c.id
    assert a.text == "a" and b.text == "b c"
    assert a.end == pytest.approx(0.5) and b.start == pytest.approx(0.5) and b.end == pytest.approx(1.6)
    assert b.words[0].color == "#00FF00"
    merged = merge_captions(b, a)  # order independent
    assert merged.text == "a b c" and merged.id == c.id
    assert (merged.start, merged.end) == (pytest.approx(0.0), pytest.approx(1.6))
    with pytest.raises(ValidationFailed):
        split_caption(c, 0)
    with pytest.raises(ValidationFailed):
        split_caption(c, 3)


def test_retext_keeps_timing_and_redistributes_by_length() -> None:
    c = _cap([("helo", 1.0, 1.4), ("wrld", 1.5, 2.0)])
    c.words[0].emphasis = True
    same = retext_caption(c, "Hello, world!")
    assert [w.text for w in same.words] == ["Hello,", "world!"]
    assert same.words[1].start == 1.5 and same.words[0].emphasis  # same count: ASR timing + styling kept
    re = retext_caption(c, "a bbb cccc")
    assert (re.start, re.end) == (1.0, 2.0)
    durs = [w.end - w.start for w in re.words]
    assert durs[1] == pytest.approx(3 * durs[0], rel=1e-2) and durs[2] == pytest.approx(4 * durs[0], rel=1e-2)
    assert re.words[0].start == 1.0 and re.words[-1].end == pytest.approx(2.0)
    with pytest.raises(ValidationFailed):
        retext_caption(c, "   ")


def test_normalize_sorts_clamps_fixes_overlaps_and_drops_empties() -> None:
    a = _cap([("a", 0.0, 1.0)], end=1.5)
    b = _cap([("b", 1.2, 1.8)], end=2.0)
    c = _cap([("c", 2.5, 3.5)], end=4.0)
    late = _cap([("x", 9.0, 9.5)])
    empty = Caption(start=0.5, end=0.9, words=[])
    same_start = _cap([("dup", 0.0, 0.2)], end=0.3)
    out = normalize_captions([c, late, b, empty, a, same_start], duration=3.8)
    assert [x.text for x in out] == ["a", "dup", "b", "c"] or [x.text for x in out] == ["dup", "a", "b", "c"]
    for prev, nxt in zip(out, out[1:], strict=False):
        assert prev.end <= nxt.start + 1e-9
    assert out[-1].end == pytest.approx(3.8)
    for cap in out:
        assert all(cap.start - 1e-9 <= w.start <= w.end <= cap.end + 1e-9 for w in cap.words)
