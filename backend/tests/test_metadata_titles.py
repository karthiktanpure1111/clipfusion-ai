"""Metadata: per-clip titles, no duplicate titles within a source, few hashtags, credit line."""

from __future__ import annotations

from app.modules.metadata.generator import generate_clip_metadata, title_key, with_attribution
from app.modules.metadata.limits import IG_CAPTION_MAX, YT_DESCRIPTION_MAX_BYTES, hashtag_line
from app.schemas.media import ClipMetadata, InstagramMetadata, YouTubeMetadata

CLIP_A = ("The boss has three phases. In the first phase you should never attack from the front. "
          "You wait for the shield to drop, and then you strike twice.")
CLIP_B = ("So here is my honest opinion. Most beginners waste money on expensive gear. "
          "Your microphone matters far more than your camera. Fix your audio first.")


def test_titles_reflect_each_clip_and_are_unique() -> None:
    a = generate_clip_metadata(transcript_text=CLIP_A, source_title="Boss Fight Stream", use_llm=False)
    b = generate_clip_metadata(transcript_text=CLIP_B, source_title="Boss Fight Stream", use_llm=False)
    assert title_key(a.youtube.title) != title_key(b.youtube.title)
    assert any(w in a.youtube.title.lower() for w in ("phase", "boss", "shield", "attack"))
    assert any(w in b.youtube.title.lower() for w in ("microphone", "camera", "gear", "audio", "beginners"))
    assert not b.youtube.title.lower().startswith("so ")
    again = generate_clip_metadata(transcript_text=CLIP_A, source_title="Boss Fight Stream", use_llm=False,
                                   avoid_titles={a.youtube.title.upper()})
    assert title_key(again.youtube.title) != title_key(a.youtube.title) and again.youtube.title
    one_line = "Never attack from the front."
    first = generate_clip_metadata(transcript_text=one_line, use_llm=False)
    second = generate_clip_metadata(transcript_text=one_line, use_llm=False, avoid_titles={first.youtube.title})
    assert second.youtube.title.endswith("(Part 2)")


def test_hashtags_are_few_and_relevant() -> None:
    meta = generate_clip_metadata(transcript_text=CLIP_B, category="strong_statement", use_llm=False)
    yt, ig = meta.youtube.hashtags, meta.instagram.hashtags
    assert yt[0] == "Shorts" and 3 <= len(yt) <= 5
    assert len(ig) <= 8 and "reels" in ig
    # niche hashtags (a strong statement -> motivation), not random words from the transcript
    assert "Motivation" in yt and "motivation" in ig


def test_attribution_is_appended_and_survives_limits() -> None:
    meta = generate_clip_metadata(transcript_text=CLIP_A, use_llm=False, attribution="Streamer X – youtube.com/@x")
    assert meta.youtube.description.endswith("\n\nCredit: Streamer X – youtube.com/@x")
    assert meta.instagram.caption.endswith("Credit: Streamer X – youtube.com/@x")
    long = ClipMetadata(youtube=YouTubeMetadata(title="t", description="é" * 4000),
                        instagram=InstagramMetadata(caption="word " * 600, hashtags=["Reels", "Gaming"]))
    out = with_attribution(long, "Source: original stream by Y")
    assert out.youtube.description.endswith("Source: original stream by Y")
    assert len(out.youtube.description.encode("utf-8")) <= YT_DESCRIPTION_MAX_BYTES
    assert out.instagram.caption.endswith("Source: original stream by Y")
    assert len(out.instagram.caption) + len(hashtag_line(out.instagram.hashtags)) + 2 <= IG_CAPTION_MAX
    assert with_attribution(long, "  ") is long
