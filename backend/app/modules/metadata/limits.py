"""Platform limits and sanitisation for clip metadata (applied to LLM and extractive output).

YouTube: title <= 100 chars, description <= 5000 bytes, no ``<``/``>`` in either, tags
<= 15 and <= 450 characters in total (the API's limit is 500 including separators),
3-5 hashtags including ``Shorts``. Instagram: caption (plus the appended hashtag line)
<= 2200 characters, <= 30 hashtags.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.modules.metadata.keywords import truncate_words
from app.schemas.media import ClipMetadata, InstagramMetadata, YouTubeMetadata

YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX_BYTES = 5000
YT_MAX_TAGS = 15
YT_TAGS_TOTAL_CHARS = 450
YT_MAX_HASHTAGS = 5
IG_CAPTION_MAX = 2200
IG_MAX_HASHTAGS = 30
HASHTAG_MAX_CHARS = 60

_HASHTAG_STRIP_RE = re.compile(r"[^\w]", re.UNICODE)
_ANGLE_RE = re.compile(r"[<>]")


def sanitize_hashtag(tag: str) -> str | None:
    """Return the hashtag without ``#``, spaces or punctuation; ``None`` when unusable."""
    cleaned = _HASHTAG_STRIP_RE.sub("", (tag or "").replace("#", " ")).strip("_")[:HASHTAG_MAX_CHARS]
    if not cleaned or cleaned.isdigit():
        return None
    return cleaned


def clean_hashtags(tags: Iterable[str], limit: int) -> list[str]:
    """Sanitise, de-duplicate (case-insensitive) and cap a hashtag list."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        clean = sanitize_hashtag(tag)
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def ensure_hashtag(tags: list[str], required: str, limit: int) -> list[str]:
    """Make sure ``required`` is present (first), keeping at most ``limit`` tags."""
    rest = [t for t in tags if t.lower() != required.lower()]
    return [required] + rest[: max(0, limit - 1)]


def clean_tags(tags: Iterable[str]) -> list[str]:
    """YouTube tags: trimmed, no ``<>``/commas/``#``, de-duplicated, <= 15 and <= 450 chars total."""
    out: list[str] = []
    seen: set[str] = set()
    total = 0
    for tag in tags:
        clean = " ".join(_ANGLE_RE.sub("", str(tag)).replace(",", " ").replace("#", "").split())[:100]
        if not clean or clean.lower() in seen:
            continue
        cost = len(clean) + (2 if " " in clean else 0) + (1 if out else 0)
        if total + cost > YT_TAGS_TOTAL_CHARS:
            continue
        seen.add(clean.lower())
        out.append(clean)
        total += cost
        if len(out) >= YT_MAX_TAGS:
            break
    return out


def _clip_bytes(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    return truncate_words(data[:max_bytes].decode("utf-8", "ignore"), max_bytes)


def hashtag_line(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


def enforce_youtube(meta: YouTubeMetadata) -> YouTubeMetadata:
    """Apply YouTube's limits to ``meta`` (returns a new object)."""
    title = truncate_words(_ANGLE_RE.sub("", meta.title), YT_TITLE_MAX)
    description = _clip_bytes(_ANGLE_RE.sub("", meta.description).strip(), YT_DESCRIPTION_MAX_BYTES)
    hashtags = ensure_hashtag(clean_hashtags(meta.hashtags, 50), "Shorts", YT_MAX_HASHTAGS)
    return meta.model_copy(update={
        "title": title, "description": description, "tags": clean_tags(meta.tags), "hashtags": hashtags,
        "hook": " ".join(meta.hook.split())[:300],
    })


def enforce_instagram(meta: InstagramMetadata) -> InstagramMetadata:
    """Apply Instagram's limits; the caption leaves room for the appended hashtag line."""
    hashtags = clean_hashtags(meta.hashtags, IG_MAX_HASHTAGS)
    room = IG_CAPTION_MAX - (len(hashtag_line(hashtags)) + 2 if hashtags else 0)
    caption = meta.caption.strip()
    if len(caption) > room:
        caption = truncate_words(caption, room)
    return meta.model_copy(update={"caption": caption, "hashtags": hashtags,
                                   "hook": " ".join(meta.hook.split())[:300]})


def enforce_limits(meta: ClipMetadata) -> ClipMetadata:
    """Apply all platform limits to ``meta``."""
    return meta.model_copy(update={"youtube": enforce_youtube(meta.youtube),
                                   "instagram": enforce_instagram(meta.instagram)})
