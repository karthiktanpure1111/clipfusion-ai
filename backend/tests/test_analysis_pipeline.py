"""End-to-end ``analyze_source`` on real media (TTS speech video + whisper tiny)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.modules.analysis.pipeline import STAGE_ORDER, SourceAnalysisResult, analyze_source
from app.schemas.media import AnalysisOptions, TranscriptData
from tests.helpers.media import make_speech_video, make_test_video
from tests.test_highlights_detector import build_transcript

SPEECH = (
    "Welcome back to the stream everyone. Today I want to tell you the craziest thing that happened to me. "
    "I was walking my dog in the park when a squirrel jumped on my head. No way, right? "
    "Everyone around me started laughing so hard. Haha, it was hilarious! "
    "So here is the lesson. Always look up when you walk under big trees. "
    "Thank you for watching, and let me know in the chat what you think."
)


def _collect() -> tuple[list[tuple[str, Any]], list[tuple[str, float]], Any, Any]:
    stages: list[tuple[str, Any]] = []
    progress: list[tuple[str, float]] = []
    return stages, progress, (lambda n, v: stages.append((n, v))), (lambda s, f, m: progress.append((s, f)))


def _check_progress(progress: list[tuple[str, float]]) -> None:
    values = [f for _, f in progress]
    assert values == sorted(values) and values[-1] == 1.0   # monotonic although stages overlap
    assert {stage for stage, _ in progress} <= set(STAGE_ORDER)
    assert progress[0][0] == "probe" and progress[-1][0] == "highlights"


def _check_timings(result: SourceAnalysisResult, expected: set[str]) -> None:
    assert set(result.stage_timings) == expected
    for t in result.stage_timings.values():
        assert t["started_at"] <= t["finished_at"] and t["seconds"] >= 0


@pytest.mark.slow
def test_analyze_tts_video_end_to_end(tmp_path: Path) -> None:
    video = make_speech_video(tmp_path / "talk.mp4", SPEECH, width=640, height=360)
    if video is None:
        pytest.skip("No text-to-speech engine available on this machine")
    stages, progress, on_stage, on_progress = _collect()
    workdir = tmp_path / "work"
    options = AnalysisOptions(clip_durations=[15.0], max_clips=3, whisper_model="tiny", use_llm=False)
    result = analyze_source(video, workdir, options, source_title="Park story", on_stage_result=on_stage,
                            progress=on_progress)
    assert isinstance(result, SourceAnalysisResult)
    names = [n for n, _ in stages]
    assert names[0] == "media" and sorted(names) == sorted(["media", "audio", "transcript", "scenes", "visual"])
    assert names.index("audio") < names.index("transcript") and names.index("scenes") < names.index("visual")
    _check_progress(progress)
    _check_timings(result, set(STAGE_ORDER))
    assert result.device in ("cuda", "cpu") and result.transcription_model == "tiny"
    assert result.mode == "balanced"
    assert result.media.has_audio and result.media.width == 640
    assert "squirrel" in result.transcript.text.lower() or "park" in result.transcript.text.lower()
    assert result.audio.rms_db and result.visual.motion
    assert result.highlights, "expected at least one highlight from 30+ s of speech"
    words = result.transcript.words()
    for c in result.highlights:
        assert c.method == "signals" and 0 <= c.score <= 100 and c.reasons
        assert 0 <= c.start < c.end <= result.media.duration
        assert not any(w.start + 1e-3 < t < w.end - 1e-3 for w in words for t in (c.start, c.end))
    assert not list(workdir.glob("*.wav")), "the analysis WAV must be cleaned up"


def test_existing_transcript_skips_transcription(tmp_path: Path, monkeypatch) -> None:
    import app.modules.transcription.service as service

    def _boom(*args: Any, **kwargs: Any) -> TranscriptData:
        raise AssertionError("transcribe must not run when a transcript is supplied")

    monkeypatch.setattr(service, "transcribe", _boom)
    video = make_test_video(tmp_path / "tone.mp4", duration=40.0, width=320, height=240, fps=10)
    lines = [(1.0 + 4.0 * i, "This sentence is part of a supplied transcript today.") for i in range(9)]
    transcript = build_transcript(lines)
    stages, progress, on_stage, on_progress = _collect()
    result = analyze_source(video, tmp_path / "w", AnalysisOptions(clip_durations=[15.0], use_llm=False),
                            existing_transcript=transcript, on_stage_result=on_stage, progress=on_progress)
    assert result.transcript is transcript
    assert dict(stages)["transcript"] is transcript
    _check_progress(progress)
    assert result.highlights and all(c.end <= 40.0 for c in result.highlights)
    assert result.warnings == []
    assert result.stage_timings["transcription"].get("skipped") is True and result.device is None


def test_saved_audio_and_video_analysis_are_reused(tmp_path: Path, monkeypatch) -> None:
    import app.modules.analysis.pipeline as pipeline

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("saved results must be reused")

    monkeypatch.setattr(pipeline, "analyze_video", _boom)
    monkeypatch.setattr(pipeline, "extract_audio_wav", _boom)
    video = make_test_video(tmp_path / "tone.mp4", duration=40.0, width=320, height=240, fps=10)
    transcript = build_transcript([(1.0 + 4.0 * i, "This sentence is part of a supplied transcript today.")
                                   for i in range(9)])
    audio = {"sample_hz": 10.0, "rms_db": [-20.0] * 400, "silences": [], "peak_times": [12.0]}
    visual = {"sample_hz": 1.0, "motion": [0.01] * 40, "brightness": [0.5] * 40}
    stages, progress, on_stage, on_progress = _collect()
    result = analyze_source(video, tmp_path / "w", AnalysisOptions(min_duration=10, max_duration=30,
                                                                   use_llm=False),
                            existing_transcript=transcript, existing_audio=audio, existing_scenes=[10.0, 25.5],
                            existing_visual=visual, on_stage_result=on_stage, progress=on_progress)
    assert [c.time for c in result.scenes] == [10.0, 25.5] and result.visual.motion == [0.01] * 40
    assert result.audio.peak_times == [12.0]
    assert all(result.stage_timings[s].get("skipped") for s in ("audio", "transcription", "video_analysis"))
    _check_progress(progress)
    assert result.highlights and all(10.0 - 1e-6 <= c.end - c.start <= 30.0 + 1e-6 for c in result.highlights)


def test_cancellation_from_the_progress_callback_stops_the_workers(tmp_path: Path) -> None:
    video = make_test_video(tmp_path / "tone.mp4", duration=30.0, width=320, height=240, fps=10)

    class Cancelled(Exception):
        pass

    def progress(stage: str, overall: float, message: str | None) -> None:
        if stage in ("audio", "video_analysis"):
            raise Cancelled()

    with pytest.raises(Cancelled):
        analyze_source(video, tmp_path / "w", AnalysisOptions(use_llm=False), progress=progress)
    assert not list((tmp_path / "w").glob("*.wav"))


def test_video_without_audio_reports_warning(tmp_path: Path) -> None:
    video = make_test_video(tmp_path / "silent.mp4", duration=12.0, width=320, height=240, fps=10, with_audio=False)
    result = analyze_source(video, tmp_path / "w", AnalysisOptions(clip_durations=[5.0], use_llm=False))
    assert not result.media.has_audio and result.transcript.segments == []
    assert result.audio.rms_db == [] and result.warnings
    assert all(c.method == "signals" for c in result.highlights)
