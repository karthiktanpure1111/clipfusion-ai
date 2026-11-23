"""Diversity filter for clip candidates: temporal overlap AND near-duplicate content.

Streams repeat themselves (catch-phrases, re-explanations, a reaction replayed in a
recap). Two candidates are near-duplicates when the share of the shorter transcript's word
trigrams (counted with multiplicity, so one repeated sentence does not make every clip that
contains it a "duplicate") also found in the other exceeds ``max_text_similarity`` --
independent of where they are in the video. Temporal rules are those of
:func:`app.modules.clipping.boundaries.suppress_overlaps`.
"""

from __future__ import annotations

import re
import zlib
from collections import Counter
from collections.abc import Callable, Sequence

from app.modules.clipping.boundaries import DUPLICATE_START_S, overlap_of_shorter
from app.schemas.media import HighlightCandidate

MAX_TEXT_SIMILARITY = 0.6
_WORD_RE = re.compile(r"[a-z0-9']+")


def signature(text: str) -> Counter[int]:
    """Hashed word trigrams of ``text`` with counts (case/punctuation insensitive)."""
    words = _WORD_RE.findall((text or "").lower().replace("’", "'"))
    if len(words) < 3:
        return Counter(zlib.crc32(w.encode()) for w in words)
    return Counter(zlib.crc32(" ".join(words[i:i + 3]).encode()) for i in range(len(words) - 2))


def text_similarity(a: Counter[int], b: Counter[int]) -> float:
    """Multiset containment of the smaller trigram bag in the larger one (0..1)."""
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return 0.0
    return sum((a & b).values()) / min(na, nb)


def select_diverse(cands: Sequence[HighlightCandidate], max_overlap: float, *,
                   text_of: Callable[[HighlightCandidate], str] | None = None,
                   max_text_similarity: float = MAX_TEXT_SIMILARITY,
                   limit: int | None = None) -> list[HighlightCandidate]:
    """Greedy selection by descending score that drops temporal overlaps (as
    ``suppress_overlaps``) and near-duplicate transcripts. ``text_of`` defaults to the
    candidate's ``transcript``; it is only evaluated for candidates that pass the time rules."""
    get_text = text_of or (lambda c: c.transcript)
    kept: list[HighlightCandidate] = []
    sigs: list[Counter[int]] = []
    for c in sorted(cands, key=lambda c: (-c.score, c.start, c.end)):
        if any(abs(c.start - k.start) < DUPLICATE_START_S
               or overlap_of_shorter(c.start, c.end, k.start, k.end) > max_overlap for k in kept):
            continue
        sig = signature(get_text(c))
        if any(text_similarity(sig, s) > max_text_similarity for s in sigs):
            continue
        kept.append(c)
        sigs.append(sig)
        if limit is not None and len(kept) >= limit:
            break
    return kept
