"""Clip metadata (titles, descriptions, captions, hashtags, hooks) for YouTube Shorts and
Instagram Reels.

With Claude configured (and ``use_llm``) the metadata is written by the LLM from the clip
transcript (``source="llm"``). Otherwise -- or when the LLM fails -- it is extracted from the
transcript itself (``source="extractive"``): no text is invented. Platform limits are
enforced on both paths (:mod:`.limits`). The writer is chosen by
:func:`app.modules.ai.registry.get_metadata_provider` (``METADATA_PROVIDER``).

Extractive titles come from the clip's OWN most informative sentence (keywords of the clip,
not of the source title; context-dependent openers like "So he..." are avoided and leading
fillers stripped). ``avoid_titles`` (the titles of the source's other clips) are never
repeated. ``attribution`` (credit line) is appended to the YouTube description and the
Instagram caption and always survives the length limits. Hashtags are few: ``Shorts`` /
``Reels`` plus the clip's recurring keywords and at most one category tag.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.modules.highlights import llm_client
from app.modules.metadata.keywords import (
    camel_case,
    keyword_scores,
    split_sentences,
    title_case,
    tokens,
    top_keywords,
    truncate_words,
)
from app.modules.metadata.limits import IG_CAPTION_MAX, YT_DESCRIPTION_MAX_BYTES, enforce_limits, hashtag_line
from app.modules.transcription.cleanup import is_dangling
from app.schemas.media import ClipMetadata, InstagramMetadata, YouTubeMetadata

log = logging.getLogger(__name__)

EXTRACTIVE_TITLE_MAX = 70
EXTRACTIVE_DESCRIPTION_MAX = 300
INSTAGRAM_CTA = "Follow for more clips like this."
YT_KEYWORD_HASHTAGS = 3
IG_KEYWORD_HASHTAGS = 6
_LEADING_FILLER_RE = re.compile(r"^(?:(?:okay|ok|so|well|alright|all right|um|uh|oh|yeah|hey|now|and|but)\b[,.!]?\s+)+",
                                re.IGNORECASE)
_TITLE_KEY_RE = re.compile(r"[^\w]+", re.UNICODE)

CATEGORY_TAGS: dict[str, list[str]] = {
    "funny": ["Funny", "Comedy"],
    "exciting": ["Epic", "Hype"],
    "strong_statement": ["Motivation", "Truth"],
    "emotional": ["Emotional", "Story"],
    "explanation": ["Explained", "Learning"],
    "surprising": ["Unexpected", "Wow"],
    "gaming_achievement": ["Gaming", "Gamer"],
    "reaction": ["Reaction", "Reacts"],
    "audience_interaction": ["Livestream", "Community"],
    "conversation": ["Podcast", "Conversation"],
}
CATEGORY_TITLES: dict[str, str] = {
    "funny": "Funny Moment", "exciting": "Epic Moment", "strong_statement": "Hot Take",
    "emotional": "Emotional Moment", "explanation": "Quick Explanation", "surprising": "Unexpected Moment",
    "gaming_achievement": "Gaming Highlight", "reaction": "Reaction Highlight",
    "audience_interaction": "Stream Highlight", "conversation": "Conversation Highlight",
}


# ------------------------------------------------------------------ extractive
def _sentence_score(sentence: str, scores: dict[str, float]) -> float:
    from app.modules.highlights.context import opening_dependency
    from app.modules.highlights.sentences import Sentence

    words = tokens(sentence)
    if not words:
        return 0.0
    value = sum(scores.get(w, 0.0) for w in set(words)) / (len(words) ** 0.5)
    dependency, _ = opening_dependency(Sentence(0.0, 0.0, sentence), None, 1.0, False)
    value *= 1.0 - 0.6 * dependency   # "So he did it." is a poor title
    if sentence.rstrip().endswith(("?", "!")):
        value *= 1.2
    if len(words) < 4 or len(words) > 22:
        value *= 0.6
    if len(sentence.strip()) > EXTRACTIVE_TITLE_MAX:  # would have to be cut to become a title
        value *= 0.7
    return value


def _drop_dangling_end(text: str) -> str:
    words = text.split()
    while len(words) > 3 and is_dangling(words[-1].strip(",;:")):
        words.pop()
    return " ".join(words)


def title_key(title: str) -> str:
    """Normalised form used to compare titles (case, punctuation and spacing insensitive)."""
    return _TITLE_KEY_RE.sub(" ", (title or "").casefold()).strip()


def _title_from(sentence: str) -> str:
    core = _LEADING_FILLER_RE.sub("", sentence.strip()).rstrip(".…").strip() or sentence.strip().rstrip(".…")
    if len(core) > EXTRACTIVE_TITLE_MAX:
        clause = re.split(r"[,;:—–]\s+", core)[0]
        core = clause if 12 <= len(clause) <= EXTRACTIVE_TITLE_MAX else truncate_words(core, EXTRACTIVE_TITLE_MAX)
        core = _drop_dangling_end(core)
    return title_case(core.strip(" ,;:-"))[:EXTRACTIVE_TITLE_MAX]


def _pick_hook(sentences: list[str], scores: dict[str, float]) -> str:
    punchy = [s for s in sentences if s.rstrip().endswith(("?", "!")) and len(tokens(s)) >= 3]
    if punchy:
        return max(punchy, key=lambda s: _sentence_score(s, scores))
    return sentences[0] if sentences else ""


def _hashtags_from(keywords: list[str], category: str | None, scores: dict[str, float], n: int) -> list[str]:
    """``n`` keyword hashtags (recurring keywords first) + at most one category tag."""
    recurring = [k for k in keywords if scores.get(k, 0.0) >= 2]
    chosen = (recurring + [k for k in keywords if k not in recurring])[:n]
    tags = [camel_case(k) for k in chosen] + CATEGORY_TAGS.get(category or "", [])[:1]
    seen: set[str] = set()
    return [t for t in tags if t and not (t.lower() in seen or seen.add(t.lower()))]


def _unique_title(options: list[str], avoid: set[str]) -> str:
    """First option whose normalised form is not in ``avoid``; else the first one numbered."""
    taken = {title_key(t) for t in avoid}
    for title in options:
        if title and title_key(title) not in taken:
            return title
    base = options[0] if options else "Video Highlight"
    k = 2
    while title_key(f"{base} (Part {k})") in taken:
        k += 1
    return truncate_words(base, EXTRACTIVE_TITLE_MAX - 9) + f" (Part {k})"


def _attribution_line(attribution: str | None) -> str:
    text = " ".join((attribution or "").split())
    if not text:
        return ""
    return text if text.lower().startswith(("credit", "source", "via ", "©", "original", "video by")) \
        else f"Credit: {text}"


def with_attribution(meta: ClipMetadata, attribution: str | None) -> ClipMetadata:
    """Append the credit line to the YouTube description and Instagram caption (limits kept)."""
    line = _attribution_line(attribution)
    if not line:
        return meta
    yt, ig = meta.youtube, meta.instagram
    room = YT_DESCRIPTION_MAX_BYTES - len(line.encode("utf-8")) - 2
    desc = yt.description.strip()
    if len(desc.encode("utf-8")) > room:
        prefix = desc.encode("utf-8")[:max(0, room - 3)].decode("utf-8", "ignore")
        desc = truncate_words(prefix, len(prefix), ellipsis="…")
    desc = f"{desc}\n\n{line}" if desc else line
    tags_len = len(hashtag_line(ig.hashtags)) + 2 if ig.hashtags else 0
    cap_room = IG_CAPTION_MAX - tags_len - len(line) - 2
    caption = ig.caption.strip()
    if len(caption) > cap_room:
        caption = truncate_words(caption, max(0, cap_room), ellipsis="…")
    caption = f"{caption}\n\n{line}" if caption else line
    return meta.model_copy(update={"youtube": yt.model_copy(update={"description": desc}),
                                   "instagram": ig.model_copy(update={"caption": caption})})


def extractive_metadata(*, transcript_text: str, source_title: str | None = None, category: str | None = None,
                        hook: str | None = None, avoid_titles: set[str] | None = None) -> ClipMetadata:
    """Build metadata purely from the clip transcript (and source title / category)."""
    text = " ".join((transcript_text or "").split())
    sentences = split_sentences(text)
    scores = dict(keyword_scores(text))       # the clip's own keywords decide the title
    ranked = sorted(sentences, key=lambda s: -_sentence_score(s, scores))
    options = [_title_from(s) for s in ranked]
    if not options:
        options = [truncate_words(source_title, EXTRACTIVE_TITLE_MAX)] if source_title else []
        options.append(CATEGORY_TITLES.get(category or "", "Video Highlight"))
    title = _unique_title(options, avoid_titles or set())
    hook_text = " ".join((hook or "").split()) or _pick_hook(sentences, scores)
    excerpt_source = " ".join(s for s in sentences if s != hook_text) or ""
    description = truncate_words(" ".join(p for p in (hook_text, excerpt_source) if p),
                                 EXTRACTIVE_DESCRIPTION_MAX, ellipsis="…")
    keywords = top_keywords(text, 12) if text else top_keywords(source_title or "", 6)
    tag_scores = dict(keyword_scores(text or source_title or ""))
    youtube = YouTubeMetadata(
        title=title, description=description, tags=keywords + CATEGORY_TAGS.get(category or "", []),
        hashtags=["Shorts"] + _hashtags_from(keywords, category, tag_scores, YT_KEYWORD_HASHTAGS)[:4], hook=hook_text,
    )
    caption_body = truncate_words(excerpt_source, 300, ellipsis="…")
    caption = "\n\n".join(p for p in (hook_text, caption_body, INSTAGRAM_CTA) if p)
    instagram = InstagramMetadata(caption=caption, hook=hook_text,
                                  hashtags=["Reels"] + _hashtags_from(keywords, category, tag_scores,
                                                                      IG_KEYWORD_HASHTAGS))
    return enforce_limits(ClipMetadata(youtube=youtube, instagram=instagram, source="extractive",
                                       generated_at=datetime.now(UTC).isoformat()))


# ------------------------------------------------------------------------ LLM
class _LLMYouTube(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]


class _LLMInstagram(BaseModel):
    model_config = ConfigDict(extra="ignore")
    caption: str
    hashtags: list[str]


class _LLMMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")
    youtube: _LLMYouTube
    instagram: _LLMInstagram
    hook: str


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}
METADATA_SCHEMA = _obj({
    "youtube": _obj({"title": _STR, "description": _STR, "tags": _STR_LIST, "hashtags": _STR_LIST}),
    "instagram": _obj({"caption": _STR, "hashtags": _STR_LIST}),
    "hook": _STR,
})

METADATA_SYSTEM = (
    "You write metadata for short vertical video clips published as YouTube Shorts and Instagram Reels. "
    "Base everything strictly on the clip transcript; never invent events, names, numbers or claims that "
    "are not in it, and never write clickbait that misrepresents the clip.\n"
    "YouTube: title at most 90 characters, engaging but accurate; description of 2-4 sentences; up to 15 "
    "relevant tags (450 characters in total at most); 3-5 hashtags including Shorts.\n"
    "Instagram: caption whose first line is the hook, then one or two short lines of context and a "
    "call to action; 8-15 relevant hashtags.\n"
    "hook: the clip's best opening line (ideally quoted from the transcript).\n"
    "Write hashtags without the # sign and without spaces. Use the transcript's language."
)


def llm_metadata(*, transcript_text: str, source_title: str | None, category: str | None,
                 hook: str | None) -> ClipMetadata:
    limit = get_settings().llm_max_transcript_chars
    prompt = "\n".join([
        f"Source video title: {source_title or '(unknown)'}",
        f"Detected moment type: {category or 'unknown'}",
        f"Suggested hook: {hook or '(none)'}",
        "",
        "Clip transcript:",
        transcript_text[:limit],
    ])
    data = llm_client.request_structured(system=METADATA_SYSTEM, prompt=prompt, schema=METADATA_SCHEMA,
                                         max_tokens=8000)
    parsed = _LLMMetadata.model_validate(data)
    meta = ClipMetadata(
        youtube=YouTubeMetadata(title=parsed.youtube.title, description=parsed.youtube.description,
                                tags=parsed.youtube.tags, hashtags=parsed.youtube.hashtags, hook=parsed.hook),
        instagram=InstagramMetadata(caption=parsed.instagram.caption, hashtags=parsed.instagram.hashtags,
                                    hook=parsed.hook),
        source="llm", generated_at=datetime.now(UTC).isoformat(),
    )
    return enforce_limits(meta)


_llm_metadata = llm_metadata   # backward-compatible name


def generate_clip_metadata(*, transcript_text: str, source_title: str | None = None,
                           category: str | None = None, hook: str | None = None,
                           use_llm: bool = True, attribution: str | None = None,
                           avoid_titles: set[str] | None = None) -> ClipMetadata:
    """Generate platform-specific metadata for one clip.

    Uses the configured metadata provider (Claude when ``METADATA_PROVIDER`` resolves to it,
    a key is set and ``use_llm``; ``source="llm"``); falls back to extractive metadata
    (``source="extractive"``) when it is unavailable or fails. ``avoid_titles``: titles already
    used by the source's other clips (never repeated). ``attribution``: credit line appended
    to the description and caption."""
    from app.modules.ai.registry import get_metadata_provider

    avoid = set(avoid_titles or ())
    meta: ClipMetadata | None = None
    provider = get_metadata_provider()
    if transcript_text.strip() and provider.active(use_llm):
        meta = provider.generate(transcript_text=transcript_text, source_title=source_title, category=category,
                                 hook=hook)
    extractive = extractive_metadata(transcript_text=transcript_text, source_title=source_title,
                                     category=category, hook=hook, avoid_titles=avoid)
    if meta is None:
        meta = extractive
    elif title_key(meta.youtube.title) in {title_key(t) for t in avoid}:
        meta = meta.model_copy(update={"youtube": meta.youtube.model_copy(update={"title": extractive.youtube.title})})
    meta = apply_viral_hashtags(meta, transcript_text=transcript_text, category=category, source_title=source_title)
    return with_attribution(meta, attribution)


def apply_viral_hashtags(meta: ClipMetadata, *, transcript_text: str, category: str | None,
                         source_title: str | None) -> ClipMetadata:
    """Replace the hashtags with the popular ones of the clip's niche (gaming, funny, podcast ...)."""
    from app.modules.metadata.viral_tags import detect_niche, viral_hashtags

    niche = detect_niche(transcript_text, category=category, source_title=source_title)
    youtube = meta.youtube.model_copy(update={"hashtags": viral_hashtags(niche, "youtube")})
    instagram = meta.instagram.model_copy(update={"hashtags": viral_hashtags(niche, "instagram")})
    return enforce_limits(meta.model_copy(update={"youtube": youtube, "instagram": instagram}))
