"""Audio extraction + loudness analysis on real encoded media."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.modules.analysis.audio import analyze_audio, extract_audio_wav
from tests.helpers.media import make_test_video
from tests.test_highlights_detector import write_wav


@pytest.fixture(scope="module")
def gapped(tmp_media_dir: Path) -> Path:
    return make_test_video(tmp_media_dir / "audio-gaps.mp4", duration=8.0, width=320, height=240, fps=10,
                           silent_ranges=[(2.0, 3.5), (5.0, 6.0)])


def test_extract_audio_wav_format(gapped: Path, tmp_path: Path) -> None:
    wav = extract_audio_wav(gapped, tmp_path / "a.wav")
    with wave.open(str(wav)) as wf:
        assert (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) == (1, 16000, 2)
        assert wf.getnframes() / 16000 == pytest.approx(8.0, abs=0.1)
    part = extract_audio_wav(gapped, tmp_path / "part.wav", start=2.0, end=5.0)
    with wave.open(str(part)) as wf:
        assert wf.getnframes() / 16000 == pytest.approx(3.0, abs=0.05)


def test_silences_on_tone_with_gaps(gapped: Path, tmp_path: Path) -> None:
    result = analyze_audio(extract_audio_wav(gapped, tmp_path / "a.wav"))
    assert result.sample_hz == 10.0 and len(result.rms_db) == pytest.approx(80, abs=2)
    assert len(result.silences) == 2
    (a1, b1), (a2, b2) = result.silences
    assert a1 == pytest.approx(2.0, abs=0.2) and b1 == pytest.approx(3.5, abs=0.2)
    assert a2 == pytest.approx(5.0, abs=0.2) and b2 == pytest.approx(6.0, abs=0.2)
    assert result.speech_ratio == pytest.approx(5.5 / 8.0, abs=0.05)
    assert result.mean_db is not None and result.mean_db > -30
    assert result.peak_times == []          # a constant tone has no loudness spikes
    assert result.laughter_like == []


def test_offset_shifts_all_times(gapped: Path, tmp_path: Path) -> None:
    wav = extract_audio_wav(gapped, tmp_path / "a.wav")
    base, shifted = analyze_audio(wav), analyze_audio(wav, offset=100.0)
    flat = [t + 100 for pair in base.silences for t in pair]
    assert flat == pytest.approx([t for pair in shifted.silences for t in pair])


def test_peaks_are_local_maxima_two_seconds_apart(tmp_path: Path) -> None:
    wav = write_wav(tmp_path / "bursts.wav", 12.0, base_db=-30.0,
                    loud=[(3.0, 3.3), (3.6, 3.9), (8.0, 8.4)])
    result = analyze_audio(wav, offset=10.0)
    assert len(result.peak_times) == 2
    assert result.peak_times[0] == pytest.approx(13.4, abs=0.6)
    assert result.peak_times[1] == pytest.approx(18.2, abs=0.4)
    assert result.silences == [] and result.speech_ratio == 1.0
