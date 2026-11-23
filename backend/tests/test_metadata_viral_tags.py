"""Niche-based viral hashtags."""

from app.modules.metadata.generator import generate_clip_metadata
from app.modules.metadata.viral_tags import detect_niche, viral_hashtags


def test_gaming_clip_gets_gaming_tags():
    r = detect_niche("He is so low, clutch! That boss fight in Minecraft was insane, gg", category="gaming_achievement")
    assert r.niche == "gaming"
    yt = viral_hashtags(r, "youtube")
    assert yt[0] == "Shorts" and "Minecraft" in yt and "Gaming" in yt and len(yt) <= 5
    ig = viral_hashtags(r, "instagram")
    assert "gaming" in ig and len(ig) <= 8


def test_reaction_from_source_title_and_creator_topic():
    r = detect_niche("what is this, no way", source_title="iShowSpeed-Reacts-To-Cursed-Tree")
    assert r.niche == "reaction" and "IShowSpeed" in r.topics


def test_podcast_and_funny_and_general():
    assert detect_niche("In this episode our guest talks about money and the show", category="conversation").niche \
        == "podcast"
    assert detect_niche("haha that joke was hilarious lol", category="funny").niche == "funny"
    assert detect_niche("The weather today is fine.").niche == "general"


def test_generated_metadata_uses_viral_tags_not_transcript_words():
    meta = generate_clip_metadata(transcript_text="Everyone worked hard. We beat the final boss in the game, clutch!",
                                  category="gaming_achievement", use_llm=False)
    assert meta.youtube.hashtags[0] == "Shorts" and "Gaming" in meta.youtube.hashtags
    assert "Everyone" not in meta.youtube.hashtags and "Worked" not in meta.youtube.hashtags
    assert "gaming" in meta.instagram.hashtags


def test_hindi_cues_are_understood():
    assert detect_niche("भाई ये गेम का लेवल बहुत कठिन है, बॉस को किल करो").niche == "gaming"
    assert detect_niche("हाहा यार ये तो पागल है, बहुत मज़ा आया").niche == "funny"
