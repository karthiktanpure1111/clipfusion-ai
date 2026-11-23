"""Probing, validation and frame extraction on real FFmpeg-generated media."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationFailed
from app.core.ffmpeg import ffmpeg_bin
from app.modules.analysis.probe import extract_frame, probe_media, sniff_container, validate_video_file
from tests.helpers.media import _run, make_test_video


@pytest.fixture(scope="module")
def video(tmp_media_dir: Path) -> Path:
    return make_test_video(tmp_media_dir / "probe-basic.mp4", duration=4.0, width=640, height=360, fps=25)


def test_probe_landscape_video(video: Path) -> None:
    info = probe_media(video)
    assert (info.width, info.height, info.rotation) == (640, 360, 0)
    assert info.fps == pytest.approx(25.0, abs=0.01)
    assert info.duration == pytest.approx(4.0, abs=0.15)
    assert info.has_audio and info.audio_codec == "aac" and info.video_codec == "h264"
    assert info.container == "mp4" and info.browser_playable
    assert info.size_bytes == video.stat().st_size and info.sample_rate == 48000


def test_probe_rotated_video_swaps_dimensions(video: Path, tmp_path: Path) -> None:
    rotated = tmp_path / "rotated.mp4"
    _run([ffmpeg_bin(), "-y", "-loglevel", "error", "-display_rotation", "90", "-i", str(video), "-c", "copy",
          str(rotated)])
    info = probe_media(rotated)
    assert info.rotation in (90, 270)
    assert (info.width, info.height) == (360, 640)


def test_probe_audio_only_and_non_browser_formats(tmp_path: Path) -> None:
    wav = tmp_path / "tone.wav"
    _run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
          str(wav)])
    info = probe_media(wav)
    assert (info.width, info.height, info.fps) == (0, 0, 0.0)
    assert info.has_audio and not info.browser_playable
    mkv = tmp_path / "clip.mkv"
    _run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=1",
          "-c:v", "mpeg4", str(mkv)])
    info = probe_media(mkv)
    assert info.container == "mkv" and not info.browser_playable and not info.has_audio


def test_validate_accepts_real_video(video: Path) -> None:
    assert validate_video_file(video).width == 640
    assert sniff_container(video) == "isobmff"


def test_validate_rejects_text_renamed_to_mp4(tmp_path: Path) -> None:
    fake = tmp_path / "notes.mp4"
    fake.write_text("this is definitely not a video file " * 20)
    with pytest.raises(ValidationFailed) as exc:
        validate_video_file(fake)
    assert exc.value.code == "unsupported_format"


def test_validate_rejects_truncated_file(video: Path, tmp_path: Path) -> None:
    data = video.read_bytes()
    cut = tmp_path / "truncated.mp4"
    cut.write_bytes(data[: len(data) // 2])
    with pytest.raises(ValidationFailed) as exc:
        validate_video_file(cut)
    assert exc.value.code == "invalid_media"


def test_validate_rejects_audio_only_mp4_and_empty_file(tmp_path: Path) -> None:
    m4a = tmp_path / "voice.mp4"
    _run([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac", str(m4a)])
    with pytest.raises(ValidationFailed) as exc:
        validate_video_file(m4a)
    assert exc.value.code == "no_video_stream"
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ValidationFailed):
        validate_video_file(empty)


def test_validate_rejects_too_long_video(video: Path, monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_video_duration_seconds", 2)
    with pytest.raises(ValidationFailed) as exc:
        validate_video_file(video)
    assert exc.value.code == "invalid_duration"


def test_extract_frame_writes_jpeg(video: Path, tmp_path: Path) -> None:
    dest = extract_frame(video, 1.5, tmp_path / "thumbs" / "frame.jpg", width=321)
    data = dest.read_bytes()
    assert data[:3] == b"\xff\xd8\xff"
    from PIL import Image

    with Image.open(dest) as img:
        assert img.width == 320
