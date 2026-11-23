"""Viral hashtags by niche (gaming, funny, podcast, reaction ...).

Each clip is classified into a niche from its transcript, its highlight category and the source
video's title; the niche's widely used hashtags are then used instead of words picked from the
transcript. Recognised games and creators (e.g. "Minecraft", "IShowSpeed") are added as topic tags.

These are curated, evergreen high-reach tags for each niche, not live trending data (no free official
API provides that). They are plain lists so they are easy to adjust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# YouTube: "Shorts" first, then the niche's best tags (5 max in total; YouTube ignores all hashtags
# when a video has more than 15, and shows the first 3 above the title).
# Instagram: up to 8 (Instagram recommends a handful of relevant tags over long lists).
NICHE_TAGS: dict[str, dict[str, list[str]]] = {
    "gaming": {"youtube": ["Gaming", "GamingShorts", "Gamer", "Viral"],
               "instagram": ["gaming", "gamer", "gamingclips", "gamingreels", "gameplay", "viral", "reels"]},
    "funny": {"youtube": ["Funny", "Comedy", "FunnyShorts", "Viral"],
              "instagram": ["funny", "comedy", "funnyreels", "memes", "lol", "viral", "reels"]},
    "reaction": {"youtube": ["Reaction", "Reacts", "Viral", "Trending"],
                 "instagram": ["reaction", "reactionvideo", "reacts", "funny", "viral", "reels", "trending"]},
    "streamer": {"youtube": ["Streamer", "StreamHighlights", "Viral", "Trending"],
                 "instagram": ["streamer", "streamhighlights", "livestream", "twitchclips", "viral", "reels",
                               "trending"]},
    "podcast": {"youtube": ["Podcast", "PodcastClips", "Viral", "Trending"],
                "instagram": ["podcast", "podcastclips", "podcastlife", "conversation", "viral", "reels",
                              "trending"]},
    "motivation": {"youtube": ["Motivation", "Mindset", "Success", "Viral"],
                   "instagram": ["motivation", "mindset", "success", "inspiration", "motivationalquotes",
                                 "viral", "reels"]},
    "education": {"youtube": ["Facts", "Learn", "Knowledge", "Viral"],
                  "instagram": ["facts", "learning", "knowledge", "didyouknow", "education", "viral", "reels"]},
    "story": {"youtube": ["Story", "StoryTime", "Emotional", "Viral"],
              "instagram": ["storytime", "story", "emotional", "reallife", "viral", "reels", "trending"]},
    "general": {"youtube": ["Viral", "Trending", "ShortsFeed", "Fyp"],
                "instagram": ["viral", "trending", "reels", "explore", "fyp", "reelsinstagram", "instagood"]},
}

_WORD = re.compile(r"[a-z0-9'ऀ-ॿ]+")  # Latin + Devanagari (Hindi / Hinglish)

# Hindi cue words (Devanagari), merged into _CUES below
_HINDI_CUES: dict[str, set[str]] = {
    "gaming": {"गेम", "गेमिंग", "लेवल", "किल", "बॉस", "मैच", "खेल", "खेलो", "जीत", "हार"},
    "funny": {"हाहा", "हंसी", "हँसी", "मज़ाक", "मजाक", "मज़ा", "मजा", "पागल", "कॉमेडी", "जोक"},
    "reaction": {"अरे", "ओह", "क्या", "यार", "भाई", "बाप", "देखो", "सच"},
    "streamer": {"चैट", "स्ट्रीम", "लाइव", "सब्सक्राइब", "डोनेशन"},
    "podcast": {"पॉडकास्ट", "एपिसोड", "बातचीत", "मेहमान", "इंटरव्यू"},
    "motivation": {"सफलता", "मेहनत", "पैसा", "पैसे", "सपना", "सपने", "लक्ष्य", "ज़िंदगी", "जिंदगी"},
    "education": {"सीखो", "सीखना", "मतलब", "कारण", "जानकारी", "तथ्य"},
    "story": {"कहानी", "पहले", "साल", "याद", "माँ", "पापा", "परिवार"},
}

# niche -> cue words / phrases found in the transcript or the source title
_CUES: dict[str, set[str]] = {
    "gaming": {"game", "games", "gaming", "gamer", "gameplay", "level", "boss", "clutch", "kill", "kills", "killed",
               "headshot", "gg", "respawn", "loot", "weapon", "sniper", "squad", "lobby", "rank", "ranked", "match",
               "victory", "noob", "pro", "speedrun", "minecraft", "fortnite", "valorant", "roblox", "pubg", "bgmi",
               "gta", "warzone", "apex", "fifa", "controller", "console", "xbox", "playstation", "nintendo"},
    "funny": {"haha", "hahaha", "lol", "lmao", "funny", "hilarious", "joke", "jokes", "laugh", "laughing",
              "prank", "dumb", "crazy", "bro", "meme", "memes", "cringe"},
    "reaction": {"react", "reacts", "reacting", "reaction", "reactions", "omg", "what", "no", "way", "wow"},
    "streamer": {"stream", "streaming", "streamer", "chat", "donation", "donations", "subscribe", "subs", "live",
                 "viewers", "twitch", "kick", "raid", "followers"},
    "podcast": {"podcast", "episode", "guest", "interview", "conversation", "host", "show", "talk", "discuss"},
    "motivation": {"success", "successful", "discipline", "mindset", "goal", "goals", "dream", "dreams", "hustle",
                   "money", "rich", "consistency", "consistent", "focus", "motivation", "quit", "failure", "grind"},
    "education": {"learn", "explain", "explained", "fact", "facts", "science", "history", "because", "means",
                  "reason", "how", "why", "tip", "tips", "secret", "lesson", "understand"},
    "story": {"story", "years", "ago", "remember", "happened", "once", "mom", "dad", "family", "cried", "life"},
}
for _niche, _words in _HINDI_CUES.items():
    _CUES[_niche] |= _words

_CATEGORY_NICHE = {
    "gaming_achievement": "gaming", "funny": "funny", "reaction": "reaction", "audience_interaction": "streamer",
    "conversation": "podcast", "strong_statement": "motivation", "explanation": "education", "emotional": "story",
    "surprising": "reaction", "exciting": "gaming",
}
# niche detected from the source title is a strong signal (e.g. "... Podcast Ep 12", "X Reacts To ...")
_TITLE_NICHE = [
    (re.compile(r"\breact(s|ing|ion)?\b", re.I), "reaction"),
    (re.compile(r"\bpodcast|\bep(isode)?\.?\s*\d+|\binterview\b", re.I), "podcast"),
    (re.compile(r"\bgameplay|\bgaming\b|\blet'?s play\b|\bminecraft|\bfortnite|\bvalorant|\broblox|\bgta\b|\bbgmi\b|"
                r"\bpubg\b|\bfree\s*fire\b", re.I), "gaming"),
    (re.compile(r"\bstream\b|\blive\b|\bhighlights?\b", re.I), "streamer"),
    (re.compile(r"\bfunny|\bcomedy|\bprank|\bmemes?\b", re.I), "funny"),
    (re.compile(r"\bmotivation|\bmindset|\bsuccess\b", re.I), "motivation"),
]
# well-known games and creators -> hashtag (topic tags help discovery more than generic ones)
_TOPICS: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.I), tag) for p, tag in (
    (r"\bminecraft\b", "Minecraft"), (r"\bfortnite\b", "Fortnite"), (r"\bvalorant\b", "Valorant"),
    (r"\broblox\b", "Roblox"), (r"\bgta\s*(5|v|6|vi)?\b", "GTA"), (r"\bbgmi\b", "BGMI"), (r"\bpubg\b", "PUBG"),
    (r"\bfree\s*fire\b", "FreeFire"), (r"\bwarzone\b", "Warzone"), (r"\bcall of duty\b|\bcod\b", "CallOfDuty"),
    (r"\bapex\b", "ApexLegends"), (r"\bfifa\b|\bea fc\b", "FIFA"), (r"\bclash royale\b", "ClashRoyale"),
    (r"\bishow\s*speed\b|\bspeed\b(?= reacts)", "IShowSpeed"), (r"\bmr\.?\s*beast\b", "MrBeast"),
    (r"\bkai\s*cenat\b", "KaiCenat"), (r"\bpewdiepie\b", "PewDiePie"), (r"\bcarry\s*minati\b", "CarryMinati"),
    (r"\btechno\s*gamerz\b", "TechnoGamerz"), (r"\btotal\s*gaming\b", "TotalGaming"),
    (r"\btriggered\s*insaan\b", "TriggeredInsaan"), (r"\bsamay\s*raina\b", "SamayRaina"),
    (r"\branveer\b|\bbeerbiceps\b", "BeerBiceps"), (r"\bjoe rogan\b", "JoeRogan"),
)]


@dataclass(frozen=True)
class NicheResult:
    niche: str
    topics: list[str]


def detect_niche(text: str, *, category: str | None = None, source_title: str | None = None) -> NicheResult:
    """Classify a clip into a niche and pick topic tags (games/creators) it mentions."""
    words = _WORD.findall((text or "").lower())
    counts: dict[str, float] = {n: 0.0 for n in _CUES}
    for w in words:
        for niche, cues in _CUES.items():
            if w in cues:
                counts[niche] += 1.0
    total = max(1, len(words))
    scores = {n: c / total * 100 for n, c in counts.items()}      # cue hits per 100 words
    if category in _CATEGORY_NICHE:
        scores[_CATEGORY_NICHE[category]] = scores.get(_CATEGORY_NICHE[category], 0.0) + 2.0
    title = source_title or ""
    for pattern, niche in _TITLE_NICHE:
        if pattern.search(title):
            scores[niche] = scores.get(niche, 0.0) + 4.0
            break
    best = max(scores, key=lambda n: scores[n]) if scores else "general"
    niche = best if scores.get(best, 0.0) >= 2.0 else "general"
    haystack = f"{title} {text or ''}"
    topics = [tag for pattern, tag in _TOPICS if pattern.search(haystack)][:2]
    return NicheResult(niche=niche, topics=topics)


def viral_hashtags(result: NicheResult, platform: str) -> list[str]:
    """Hashtags (without '#') for ``platform`` ("youtube" | "instagram")."""
    tags = NICHE_TAGS[result.niche][platform]
    if platform == "youtube":
        out = ["Shorts", *result.topics, *tags]
        return _dedupe(out)[:5]
    out = [t.lower() for t in result.topics] + tags
    return _dedupe(out)[:8]


def _dedupe(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    return [t for t in tags if not (t.lower() in seen or seen.add(t.lower()))]
