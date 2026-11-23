"""Small built-in lexicons used as transparent lexical cues by the highlight detector.

Every cue is a plain word/phrase list so a highlight's reasons can quote the exact text
that matched. Matching is case-insensitive on word boundaries (see :func:`find_cues`).
The lists are English; other languages still get the language-independent cues
(punctuation, numbers, audio and visual signals).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

CUE_GROUPS: dict[str, tuple[str, ...]] = {
    "laughter": (
        "haha", "hahaha", "hehe", "lol", "lmao", "lmfao", "rofl", "[laughter]", "(laughter)", "[laughs]",
        "(laughs)", "that's hilarious", "so funny", "i'm dying", "i'm crying", "that's funny",
        "funniest", "hilarious", "joke", "kidding",
    ),
    "surprise": (
        "no way", "what the", "oh my god", "oh my gosh", "omg", "wait what", "are you serious",
        "unbelievable", "insane", "crazy", "wow", "whoa", "holy", "i can't believe", "shocking",
        "plot twist", "out of nowhere", "didn't expect", "wtf",
    ),
    "strong_statement": (
        "never", "always", "everyone", "nobody", "everything", "nothing", "the best", "the worst",
        "the most", "the biggest", "the only", "the truth", "the secret", "the problem", "the reason",
        "biggest mistake", "most important", "i guarantee", "honestly", "literally", "absolutely",
        "completely", "definitely", "100 percent", "game changer", "changed my life", "you need to",
        "stop doing", "the key", "the real",
    ),
    "emotional": (
        "love", "hate", "scared", "afraid", "sad", "cry", "cried", "tears", "happy", "proud", "angry",
        "heartbroken", "grateful", "thankful", "emotional", "hurt", "pain", "lonely", "beautiful",
        "miss you", "passed away", "struggle", "depressed", "anxiety", "dream", "fear",
    ),
    "explanation": (
        "because", "the reason", "here's how", "here's why", "how to", "step one", "first of all",
        "basically", "in other words", "which means", "that means", "for example", "the way it works",
        "the trick", "the idea is", "let me explain", "the difference", "what happens is", "so that",
    ),
    "gaming": (
        "clutch", "ace", "let's go", "lets go", "gg", "no way", "headshot", "one tap", "triple kill",
        "quadra", "penta", "pentakill", "victory royale", "boss fight", "speedrun", "world record",
        "pb", "personal best", "1v1", "1v2", "1v3", "1v4", "1v5", "rank up", "ranked", "carry", "snipe",
        "sniped", "killed", "kill", "frag", "combo", "respawn", "loot", "legendary", "nerf", "buff",
        "noob", "pog", "poggers", "ez", "insane play", "clip it", "clip that",
    ),
    "audience": (
        "chat", "you guys", "thank you for", "thanks for", "donation", "donated", "subscribe", "subscribed",
        "subs", "followers", "raid", "bits", "superchat", "super chat", "comments",
        "comment below", "let me know", "shout out", "shoutout", "viewers", "stream", "welcome back",
        "appreciate you", "q&a", "someone asked", "question from",
    ),
    "hook": (
        "here's", "here is", "the secret", "nobody", "no one", "you won't believe", "what if", "imagine",
        "the truth", "stop", "this is why", "this is how", "did you know", "you need", "never", "the biggest",
        "the craziest", "watch this", "listen", "wait", "how i", "why you", "i tried", "story",
    ),
}

LEADING_CONJUNCTIONS = frozenset({"and", "but", "so", "because", "or", "then", "also", "plus", "cause", "cuz"})

GROUP_LABELS: dict[str, str] = {
    "laughter": "Laughter",
    "surprise": "Surprised reaction",
    "strong_statement": "Strong statement",
    "emotional": "Emotional moment",
    "explanation": "Explanation",
    "gaming": "Gaming moment",
    "audience": "Audience interaction",
}

_NUMBER_RE = re.compile(r"(?<![\w.])(?:\d+(?:[.,]\d+)?%?|\$\d+)(?![\w])")


@lru_cache(maxsize=16)
def _pattern(group: str) -> re.Pattern[str]:
    phrases = sorted(CUE_GROUPS[group], key=len, reverse=True)
    alternation = "|".join(re.escape(p) for p in phrases)
    return re.compile(rf"(?<![\w']){alternation}(?![\w'])", re.IGNORECASE)


def find_cues(text: str, group: str) -> list[str]:
    """Return the cue phrases of ``group`` found in ``text`` (lowercased, in order)."""
    if not text:
        return []
    normalised = text.replace("’", "'")
    return [m.group(0).lower() for m in _pattern(group).finditer(normalised)]


def count_numbers(text: str) -> int:
    """Count numeric tokens (``42``, ``3.5``, ``90%``, ``$100``) in ``text``."""
    return len(_NUMBER_RE.findall(text or ""))


def first_word(text: str) -> str:
    """Lowercased first alphabetic token of ``text`` ('' when none)."""
    m = re.search(r"[A-Za-z']+", text or "")
    return m.group(0).lower() if m else ""


def all_groups() -> Iterable[str]:
    """Names of the lexical cue groups (excluding the hook-only list)."""
    return (g for g in CUE_GROUPS if g != "hook")
