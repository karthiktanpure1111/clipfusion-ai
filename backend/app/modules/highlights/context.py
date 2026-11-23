"""Contextual (discourse-level) signals for clip selection -- numpy only, no external models.

A great short clip is *self-contained*: it opens without needing earlier context, covers
one complete thought / topic / exchange / story, and ends on a conclusion or punchline
instead of mid-thought. :func:`build_context` pre-computes per-sentence signals once per
source; :func:`window_context` scores a window of whole sentences in O(segments).

* **Topic segmentation** (TextTiling, Hearst 1997, adapted to speech): content-word
  bag-of-words vectors (hashed into ``HASH_DIM`` buckets, stable crc32 hashing) of the ~25
  content words before and after every sentence gap are compared by cosine similarity; the
  smoothed similarity curve's *depth* at a gap (how far it dips below the peaks on both sides)
  plus the pause length and discourse cues ("let's talk about", "anyway", ... vs. "and",
  "because", ...) give a boundary strength 0..1. Gaps at or above ``SEGMENT_THRESHOLD``
  split the transcript into topic segments.
* **Context dependency** of a sentence used as the clip's FIRST line: leading conjunctions,
  anaphoric openers ("that's why", "so he", "this is why", "after that"), dangling pronouns,
  bare answers whose question precedes the clip, mid-topic starts.
* **Ending quality** of a sentence used as the LAST line: topic boundary right after it,
  conclusion / lesson markers, punchlines (exclamations, short emphatic lines, laughter),
  minus questions answered only after the clip, set-up lines ("let me tell you") and
  continuations in the next sentence.
* **Question -> answer** pairs and **story arcs** (setup -> turn -> payoff markers).

All lexicons are English; other languages still get the language-independent parts
(lexical cohesion, pauses, punctuation).
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

import numpy as np

from app.modules.highlights.lexicon import find_cues
from app.modules.highlights.sentences import Sentence
from app.modules.metadata.keywords import STOPWORDS

HASH_DIM = 512
BLOCK_WORDS = 25                # content words per side of a gap (TextTiling block)
MAX_BLOCK_SENTENCES = 6
SEGMENT_THRESHOLD = 0.5
MIN_SEGMENT_SENTENCES = 2
QA_MAX_GAP_S = 5.0
DEPTH_SCALE = 0.6               # depth of this size (cosine units) = full boundary strength
_WORD_RE = re.compile(r"[a-z0-9']+")

CONTINUATION_OPENERS = frozenset({"and", "but", "because", "or", "also", "plus", "then", "cause", "cuz",
                                  "which", "whereas", "although", "though"})
INTERJECTIONS = frozenset({"okay", "ok", "oh", "well", "alright", "um", "uh", "hmm", "like", "yo", "hey"})
RESPONSE_WORDS = frozenset({"yeah", "yes", "yep", "no", "nope", "exactly", "right", "true", "sure", "totally",
                            "agreed", "absolutely", "definitely"})
THIRD_PERSON = frozenset({"he", "she", "they", "him", "her", "them", "his", "their", "it", "its"})
DEMONSTRATIVES = frozenset({"that", "those", "this", "these"})
PRONOUN_VERBS = frozenset({"is", "was", "'s", "s", "are", "were", "means", "meant", "made", "makes", "happened",
                           "said", "did", "does", "will", "would", "could", "can", "has", "had", "one", "part",
                           "thing", "guy", "way"})
ANAPHORIC_OPENERS = (
    "that's why", "that is why", "this is why", "which is why", "that's how", "that means", "which means",
    "and that", "so he", "so she", "so they", "so it", "so that's", "so this", "after that", "because of that",
    "like i said", "as i said", "as i mentioned", "the other one", "same thing", "that one", "that was",
    "that's what", "it was", "it's because", "then he", "then she", "then they", "and then", "but then",
    "he said", "she said", "they said", "on top of that", "other than that", "besides that", "that guy",
)
TOPIC_OPENERS = (
    "let's talk about", "let us talk about", "let me tell you", "let me explain", "here's the thing",
    "here is the thing", "so here is", "so here's", "the question is", "quick question", "moving on", "next up",
    "anyway", "speaking of", "another thing", "one more thing", "first of all", "the first thing",
    "what is the", "what's the", "have you ever", "did you know", "story time", "okay chat", "alright chat",
    "so what did we learn", "today we", "welcome back",
)
CONCLUSION_MARKERS = (
    "that's why", "that is why", "which is why", "that's how", "and that's", "so that's", "that's the secret",
    "that's the point", "the point is", "the lesson", "in the end", "at the end of the day", "bottom line",
    "long story short", "remember that", "trust me", "never again", "that's it", "that is it", "the moral",
    "lesson learned", "i learned", "and that is", "that's all", "so yeah", "see you", "thank you so much",
    "thanks for watching", "fix your", "every single time", "and it worked", "changed my life", "ever since",
)
SETUP_LINE_MARKERS = (
    "let me tell you", "here's the thing", "here is the thing", "here's what", "here is what", "check this out",
    "watch this", "listen to this", "guess what", "you know what", "the question is", "wait for it",
)
STORY_SETUP = (
    "let me tell you", "story", "one time", "years ago", "back when", "the other day", "last week", "last year",
    "last night", "i remember", "imagine", "picture this", "here's what happened", "so i was", "when i was",
    "i was working", "i was walking", "growing up", "a while ago", "once",
)
STORY_TURN = (
    "then", "suddenly", "but then", "out of nowhere", "all of a sudden", "until", "turns out", "it turns out",
    "plot twist", "wait", "and then", "that's when", "one night", "one day", "except", "instead",
)
STORY_PAYOFF = (
    "finally", "in the end", "ever since", "and that's how", "the lesson", "i learned", "that's why",
    "changed my life", "helped", "now i", "to this day", "never again", "it worked", "and it worked",
    "turned out", "so yeah", "that's the story",
)


def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![\w']){alternation}(?![\w'])", re.IGNORECASE)


_ANAPHORIC = _phrase_re(ANAPHORIC_OPENERS)
_TOPIC = _phrase_re(TOPIC_OPENERS)
_CONCLUSION = _phrase_re(CONCLUSION_MARKERS)
_SETUP_LINE = _phrase_re(SETUP_LINE_MARKERS)
_STORY = {"setup": _phrase_re(STORY_SETUP), "turn": _phrase_re(STORY_TURN), "payoff": _phrase_re(STORY_PAYOFF)}


def _norm(text: str) -> str:
    return (text or "").replace("’", "'").lower()


def words_of(text: str) -> list[str]:
    return _WORD_RE.findall(_norm(text))


def _stem(w: str) -> str:
    for suffix in ("'s", "ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def content_tokens(text: str) -> list[str]:
    """Lower-cased, lightly stemmed content words (stopwords and short tokens removed)."""
    return [_stem(w) for w in words_of(text) if len(w) >= 3 and w not in STOPWORDS and not w.isdigit()]


def _opening_words(text: str, n: int = 3) -> list[str]:
    """First ``n`` words after leading interjections ("okay", "oh", ...)."""
    ws = words_of(text)
    i = 0
    while i < len(ws) - 1 and ws[i] in INTERJECTIONS:
        i += 1
    return ws[i:i + n]


def _starts_with(pattern: re.Pattern[str], text: str, within_chars: int = 18) -> bool:
    m = pattern.search(_norm(text))
    return m is not None and m.start() <= within_chars


@dataclass
class ContextSignals:
    """Per-sentence context signals of one transcript (index = sentence index)."""

    boundary: np.ndarray            # len n + 1: topic-boundary strength BEFORE sentence i (0..1); 1 at both ends
    segment: np.ndarray             # topic segment id per sentence
    segment_bounds: list[tuple[int, int]] = field(default_factory=list)   # inclusive sentence ranges
    dependency: np.ndarray = field(default_factory=lambda: np.zeros(0))   # 0..1 as an OPENING line
    dependency_why: list[str] = field(default_factory=list)
    ending: np.ndarray = field(default_factory=lambda: np.zeros(0))       # 0..1 as a CLOSING line
    ending_why: list[str] = field(default_factory=list)
    answers: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))  # question index or -1
    story: dict[str, np.ndarray] = field(default_factory=dict)            # setup/turn/payoff flags
    durations: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @property
    def size(self) -> int:
        return int(self.segment.size)


# ----------------------------------------------------------- topic segmentation
def _hashed_counts(sentences: list[Sentence]) -> tuple[np.ndarray, np.ndarray]:
    n = len(sentences)
    mat = np.zeros((n, HASH_DIM), dtype=np.float32)
    counts = np.zeros(n, dtype=np.int64)
    for i, s in enumerate(sentences):
        toks = content_tokens(s.text)
        counts[i] = len(toks)
        for t in toks:
            mat[i, zlib.crc32(t.encode("utf-8")) % HASH_DIM] += 1.0
    return mat, counts


def _gap_similarity(mat: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Cosine similarity of the content-word blocks on both sides of every gap (len n + 1)."""
    n = mat.shape[0]
    sims = np.ones(n + 1, dtype=np.float64)
    if n < 2:
        return sims
    prefix = np.vstack([np.zeros((1, HASH_DIM), np.float32), np.cumsum(mat, axis=0, dtype=np.float32)])
    words = np.concatenate([[0], np.cumsum(counts)])
    for g in range(1, n):
        lo = g - 1
        while lo > 0 and g - lo < MAX_BLOCK_SENTENCES and words[g] - words[lo] < BLOCK_WORDS:
            lo -= 1
        hi = g + 1
        while hi < n and hi - g < MAX_BLOCK_SENTENCES and words[hi] - words[g] < BLOCK_WORDS:
            hi += 1
        left, right = prefix[g] - prefix[lo], prefix[hi] - prefix[g]
        denom = float(np.linalg.norm(left) * np.linalg.norm(right))
        sims[g] = float(left @ right) / denom if denom > 0 else 0.0
    return sims


def _depth(sims: np.ndarray) -> np.ndarray:
    """TextTiling depth score per gap on the 3-point smoothed similarity curve."""
    n1 = sims.size
    depth = np.zeros(n1, dtype=np.float64)
    if n1 <= 3:
        return depth
    inner = sims[1:-1].copy()
    if inner.size >= 3:
        smooth = np.convolve(np.pad(inner, 1, mode="edge"), np.ones(3) / 3.0, mode="valid")
    else:
        smooth = inner
    m = smooth.size
    # Peak reached when climbing left / right from k (exact O(n) form of the hill climb).
    left_peak, right_peak = smooth.copy(), smooth.copy()
    for k in range(1, m):
        if smooth[k - 1] >= smooth[k]:
            left_peak[k] = left_peak[k - 1]
    for k in range(m - 2, -1, -1):
        if smooth[k + 1] >= smooth[k]:
            right_peak[k] = right_peak[k + 1]
    depth[1:m + 1] = (left_peak - smooth) + (right_peak - smooth)
    return depth


def topic_boundaries(sentences: list[Sentence]) -> np.ndarray:
    """Boundary strength 0..1 before every sentence (len n + 1, both ends = 1)."""
    n = len(sentences)
    out = np.ones(n + 1, dtype=np.float64)
    if n < 2:
        return out
    mat, counts = _hashed_counts(sentences)
    depth = _depth(_gap_similarity(mat, counts))
    for g in range(1, n):
        prev, cur = sentences[g - 1], sentences[g]
        pause = max(0.0, cur.start - prev.end)
        b = 0.65 * min(1.0, depth[g] / DEPTH_SCALE) + 0.35 * min(1.0, max(0.0, pause - 0.4) / 2.0)
        opener = _opening_words(cur.text, 1)
        if _starts_with(_TOPIC, cur.text):
            b += 0.3
        if (opener and opener[0] in CONTINUATION_OPENERS) or _starts_with(_ANAPHORIC, cur.text, 6):
            b -= 0.35
        if prev.is_question and not cur.is_question:      # never split a question from its answer
            b -= 0.4
        out[g] = min(1.0, max(0.0, b))
    return out


def _segments(boundary: np.ndarray, n: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    seg = np.zeros(n, dtype=np.int64)
    bounds: list[tuple[int, int]] = []
    start = 0
    for g in range(1, n):
        if boundary[g] >= SEGMENT_THRESHOLD and g - start >= MIN_SEGMENT_SENTENCES:
            bounds.append((start, g - 1))
            start = g
    if n:
        bounds.append((start, n - 1))
    for k, (a, b) in enumerate(bounds):
        seg[a:b + 1] = k
    return seg, bounds


# ------------------------------------------------------------- opening lines
def _answers(sentences: list[Sentence]) -> np.ndarray:
    n = len(sentences)
    out = np.full(n, -1, dtype=np.int64)
    for i in range(1, n):
        q = sentences[i - 1]
        if q.is_question and not sentences[i].is_question and sentences[i].start - q.end <= QA_MAX_GAP_S:
            out[i] = i - 1
    return out


def opening_dependency(s: Sentence, prev: Sentence | None, boundary_before: float,
                       answers_question: bool) -> tuple[float, str]:
    """0..1: how much ``s`` relies on what was said before it (as a clip's first line)."""
    first = _opening_words(s.text, 3)
    dep, why = 0.0, ""
    if not first:
        return 0.0, ""
    w0 = first[0]
    w1 = first[1] if len(first) > 1 else ""
    if _starts_with(_ANAPHORIC, s.text, 12):
        dep, why = 0.5, "opens by referring back"
    elif w0 in CONTINUATION_OPENERS:
        dep, why = 0.45, f"opens with “{w0}”"
    elif w0 == "so" and w1 in THIRD_PERSON | DEMONSTRATIVES:
        dep, why = 0.45, f"opens with “so {w1}”"
    elif w0 in THIRD_PERSON and w0 not in ("it", "its"):
        dep, why = 0.4, f"opens with the pronoun “{w0}”"
    elif w0 in ("that", "those") and (w1 in PRONOUN_VERBS or not w1):
        dep, why = 0.35, f"opens with “{w0} {w1}”".rstrip()
    elif w0 == "it" and w1 in PRONOUN_VERBS:
        dep, why = 0.3, "opens with “it”"
    elif w0 in ("this", "these") and w1 in ("is", "was", "means", "made", "makes", "happened"):
        dep, why = 0.15, ""
    elif w0 in RESPONSE_WORDS:
        dep, why = (0.45, "opens with a reply") if answers_question else (0.15, "")
    elif w0 == "so":
        dep = 0.12
    if answers_question:
        dep += 0.35
        why = why or "starts with an answer to an earlier question"
    if prev is not None:
        dep += 0.25 * (1.0 - boundary_before)
    if not why and dep >= 0.25:
        why = "starts in the middle of a topic"
    return min(1.0, dep), why


# -------------------------------------------------------------- closing lines
def closing_quality(s: Sentence, nxt: Sentence | None, boundary_after: float, *,
                    question_answered_later: bool) -> tuple[float, str]:
    """0..1: how good ``s`` is as a clip's last line (conclusion / punchline vs mid-thought)."""
    text = _norm(s.text)
    q = 0.35 + 0.3 * boundary_after
    good, bad = "", ""
    if _CONCLUSION.search(text):
        q += 0.25
        good = "ends on a conclusion"
    wc = s.word_count
    punch = s.is_exclamation or (wc <= 8 and s.terminal and not s.is_question) or bool(
        find_cues(s.text, "laughter") or find_cues(s.text, "surprise"))
    if punch:
        q += 0.15
        good = good or "ends on a punchline"
    if nxt is not None and nxt.start - s.end <= 2.0 and find_cues(nxt.text, "laughter"):
        q += 0.1
        good = good or "ends right before the laugh"
    if _SETUP_LINE.search(text) or s.text.rstrip().endswith((":", "…", "...")):
        q -= 0.4
        bad = "ends on a set-up line"
    if question_answered_later:
        q -= 0.45
        bad = bad or "ends on a question answered after the clip"
    if not s.terminal:
        q -= 0.15
        bad = bad or "ends mid-sentence"
    if nxt is not None and nxt.start - s.end <= 1.0:
        opener = _opening_words(nxt.text, 1)
        if (opener and opener[0] in CONTINUATION_OPENERS) or _starts_with(_ANAPHORIC, nxt.text, 6):
            q -= 0.25
            bad = bad or "the thought continues after the clip"
    if boundary_after >= SEGMENT_THRESHOLD and not good:
        good = "ends where the topic ends"
    q = min(1.0, max(0.0, q))
    return q, (bad if q < 0.5 and bad else good)


# --------------------------------------------------------------------- public
def build_context(sentences: list[Sentence]) -> ContextSignals:
    """Pre-compute every per-sentence context signal for ``sentences`` (time-ordered)."""
    n = len(sentences)
    boundary = topic_boundaries(sentences)
    seg, bounds = _segments(boundary, n)
    answers = _answers(sentences)
    answered_by_next = np.zeros(n, dtype=bool)
    answered_by_next[answers[answers >= 0]] = True
    dependency = np.zeros(n)
    dep_why: list[str] = []
    ending = np.zeros(n)
    end_why: list[str] = []
    for i, s in enumerate(sentences):
        prev = sentences[i - 1] if i > 0 else None
        nxt = sentences[i + 1] if i + 1 < n else None
        d, dw = opening_dependency(s, prev, float(boundary[i]), bool(answers[i] >= 0))
        e, ew = closing_quality(s, nxt, float(boundary[i + 1]), question_answered_later=bool(answered_by_next[i]))
        dependency[i], ending[i] = d, e
        dep_why.append(dw)
        end_why.append(ew)
    story = {k: np.array([1.0 if p.search(_norm(s.text)) else 0.0 for s in sentences]) for k, p in _STORY.items()}
    durations = np.array([max(0.0, s.end - s.start) for s in sentences])
    return ContextSignals(boundary=boundary, segment=seg, segment_bounds=bounds, dependency=dependency,
                          dependency_why=dep_why, ending=ending, ending_why=end_why, answers=answers, story=story,
                          durations=durations)


def _story_arc(ctx: ContextSignals, first: int, last: int) -> float:
    if not ctx.story:
        return 0.0
    span = last - first + 1
    setup = np.flatnonzero(ctx.story["setup"][first:last + 1])
    if setup.size == 0 or setup[0] > span / 2:
        return 0.0
    s0 = int(setup[0])
    turn = np.flatnonzero(ctx.story["turn"][first + s0 + 1:last + 1])
    payoff = np.flatnonzero(ctx.story["payoff"][first + s0 + 1:last + 1])
    score = 0.4
    if turn.size:
        score += 0.3
    if payoff.size and (first + s0 + 1 + int(payoff[-1])) >= first + span / 2:
        score += 0.3
    return score


def _qa_structure(ctx: ContextSignals, first: int, last: int) -> float:
    """1 when a question is asked AND answered inside the window (answer >= 2 sentences or
    running to a topic boundary); 0.6 for a one-line answer; 0 without an exchange."""
    best = 0.0
    for i in range(first + 1, last + 1):
        q = int(ctx.answers[i])
        if q < first:
            continue
        seg_end = ctx.segment_bounds[int(ctx.segment[i])][1]
        full = (last >= i + 1) or last >= seg_end
        best = max(best, 1.0 if full else 0.6)
    return best


def window_context(ctx: ContextSignals, first: int, last: int) -> dict[str, float]:
    """Context features (0..1) of the window covering sentences ``first..last`` (inclusive).

    ``topic``      starts/ends on topic boundaries and covers complete topic segments
    ``standalone`` 1 - context dependency of the opening sentence
    ``ending``     closing-line quality
    ``arc``        best of story arc (setup -> turn -> payoff) and question -> answer structure
    """
    if ctx.size == 0 or first > last:
        return {}
    b_start, b_end = float(ctx.boundary[first]), float(ctx.boundary[last + 1])
    total = float(ctx.durations[first:last + 1].sum()) or 1.0
    complete = 0.0
    for k in range(int(ctx.segment[first]), int(ctx.segment[last]) + 1):
        a, b = ctx.segment_bounds[k]
        if a >= first and b <= last:
            complete += float(ctx.durations[a:b + 1].sum())
    coverage = complete / total
    return {
        "topic": round(0.35 * b_start + 0.35 * b_end + 0.3 * coverage, 4),
        "standalone": round(1.0 - float(ctx.dependency[first]), 4),
        "ending": round(float(ctx.ending[last]), 4),
        "arc": round(max(_story_arc(ctx, first, last), _qa_structure(ctx, first, last)), 4),
        "topic_coverage": round(coverage, 4),
    }
