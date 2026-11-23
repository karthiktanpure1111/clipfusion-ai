"""Highlight detection on a deterministic synthetic transcript + real audio analysis."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from app.modules.analysis.audio import analyze_audio
from app.modules.clipping.boundaries import temporal_iou
from app.modules.highlights.detector import detect_highlights
from app.modules.highlights.sentences import build_sentences
from app.schemas.media import (
    AnalysisOptions,
    AudioAnalysis,
    MediaInfo,
    RetentionPoint,
    SceneCut,
    TranscriptData,
    TranscriptSegment,
    TranscriptWord,
    VisualActivity,
)

DURATION = 240.0
MOMENT = (120.0, 131.0)
FILLER = "We are talking about the weekly update for the project today."
MOMENT_LINES = [
    "Wait, what just happened?",
    "No way, that was the craziest clutch I have ever seen!",
    "Haha that is hilarious!",
    "Let's go chat, thank you for the donation!",
]


def build_transcript(lines: list[tuple[float, str]], word_dur: float = 0.3, gap: float = 0.05) -> TranscriptData:
    segments = []
    for i, (t0, text) in enumerate(lines):
        words, t = [], t0
        for tok in text.split():
            words.append(TranscriptWord(start=round(t, 3), end=round(t + word_dur, 3), word=" " + tok))
            t += word_dur + gap
        segments.append(TranscriptSegment(id=i, start=words[0].start, end=words[-1].end, text=text, words=words))
    return TranscriptData(language="en", model="synthetic", duration=DURATION, segments=segments)


def synthetic_lines() -> list[tuple[float, str]]:
    lines: list[tuple[float, str]] = []
    t = 1.0
    while t < DURATION - 5:
        if MOMENT[0] <= t < MOMENT[1]:
            for text in MOMENT_LINES:
                lines.append((t, text))
                t += len(text.split()) * 0.35 + 0.4
            t = max(t, MOMENT[1])
            continue
        if 200.0 <= t < 210.0:          # 10 s of silence without speech
            t = 210.0
            continue
        lines.append((t, FILLER))
        t += 4.2
    return lines


def write_wav(path: Path, duration: float, *, base_db: float = -28.0,
              loud: list[tuple[float, float]] = (), silent: list[tuple[float, float]] = (),
              sr: int = 16000, seed: int = 7) -> Path:
    rng = np.random.default_rng(seed)
    n = int(duration * sr)
    signal = rng.standard_normal(n) * (10 ** (base_db / 20) * 32768)
    for a, b in loud:
        signal[int(a * sr):int(b * sr)] *= 5.0          # ~ +14 dB
    for a, b in silent:
        signal[int(a * sr):int(b * sr)] = 0.0
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.clip(signal, -32768, 32767).astype("<i2").tobytes())
    return path


@pytest.fixture(scope="module")
def signals(tmp_path_factory) -> dict:
    wav = write_wav(tmp_path_factory.mktemp("hl") / "audio.wav", DURATION,
                    loud=[(121.0, 121.6), (124.5, 125.1), (127.5, 128.0)], silent=[(200.0, 210.0)])
    motion = [0.02] * int(DURATION)
    for i in range(120, 131):
        motion[i] = 0.2
    return {
        "transcript": build_transcript(synthetic_lines()),
        "audio": analyze_audio(wav),
        "scenes": [SceneCut(time=t) for t in (60.0, 122.0, 125.0, 180.0)],
        "visual": VisualActivity(sample_hz=1.0, motion=motion, brightness=[0.5] * int(DURATION)),
        "media": MediaInfo(duration=DURATION, width=1280, height=720, fps=30.0, has_audio=True),
    }


def _inside_word(t: float, transcript: TranscriptData) -> bool:
    return any(w.start + 1e-3 < t < w.end - 1e-3 for w in transcript.words())


def test_audio_signals_are_real(signals) -> None:
    audio: AudioAnalysis = signals["audio"]
    assert any(120.5 <= p <= 128.5 for p in audio.peak_times)
    assert any(a <= 201 and b >= 209 for a, b in audio.silences)


def test_detects_the_reaction_moment(signals) -> None:
    options = AnalysisOptions(clip_durations=[30.0], max_clips=5, use_llm=False)
    progress: list[float] = []
    cands = detect_highlights(**signals, options=options, progress=lambda f, m: progress.append(f))
    assert 1 <= len(cands) <= 5
    assert [c.score for c in cands] == sorted((c.score for c in cands), reverse=True)
    top = cands[0]
    overlap = min(top.end, MOMENT[1]) - max(top.start, MOMENT[0])
    assert overlap >= 5.0, (top.start, top.end)
    assert top.category in {"funny", "surprising", "gaming_achievement", "exciting", "reaction",
                            "audience_interaction"}
    assert top.method == "signals" and top.retention_score is None
    assert 1 <= len(top.reasons) <= 3
    assert any(("Loud reaction at 2:0" in r and "dB above average" in r) or "at 2:0" in r for r in top.reasons)
    assert top.transcript and top.hook
    assert abs(sum(top.breakdown.values()) - top.score) < 0.1
    assert "retention" not in top.breakdown
    assert progress and progress[-1] == 1.0


def test_candidates_are_natural_and_non_overlapping(signals) -> None:
    options = AnalysisOptions(clip_durations=[15.0, 30.0], max_clips=8, max_overlap=0.25, use_llm=False)
    cands = detect_highlights(**signals, options=options)
    sentences = build_sentences(signals["transcript"])
    starts = {round(s.start, 3) for s in sentences}
    ends = {round(s.end, 3) for s in sentences}
    for c in cands:
        assert 0 <= c.start < c.end <= DURATION
        assert 0 <= c.score <= 100
        assert c.end - c.start <= 1.25 * c.target_duration + 0.01
        assert not _inside_word(c.start, signals["transcript"]) and not _inside_word(c.end, signals["transcript"])
        assert any(abs(c.start + 0.15 - s) < 0.02 or abs(c.start - s) < 0.02 for s in starts)
        assert any(abs(c.end - 0.15 - e) < 0.02 or abs(c.end - e) < 0.02 for e in ends)
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            assert temporal_iou(a.start, a.end, b.start, b.end) <= 0.25
            assert abs(a.start - b.start) >= 2.0


def test_min_score_and_max_clips(signals) -> None:
    few = detect_highlights(**signals, options=AnalysisOptions(clip_durations=[15.0], max_clips=2, use_llm=False))
    assert len(few) <= 2
    strict = detect_highlights(**signals, options=AnalysisOptions(clip_durations=[15.0], min_score=99.5,
                                                                  use_llm=False))
    assert all(c.score >= 99.5 for c in strict)


def test_real_retention_is_used_and_reported_separately(signals) -> None:
    retention = [RetentionPoint(ratio=i / 100, audience_watch_ratio=0.9 if 48 <= i <= 56 else 0.4)
                 for i in range(101)]
    options = AnalysisOptions(clip_durations=[30.0], max_clips=4, use_llm=False, retention=retention)
    cands = detect_highlights(**signals, options=options)
    assert cands
    for c in cands:
        assert c.retention_score is not None and 0.0 <= c.retention_score <= 1.0
        assert "retention" in c.breakdown


def test_sources_without_speech_use_audio_and_visual_signals(signals, tmp_path) -> None:
    wav = write_wav(tmp_path / "game.wav", 180.0, base_db=-30.0, loud=[(95.0, 96.0)])
    motion = [0.01] * 180
    for i in range(90, 100):
        motion[i] = 0.3
    cands = detect_highlights(
        transcript=TranscriptData(model="none", segments=[]), audio=analyze_audio(wav),
        scenes=[SceneCut(time=t) for t in (92.0, 94.0, 97.0)],
        visual=VisualActivity(sample_hz=1.0, motion=motion, brightness=[0.4] * 180),
        media=MediaInfo(duration=180.0, width=1920, height=1080, fps=60.0, has_audio=True),
        options=AnalysisOptions(clip_durations=[20.0], max_clips=3, use_llm=False),
    )
    assert cands
    top = cands[0]
    assert top.start <= 95.0 <= top.end
    assert top.method == "signals" and top.category in {"exciting", "other", "reaction"}
    assert "speech" not in top.breakdown and "lexical" not in top.breakdown
    assert any("Loud reaction" in r or "scene changes" in r or "motion" in r for r in top.reasons)
