"""Transcription with the real faster-whisper "tiny" model on real TTS speech."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.analysis.audio import extract_audio_wav
from app.modules.transcription.cleanup import clean_text, clean_word, ends_sentence
from app.modules.transcription.service import transcribe, whisper_available
from tests.helpers.media import make_speech_wav

SPEECH = ("Hello everyone and welcome back to the channel. Today we are testing the transcription engine. "
          "The weather is beautiful and the coffee is hot.")


def test_cleanup_is_light_and_deterministic() -> None:
    assert clean_text("  hello ,  i think   so .  this is great!!!!  ok") == "Hello, I think so. This is great!! Ok"
    assert clean_text("wait.... what") == "Wait... what"
    assert clean_text("bonjour i", language="fr") == "Bonjour i"
    assert clean_word(" i'm", sentence_start=False) == " I'm"
    assert clean_word(" so", sentence_start=True) == " So"
    assert ends_sentence(" done.\"") and not ends_sentence(" done,")


@pytest.mark.slow
def test_transcribes_real_speech_with_word_timestamps(tmp_path: Path) -> None:
    assert whisper_available()
    wav = make_speech_wav(tmp_path / "speech.wav", SPEECH)
    if wav is None:
        pytest.skip("No text-to-speech engine available on this machine")
    audio = extract_audio_wav(wav, tmp_path / "speech16k.wav")
    fractions: list[float] = []
    result = transcribe(audio, model_name="tiny", offset=10.0, progress=lambda f, m: fractions.append(f))
    assert result.model == "tiny" and result.language == "en"
    text = re.sub(r"[^a-z ]", "", result.text.lower())
    for word in ("welcome", "channel", "testing", "weather", "coffee"):
        assert word in text, result.text
    words = result.words()
    assert len(words) >= 15
    assert all(w.start >= 10.0 and w.end >= w.start for w in words)
    assert all(b.start >= a.start for a, b in zip(words, words[1:], strict=False))
    assert words[-1].end <= 10.0 + (result.duration or 0) + 0.5
    assert all(s.text == s.text.strip() and s.text[:1].isupper() for s in result.segments)
    assert fractions and fractions[-1] == 1.0


def test_long_audio_is_chunked_at_quiet_points(monkeypatch) -> None:
    import numpy as np

    import app.modules.transcription.service as service

    monkeypatch.setattr(service, "CHUNK_SECONDS", 10.0)
    monkeypatch.setattr(service, "SPLIT_SEARCH_SECONDS", 4.0)
    sr = service.SAMPLE_RATE
    pcm = (np.random.default_rng(0).standard_normal(35 * sr) * 3000).astype("<i2")
    pcm[int(7.5 * sr):int(7.7 * sr)] = 0          # quiet spot inside the first search window (6-10 s)
    bounds = service._chunk_bounds(pcm)
    assert bounds[0][0] == 0 and bounds[-1][1] == len(pcm)
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:], strict=False))
    assert 7.5 <= bounds[0][1] / sr <= 7.7
    assert all(b - a <= 14 * sr for a, b in bounds)
