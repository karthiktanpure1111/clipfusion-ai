"""Clip metadata: extractive generation, platform limits and the LLM path (fake client)."""

from __future__ import annotations

from app.modules.metadata.generator import extractive_metadata, generate_clip_metadata
from app.modules.metadata.keywords import camel_case, title_case, truncate_words
from app.modules.metadata.limits import clean_tags, enforce_limits, sanitize_hashtag
from app.schemas.media import ClipMetadata, InstagramMetadata, YouTubeMetadata
from tests.test_highlights_llm import fake_message, llm_on  # noqa: F401 - shared fixture

TRANSCRIPT = (
    "Have you ever wondered why your sourdough bread never rises? "
    "The secret is the starter temperature. "
    "Keep your sourdough starter at twenty four degrees and feed it twice a day. "
    "After one week the bread doubles in size! "
    "That is the whole trick for perfect sourdough bread."
)


def test_extractive_metadata_is_grounded_and_within_limits() -> None:
    meta = extractive_metadata(transcript_text=TRANSCRIPT, source_title="Baking Basics Ep. 4",
                               category="explanation")
    assert meta.source == "extractive" and meta.generated_at
    yt, ig = meta.youtube, meta.instagram
    assert 0 < len(yt.title) <= 70 and yt.title[0].isupper()
    assert "Sourdough" in yt.title or "Bread" in yt.title
    assert yt.hook.endswith(("?", "!"))
    assert len(yt.description) <= 300 and yt.description.startswith(yt.hook)
    assert yt.hashtags[0] == "Shorts" and 3 <= len(yt.hashtags) <= 5
    assert "Sourdough" in yt.hashtags
    assert len(yt.tags) <= 15 and sum(len(t) for t in yt.tags) <= 450
    assert ig.hashtags[0] == "Reels" and len(ig.hashtags) <= 30
    assert ig.caption.splitlines()[0] == ig.hook
    for tag in yt.hashtags + ig.hashtags:
        assert "#" not in tag and " " not in tag
    assert len({t.lower() for t in ig.hashtags}) == len(ig.hashtags)


def test_extractive_without_transcript_is_honest() -> None:
    meta = extractive_metadata(transcript_text="", source_title=None, category="funny")
    assert meta.youtube.title == "Funny Moment" and meta.youtube.description == ""
    assert meta.youtube.hashtags[0] == "Shorts"
    meta = extractive_metadata(transcript_text="   ", source_title="My Great Stream Highlights")
    assert meta.youtube.title == "My Great Stream Highlights"


def test_limits_are_enforced() -> None:
    long = "word " * 3000
    meta = enforce_limits(ClipMetadata(
        youtube=YouTubeMetadata(title="<b>" + "Very long title " * 20, description=long + "<script>",
                                tags=[f"tag number {i}" for i in range(40)],
                                hashtags=["#gaming", "epic win", "Gaming", "123", "a#b", "x", "y", "z", "w"]),
        instagram=InstagramMetadata(caption=long, hashtags=[f"tag{i}" for i in range(50)]),
    ))
    yt, ig = meta.youtube, meta.instagram
    assert len(yt.title) <= 100 and "<" not in yt.title and ">" not in yt.title
    assert len(yt.description.encode()) <= 5000 and "<" not in yt.description
    assert len(yt.tags) <= 15 and sum(len(t) + 3 for t in yt.tags) <= 500
    assert yt.hashtags[0] == "Shorts" and len(yt.hashtags) <= 5
    assert "gaming" in [h.lower() for h in yt.hashtags] and "123" not in yt.hashtags
    assert len(ig.hashtags) == 30
    assert len(ig.caption) + 2 + len(" ".join(f"#{h}" for h in ig.hashtags)) <= 2200


def test_helpers() -> None:
    assert sanitize_hashtag("# Epic Win!") == "EpicWin" and sanitize_hashtag("#42") is None
    assert camel_case("world record") == "WorldRecord"
    assert title_case("the secret of the best bread") == "The Secret of the Best Bread"
    assert truncate_words("one two three four", 9) == "one two"
    assert clean_tags(["a,b", "A,B", "<x>"]) == ["a b", "x"]


LLM_META = {
    "youtube": {"title": "The Sourdough Starter Secret Nobody Tells You", "description": "Why bread fails. How to fix it.",
                "tags": ["sourdough", "bread", "baking"], "hashtags": ["#Sourdough", "Baking Tips", "Shorts"]},
    "instagram": {"caption": "Your sourdough never rises?\nKeep the starter at 24 degrees.\nSave this for later!",
                  "hashtags": ["sourdough", "baking", "bread", "homebaker", "breadmaking", "starter", "recipe",
                               "kitchen"]},
    "hook": "Have you ever wondered why your sourdough bread never rises?",
}


def test_llm_metadata_path(llm_on) -> None:  # noqa: F811
    client = llm_on([fake_message(LLM_META)])
    meta = generate_clip_metadata(transcript_text=TRANSCRIPT, source_title="Baking Basics", category="explanation")
    assert meta.source == "llm"
    assert meta.youtube.title == LLM_META["youtube"]["title"]
    # hashtags always come from the clip's niche (viral tags), whatever wrote the title
    assert meta.youtube.hashtags[0] == "Shorts" and len(meta.youtube.hashtags) <= 5
    assert meta.instagram.hook == LLM_META["hook"] and len(meta.instagram.hashtags) <= 8
    call = client.calls[0]
    assert call["fallbacks"] == "default" and "thinking" not in call
    assert call["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert TRANSCRIPT in call["messages"][0]["content"]


def test_llm_refusal_falls_back_to_extractive(llm_on) -> None:  # noqa: F811
    llm_on([fake_message({}, stop_reason="refusal")])
    meta = generate_clip_metadata(transcript_text=TRANSCRIPT)
    assert meta.source == "extractive" and meta.youtube.title


def test_llm_skipped_when_not_requested(llm_on) -> None:  # noqa: F811
    client = llm_on([fake_message(LLM_META)])
    assert generate_clip_metadata(transcript_text=TRANSCRIPT, use_llm=False).source == "extractive"
    assert not client.calls
