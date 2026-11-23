"""Render-speed optimisations must not change the output: hidden-background skip, frame
buffer reuse, large pipes, NVENC with libx264 fallback, and zero caption work without subtitles."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import app.core.gpu as gpu
import app.modules.rendering.renderer as renderer
from app.modules.analysis.probe import probe_media
from app.modules.framing.layout import compile_layout, default_framing_config
from app.modules.rendering.compositor import Compositor
from app.modules.rendering.video_io import FrameReader
from app.schemas.render import RenderOptions, RenderSpec
from tests.helpers.media import make_test_video


@pytest.fixture(scope="module")
def clip(tmp_media_dir: Path) -> Path:
    return make_test_video(tmp_media_dir / "speed-src.mp4", duration=3.0, width=640, height=360, fps=30)


def test_hidden_background_skip_is_bit_identical() -> None:
    layout = compile_layout(default_framing_config(1920, 1080, "facecam_gameplay"), 1920, 1080)
    comp = Compositor(layout, 640, 360)
    assert comp.background_hidden
    frame = np.random.default_rng(3).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    fast = comp.compose(frame, 0.5)
    comp.background_hidden = False
    assert np.array_equal(fast, comp.compose(frame, 0.5))
    full = compile_layout(default_framing_config(1920, 1080, "full_frame"), 1920, 1080)
    assert not Compositor(full, 640, 360).background_hidden          # letterboxed: background visible


def test_buffer_reuse_yields_the_same_frames(clip: Path) -> None:
    def frames(reuse: bool) -> list[np.ndarray]:
        with FrameReader(clip, start=0.5, frames=40, fps=Fraction(30), width=320, height=180,
                         reuse_buffers=reuse) as reader:
            return [f.copy() for f in reader]

    plain, reused = frames(False), frames(True)
    assert len(plain) == len(reused) == 40
    assert all(np.array_equal(a, b) for a, b in zip(plain, reused, strict=True))


def _spec(clip: Path, out: Path, **options) -> RenderSpec:
    media = probe_media(clip)
    layout = compile_layout(default_framing_config(media.width, media.height, "full_frame"), media.width,
                            media.height)
    return RenderSpec(source_path=clip, start=0.5, end=2.5, media=media, layout=layout, subtitles_enabled=False,
                      options=RenderOptions(**options), output_path=out)


def test_nvenc_failure_falls_back_to_libx264(clip: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(gpu, "_NVENC_STATE", {"ok": True})
    real = gpu.video_encoder_args

    def broken(**kw):
        name, args = real(**kw)
        if name == "h264_nvenc":     # an option NVENC rejects -> FFmpeg exits with an error
            args = args + ["-rc-lookahead", "-5"]
        return name, args

    monkeypatch.setattr(gpu, "video_encoder_args", broken)
    result = renderer.render_clip(_spec(clip, tmp_path / "out.mp4"))
    assert result.output_path.exists() and result.duration == pytest.approx(2.0, abs=0.1)
    assert gpu._NVENC_STATE["ok"] is False


def test_no_caption_work_without_subtitles(clip: Path, tmp_path: Path, monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError("caption code must not run when subtitles are disabled")

    monkeypatch.setattr(renderer, "CaptionTrack", boom)
    result = renderer.render_clip(_spec(clip, tmp_path / "plain.mp4", encoder="libx264"))
    assert result.width == 1080 and result.height == 1920
    info = probe_media(result.output_path)
    assert info.video_codec == "h264" and info.audio_codec == "aac" and info.has_audio
