"""Keyword extraction and text helpers for extractive (non-LLM) clip metadata."""

from __future__ import annotations

import re
from collections import Counter

STOPWORDS = frozenset("""
a about above after again against all almost also am an and any are aren't around as at back be because been
before being below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't
down during each even ever every few for from further get gets getting go goes going gonna got gotta had hadn't
has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd
i'll i'm i've if in into is isn't it it's its itself just kind know let let's like little lot make made many me
might more most much must my myself need never no nor not now of off oh ok okay on once one only or other ought
our ours ourselves out over own pretty probably put quite rather really right said same say says see she she'd
she'll she's should shouldn't so some something still such sure take than that that's the their theirs them
themselves then there there's these they they'd they'll they're they've thing things think this those though
through to too um uh under until up upon us very via wanna want was wasn't way we we'd we'll we're we've well
went were weren't what what's when when's where where's whether which while who who's whom why why's will with
won't would wouldn't yeah yes yet you you'd you'll you're you've your yours yourself yourselves actually
anyway basically literally maybe stuff guys guy hey hmm mean means time times today now going come comes came
look looks looking thank thanks first second two three back again
""".split())

SMALL_TITLE_WORDS = frozenset("a an and as at but by for in nor of on or so the to up via vs with".split())
_TOKEN_RE = re.compile(r"[^\W_][\w'\-]*", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences on terminal punctuation."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(" ".join((text or "").split())) if s.strip()]


def tokens(text: str) -> list[str]:
    """Lowercased word tokens (apostrophes kept, edge hyphens/quotes stripped)."""
    return [t.strip("'-").lower() for t in _TOKEN_RE.findall(text or "") if t.strip("'-")]


def is_keyword(token: str) -> bool:
    return len(token) >= 3 and token not in STOPWORDS and not token.isdigit() and "'" not in token


def keyword_scores(text: str, *, boost_text: str | None = None) -> Counter[str]:
    """Term frequency of non-stopword tokens; terms also in ``boost_text`` count 50 % more."""
    counts: Counter[str] = Counter(t for t in tokens(text) if is_keyword(t))
    boost = {t for t in tokens(boost_text or "") if is_keyword(t)}
    for term in list(counts):
        if term in boost:
            counts[term] = counts[term] * 1.5
    return counts


def top_keywords(text: str, n: int, *, boost_text: str | None = None) -> list[str]:
    """The ``n`` highest-scoring keywords (ties broken by first appearance)."""
    scores = keyword_scores(text, boost_text=boost_text)
    order = {t: i for i, t in reversed(list(enumerate(tokens(text))))}
    return sorted(scores, key=lambda t: (-scores[t], order.get(t, 0)))[:n]


def camel_case(phrase: str) -> str:
    """``"world record"`` -> ``"WorldRecord"`` (letters/digits only)."""
    parts = re.findall(r"[^\W_]+", phrase, re.UNICODE)
    return "".join(p[:1].upper() + p[1:] for p in parts)


def title_case(text: str) -> str:
    """Title-case ``text`` keeping small words lowercase (except first/last) and acronyms."""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        core = w.strip("\"'“”‘’()[]")
        if core.isupper() and len(core) > 1:
            out.append(w)
        elif 0 < i < len(words) - 1 and core.lower() in SMALL_TITLE_WORDS:
            out.append(w.lower())
        else:
            idx = next((k for k, ch in enumerate(w) if ch.isalpha()), None)
            out.append(w if idx is None else w[:idx] + w[idx].upper() + w[idx + 1:])
    return " ".join(out)


def truncate_words(text: str, limit: int, *, ellipsis: str = "") -> str:
    """Cut ``text`` to at most ``limit`` characters at a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    room = max(1, limit - len(ellipsis))
    cut = text[:room + 1].rsplit(" ", 1)[0] if " " in text[:room + 1] else text[:room]
    cut = cut[:room].rstrip(" ,;:-–—")
    return cut + ellipsis
