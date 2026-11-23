"""Clip proxies are real, seekable H.264 files in display orientation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.ffmpeg import ffmpeg_bin
from app.modules.analysis.probe import probe_media
from app.modules.clipping.proxy import make_clip_proxy
from tests.helpers.media import _run, make_test_video


@pytest.fixture(scope="module")
def source(tmp_media_dir: Path) -> Path:
    return make_test_video(tmp_media_dir / "proxy-src.mp4", duration=10.0, width=1280, height=720, fps=30)


def test_proxy_covers_clip_with_padding(source: Path, tmp_path: Path) -> None:
    dest = tmp_path / "proxy.mp4"
    proxy_start = make_clip_proxy(source, 3.0, 5.0, dest, pad=1.0, height=360)
    assert proxy_start == 2.0
    info = probe_media(dest)
    assert info.duration == pytest.approx(4.0, abs=0.15)
    assert (info.width, info.height) == (640, 360)
    assert info.video_codec == "h264" and info.audio_codec == "aac" and info.browser_playable
    assert dest.read_bytes()[:64].find(b"moov") != -1 or b"moov" in dest.read_bytes()[:4096]  # +faststart


def test_proxy_start_clamped_and_small_sources_not_upscaled(tmp_path: Path) -> None:
    small = make_test_video(tmp_path / "small.mp4", duration=4.0, width=320, height=240, fps=15, with_audio=False)
    dest = tmp_path / "p.mp4"
    assert make_clip_proxy(small, 0.5, 2.0, dest, pad=1.0) == 0.0
    info = probe_media(dest)
    assert (info.width, info.height) == (320, 240) and not info.has_audio
    assert info.duration == pytest.approx(3.0, abs=0.15)


def test_proxy_keeps_display_orientation(source: Path, tmp_path: Path) -> None:
    rotated = tmp_path / "rot.mp4"
    _run([ffmpeg_bin(), "-y", "-loglevel", "error", "-display_rotation", "90", "-i", str(source), "-c", "copy",
          str(rotated)])
    dest = tmp_path / "rot-proxy.mp4"
    make_clip_proxy(rotated, 1.0, 3.0, dest, height=720)
    info = probe_media(dest)
    assert info.height > info.width and info.height == 720 and info.rotation == 0


def test_invalid_range_rejected(source: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_clip_proxy(source, 5.0, 5.0, tmp_path / "x.mp4")
