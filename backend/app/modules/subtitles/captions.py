"""Caption building and editing (pure functions over :class:`~app.schemas.subtitles.Caption`).

Captions use clip-relative seconds. :func:`build_captions` turns ASR words (source time)
into readable chunks; the editing helpers back the Subtitle Studio operations (split,
merge, re-text) and :func:`normalize_captions` is applied before anything is stored or
rendered so captions are sorted, inside the clip and never overlap.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from app.core.errors import ValidationFailed
from app.schemas.media import TranscriptWord
from app.schemas.subtitles import Caption, CaptionWord, SubtitleStyle

PAUSE_SPLIT_S = 0.6        # a silence longer than this starts a new caption
JOIN_GAP_S = 0.35          # shorter gaps between captions are bridged (no flicker)
TAIL_HOLD_S = 0.2          # the last caption stays this long after its last word
MIN_CAPTION_S = 0.05       # matches the Caption.end validator
_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*$")
_WORD_CORE = re.compile(r"[\W_]+", re.UNICODE)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text))


def _clip_words(words: Iterable[TranscriptWord], clip_start: float, duration: float) -> list[CaptionWord]:
    """Source-time ASR words -> clip-relative CaptionWords inside the clip (text stripped)."""
    out: list[CaptionWord] = []
    for w in sorted(words, key=lambda w: w.start):
        text = w.word.strip()
        if not text or w.end <= clip_start or w.start >= clip_start + duration:
            continue
        s = _clamp(w.start - clip_start, 0.0, duration)
        e = _clamp(max(w.end, w.start) - clip_start, s, duration)
        if out and s < out[-1].start:  # keep monotonic word starts
            s = out[-1].start
            e = max(e, s)
        out.append(CaptionWord(text=text[:80], start=round(s, 3), end=round(e, 3)))
    return out


def _chunk(words: list[CaptionWord], style: SubtitleStyle) -> list[list[CaptionWord]]:
    chunks: list[list[CaptionWord]] = []
    cur: list[CaptionWord] = []
    for w in words:
        if cur:
            too_many = len(cur) >= style.words_per_caption
            too_long = w.end - cur[0].start > style.max_caption_duration
            pause = w.start - cur[-1].end > PAUSE_SPLIT_S
            if too_many or too_long or pause or _ends_sentence(cur[-1].text):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_captions(words: list[TranscriptWord], *, clip_start: float, clip_end: float,
                   style: SubtitleStyle) -> list[Caption]:
    """Group source-time ASR ``words`` into clip-relative captions.

    A new caption starts when the current one holds ``style.words_per_caption`` words, would
    last longer than ``style.max_caption_duration``, after sentence punctuation, or after a
    pause longer than 0.6 s. Gaps shorter than 0.35 s between captions are bridged so text
    does not flicker; the last caption is held for 0.2 s (inside the clip)."""
    duration = max(0.0, clip_end - clip_start)
    if duration <= 0:
        return []
    chunks = _chunk(_clip_words(words, clip_start, duration), style)
    captions: list[Caption] = []
    for i, chunk in enumerate(chunks):
        start = chunk[0].start
        end = max(chunk[-1].end, start + MIN_CAPTION_S)
        if i + 1 < len(chunks):
            nxt = chunks[i + 1][0].start
            if 0 <= nxt - end <= JOIN_GAP_S:
                end = nxt
        else:
            end = min(end + TAIL_HOLD_S, duration)
        captions.append(Caption(id=_new_id(), start=round(start, 3), end=round(end, 3), words=chunk))
    return normalize_captions(captions, duration)


def split_caption(c: Caption, at_word: int) -> tuple[Caption, Caption]:
    """Split before word index ``at_word`` (1 <= at_word < len(words)). The first part keeps
    the caption id; the cut is placed at the start of the first word of the second part."""
    if not 1 <= at_word < len(c.words):
        raise ValidationFailed("split position must be between the first and the last word",
                               code="invalid_split")
    first, second = c.words[:at_word], c.words[at_word:]
    cut = _clamp(second[0].start, c.start + MIN_CAPTION_S, max(c.end - MIN_CAPTION_S, c.start + MIN_CAPTION_S))
    override = c.style_override.model_copy(deep=True) if c.style_override else None
    a = Caption(id=c.id, start=c.start, end=cut, words=[w.model_copy() for w in first],
                style_override=override)
    b = Caption(id=_new_id(), start=cut, end=max(c.end, cut + MIN_CAPTION_S), words=[w.model_copy() for w in second],
                style_override=override.model_copy(deep=True) if override else None)
    return a, b


def merge_captions(a: Caption, b: Caption) -> Caption:
    """Merge two captions into one spanning both (keeps the earlier caption's id/override)."""
    first, second = (a, b) if (a.start, a.end) <= (b.start, b.end) else (b, a)
    words = [w.model_copy() for w in (*first.words, *second.words)]
    words.sort(key=lambda w: w.start)
    return Caption(id=first.id, start=min(a.start, b.start), end=max(a.end, b.end), words=words,
                   style_override=first.style_override.model_copy(deep=True) if first.style_override else None)


def _core(text: str) -> str:
    return _WORD_CORE.sub("", text).lower()


def retext_caption(c: Caption, new_text: str) -> Caption:
    """Replace the caption text while keeping its timing.

    When the word count is unchanged (typo/punctuation fixes) every word keeps its ASR timing
    and styling. Otherwise word times are redistributed over ``[start, end]`` proportionally
    to word length; words whose text matches the old word at the same index keep their
    per-word styling."""
    tokens = [t[:80] for t in new_text.split()]
    if not tokens:
        raise ValidationFailed("caption text cannot be empty", code="empty_caption")
    if len(tokens) == len(c.words):
        words = [w.model_copy(update={"text": tok}) for w, tok in zip(c.words, tokens, strict=True)]
        return Caption(id=c.id, start=c.start, end=c.end, words=words, style_override=c.style_override)
    total = sum(len(t) for t in tokens)
    span = c.end - c.start
    words: list[CaptionWord] = []
    acc = 0
    for i, tok in enumerate(tokens):
        s = c.start + span * acc / total
        acc += len(tok)
        e = c.start + span * acc / total
        old = c.words[i] if i < len(c.words) else None
        keep = old is not None and _core(old.text) == _core(tok)
        words.append(CaptionWord(text=tok, start=round(s, 3), end=round(e, 3),
                                 color=old.color if keep else None, scale=old.scale if keep else None,
                                 highlight=old.highlight if keep else False,
                                 emphasis=old.emphasis if keep else False))
    return Caption(id=c.id, start=c.start, end=c.end, words=words, style_override=c.style_override)


def _fit_words(words: list[CaptionWord], start: float, end: float) -> list[CaptionWord]:
    """Clamp word times inside the caption and keep starts monotonic."""
    out: list[CaptionWord] = []
    prev = start
    for w in words:
        if not w.text.strip():
            continue
        s = _clamp(max(w.start, prev), start, end)
        e = _clamp(max(w.end, s), s, end)
        out.append(w.model_copy(update={"text": w.text.strip(), "start": round(s, 4), "end": round(e, 4)}))
        prev = s
    return out


def normalize_captions(captions: list[Caption], duration: float) -> list[Caption]:
    """Sort by start, clamp into ``[0, duration]``, resolve overlaps (the earlier caption is
    shortened; if that is impossible the later one starts after it) and drop empty or
    zero-length captions."""
    out: list[Caption] = []
    for c in sorted(captions, key=lambda c: (c.start, c.end)):
        if not any(w.text.strip() for w in c.words):
            continue
        start = _clamp(c.start, 0.0, duration)
        end = _clamp(c.end, 0.0, duration)
        if out and start < out[-1].end:
            prev = out[-1]
            if start >= prev.start + MIN_CAPTION_S:
                out[-1] = prev.model_copy(update={"end": start, "words": _fit_words(prev.words, prev.start, start)})
            else:
                start = prev.end
        if end - start < MIN_CAPTION_S - 1e-9:
            continue
        words = _fit_words(c.words, start, end)
        if not words:
            continue
        out.append(Caption(id=c.id, start=round(start, 4), end=round(end, 4), words=words,
                           style_override=c.style_override))
    return out
