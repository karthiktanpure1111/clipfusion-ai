"""Light, deterministic punctuation / casing clean-up of ASR output.

Whisper output is already punctuated; this only fixes the small artefacts that look bad in
captions: doubled spaces, spaces before punctuation, repeated punctuation, lowercase sentence
starts and (for English) the lowercase pronoun "i". Nothing is invented or reworded.
"""

from __future__ import annotations

import re

_SPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:%])")
_REPEAT_PUNCT_RE = re.compile(r"([,;:])\1+")
_LONG_DOTS_RE = re.compile(r"\.{4,}")
_REPEAT_BANG_RE = re.compile(r"([!?])\1{2,}")
_SENTENCE_START_RE = re.compile(r"((?<!\.\.)[.!?]\s+)([a-z])")  # not after an ellipsis
_EN_I_RE = re.compile(r"(?<![\w'])i(?=(?:'(?:m|d|ll|ve|s))?(?![\w']))")
SENTENCE_END_CHARS = ".!?"
# English words a sentence (or a title) does not end on: "... no way I | cannot believe ..."
_DANGLING_EN = frozenset("""
a an the my your his her its our their this these those some any every each no
i we he she they who whose which that what how why when where while
and or but so because if than as though although unless until
of in on at to for from with by into onto about over under between through without like
is are was were be been being am do does did have has had will would can could should must may might
not don't doesn't didn't can't won't i'm i've i'll i'd you're you've we're we've they're it's that's
very really just more most such
""".split())


def _fix_english_i(text: str) -> str:
    return _EN_I_RE.sub("I", text)


def _capitalise_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:] if ch.islower() else text
        if ch.isdigit():
            return text
    return text


def clean_text(text: str, language: str | None = None, *, sentence_start: bool = True) -> str:
    """Return ``text`` with whitespace/punctuation artefacts fixed and sentences capitalised.

    ``sentence_start=False``: the text continues the previous segment's sentence, so its first
    letter keeps its case."""
    out = _SPACE_RE.sub(" ", text or "").strip()
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _REPEAT_PUNCT_RE.sub(r"\1", out)
    out = _LONG_DOTS_RE.sub("...", out)
    out = _REPEAT_BANG_RE.sub(r"\1\1", out)
    if (language or "en").lower().startswith("en"):
        out = _fix_english_i(out)
    out = _SENTENCE_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), out)
    return _capitalise_first(out) if sentence_start else out


def clean_word(word: str, *, sentence_start: bool, language: str | None = None) -> str:
    """Clean one ASR word token, preserving its leading space (captions rely on it)."""
    lead = word[: len(word) - len(word.lstrip())]
    core = _SPACE_RE.sub(" ", word.strip())
    if (language or "en").lower().startswith("en"):
        core = _fix_english_i(core)
    if sentence_start:
        core = _capitalise_first(core)
    return lead + core


def ends_sentence(word: str) -> bool:
    """True when the token ends with sentence-final punctuation (ignoring closing quotes)."""
    core = word.strip().rstrip("\"')]”’")
    return bool(core) and core[-1] in SENTENCE_END_CHARS


def is_dangling(word: str) -> bool:
    """True when an (English) phrase ending with ``word`` is obviously unfinished."""
    core = word.strip().strip("\"'\u201c\u201d\u2018\u2019()[]").replace("\u2019", "'").lower()
    return core in _DANGLING_EN
