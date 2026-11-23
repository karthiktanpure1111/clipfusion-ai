"""Media/analysis domain models shared by the analysis engine, workers and API.

All times are seconds. Source times are relative to the original video; clip times are
relative to the clip start (``clip_t = source_t - clip.start_time``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------- probe
class MediaInfo(_Model):
    duration: float
    width: int            # display width (rotation applied)
    height: int           # display height (rotation applied)
    fps: float
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None
    container: str | None = None
    rotation: int = 0
    bit_rate: int | None = None
    sample_rate: int | None = None
    size_bytes: int | None = None
    browser_playable: bool = False  # h264/vp9/av1 in mp4/webm/mov -> playable without a proxy


# -------------------------------------------------------------- transcription
class TranscriptWord(_Model):
    start: float
    end: float
    word: str              # as emitted by the ASR, may include leading space/punctuation
    probability: float = 1.0


class TranscriptSegment(_Model):
    id: int
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


class TranscriptData(_Model):
    language: str | None = None
    language_probability: float | None = None
    model: str
    duration: float | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def words(self) -> list[TranscriptWord]:
        return [w for s in self.segments for w in s.words]


# ---------------------------------------------------------------- audio/visual
class AudioAnalysis(_Model):
    sample_hz: float = 10.0                     # values below are sampled at this rate
    rms_db: list[float] = Field(default_factory=list)       # loudness envelope (dBFS)
    silences: list[tuple[float, float]] = Field(default_factory=list)  # [start, end] in source seconds
    speech_ratio: float | None = None           # fraction of non-silent time
    mean_db: float | None = None
    peak_times: list[float] = Field(default_factory=list)   # loudness spikes (reactions, hype)
    laughter_like: list[tuple[float, float]] = Field(default_factory=list)  # heuristic, may be empty


class VisualActivity(_Model):
    sample_hz: float = 1.0
    motion: list[float] = Field(default_factory=list)       # 0..1 normalised frame-difference energy
    brightness: list[float] = Field(default_factory=list)


class SceneCut(_Model):
    time: float
    score: float | None = None


# ------------------------------------------------------------------- highlights
HighlightCategory = Literal[
    "funny", "exciting", "strong_statement", "emotional", "explanation", "surprising",
    "gaming_achievement", "reaction", "audience_interaction", "conversation", "other",
]


class HighlightCandidate(_Model):
    start: float
    end: float
    score: float = Field(ge=0, le=100)          # AI highlight score (estimate, NOT viewership)
    category: HighlightCategory = "other"
    reasons: list[str] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(default_factory=dict)  # signal -> contribution (0..100 scale)
    transcript: str = ""
    hook: str | None = None
    target_duration: float | None = None
    method: Literal["signals", "signals+llm", "manual"] = "signals"
    retention_score: float | None = None        # mean REAL audience watch ratio (YouTube Analytics) if available


class RetentionPoint(_Model):
    ratio: float                                # elapsedVideoTimeRatio 0..1
    audience_watch_ratio: float
    relative_performance: float | None = None


ProcessingMode = Literal["fast", "balanced", "best"]


class AnalysisOptions(_Model):
    mode: ProcessingMode = "balanced"           # speed/quality trade-off (models, sampling rates, search depth)
    min_duration: float = Field(default=20.0, ge=5, le=180)   # hard limits for every clip
    max_duration: float = Field(default=60.0, ge=5, le=180)
    clip_durations: list[float] = Field(default_factory=lambda: [30.0, 60.0])  # targets (15/30/45/60/90/custom)
    max_clips: int = Field(default=12, ge=1, le=100)
    min_score: float = 0.0
    language: str | None = None                 # None = auto-detect
    whisper_model: str | None = None            # None = settings.whisper_model
    use_llm: bool = True
    max_overlap: float = 0.25                   # max IoU allowed between returned clips
    retention: list[RetentionPoint] | None = None


# ----------------------------------------------------------------- metadata
class YouTubeMetadata(_Model):
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)  # without '#'
    hook: str = ""
    privacy: Literal["public", "unlisted", "private"] = "public"
    made_for_kids: bool = False


class InstagramMetadata(_Model):
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    hook: str = ""
    share_to_feed: bool = True


class ClipMetadata(_Model):
    youtube: YouTubeMetadata = Field(default_factory=YouTubeMetadata)
    instagram: InstagramMetadata = Field(default_factory=InstagramMetadata)
    source: Literal["llm", "extractive", "user"] = "extractive"
    generated_at: str | None = None
