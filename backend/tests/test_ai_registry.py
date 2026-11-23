"""Provider registry: free local defaults, env selection, fallbacks and one-file plug-ins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.modules.ai.registry as registry
from app.modules.ai.base import HighlightRanker, MetadataProvider, TranscriptionProvider
from app.modules.analysis.pipeline import analyze_source
from app.schemas.media import AnalysisOptions, TranscriptData
from tests.helpers.media import make_test_video
from tests.test_highlights_detector import build_transcript


@pytest.fixture()
def env(monkeypatch):
    settings = registry.ProviderSettings()

    def set_(**kw: str) -> None:
        for k, v in kw.items():
            monkeypatch.setattr(settings, k, v)

    monkeypatch.setattr(registry, "provider_settings", lambda: settings)
    return set_


def test_defaults_are_free_and_local(env) -> None:
    env()
    assert isinstance(registry.get_transcription_provider(), TranscriptionProvider)
    assert registry.get_transcription_provider().name == "faster_whisper"
    ranker, meta = registry.get_highlight_ranker(), registry.get_metadata_provider()
    assert isinstance(ranker, HighlightRanker) and isinstance(meta, MetadataProvider)
    assert (ranker.name, meta.name) == ("signals", "extractive")   # no ANTHROPIC_API_KEY in tests
    assert not ranker.active(True) and not meta.active(True)


def test_auto_uses_claude_only_with_a_key(env, llm_on) -> None:  # noqa: F811
    llm_on([])
    env()
    assert registry.get_highlight_ranker().name == "claude" and registry.get_highlight_ranker().active(True)
    assert not registry.get_highlight_ranker().active(False)          # per-request use_llm still applies
    env(highlight_ranker="signals", metadata_provider="extractive")
    assert registry.describe() == {"transcription": "faster_whisper", "highlight_ranker": "signals",
                                   "metadata": "extractive"}


def test_unknown_provider_falls_back(env) -> None:
    env(transcription_provider="nope", highlight_ranker="nope", metadata_provider="nope")
    assert registry.describe() == {"transcription": "faster_whisper", "highlight_ranker": "signals",
                                   "metadata": "extractive"}


def test_a_plugin_provider_is_used_by_the_pipeline(env, tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class EchoTranscriber:
        name = "echo"

        def available(self) -> bool:
            return True

        def transcribe(self, audio: Path, **kw: Any) -> TranscriptData:
            calls.append(kw)
            kw["on_plan"](type("Plan", (), {"device": "cpu", "model": "echo-1"})())
            lines = [(1.0 + 4.0 * i, "This sentence comes from the plug-in transcriber today.") for i in range(6)]
            return build_transcript(lines).model_copy(update={"model": "echo-1"})

    monkeypatch.setitem(registry._TRANSCRIPTION, "echo", EchoTranscriber)
    env(transcription_provider="echo")
    video = make_test_video(tmp_path / "tone.mp4", duration=30.0, width=320, height=240, fps=10)
    result = analyze_source(video, tmp_path / "w", AnalysisOptions(mode="fast", min_duration=8, use_llm=False))
    assert calls and calls[0]["mode"] == "fast"
    assert result.transcript.model == "echo-1" and result.transcription_model == "echo-1" and result.device == "cpu"


from tests.test_highlights_llm import llm_on  # noqa: E402,F401 - shared fixture
