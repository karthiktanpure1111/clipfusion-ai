"""One-pass video analysis (scene cuts + motion + brightness from a single decode) and modes."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.core.ffmpeg import ffmpeg_bin
from app.modules.analysis.modes import MODES, clip_targets, get_profile, transcription_model
from app.modules.analysis.video_analysis import analyze_video, detect_cuts, summarise
from app.schemas.media import AnalysisOptions
from tests.helpers.media import _run


@pytest.fixture(scope="module")
def three_scenes(tmp_media_dir: Path) -> Path:
    """testsrc2 (moving) 3 s | smptebars (static) 3 s | mandelbrot (zooming) 4 s."""
    out = tmp_media_dir / "three-scenes.mp4"
    _run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
          "-f", "lavfi", "-i", "smptebars=size=640x360:rate=25:duration=3",
          "-f", "lavfi", "-i", "mandelbrot=size=640x360:rate=25",
          "-filter_complex", "[2:v]trim=duration=4,setpts=PTS-STARTPTS[m];[0:v][1:v][m]concat=n=3:v=1:a=0[v]",
          "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])
    return out


@pytest.mark.parametrize("fps", [5.0, 10.0, 15.0])
def test_detects_hard_cuts_in_one_pass(three_scenes: Path, fps: float) -> None:
    seen: list[float] = []
    res = analyze_video(three_scenes, fps=fps, duration=10.0, progress=lambda f, m: seen.append(f))
    assert [round(c.time) for c in res.scenes] == [3, 6]
    assert all(abs(c.time - t) <= 1.0 / fps + 1e-6 for c, t in zip(res.scenes, (3.0, 6.0), strict=True))
    assert all(0.3 <= (c.score or 0) <= 1.0 for c in res.scenes)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)
    v = res.visual
    assert v.sample_hz == 1.0 and len(v.motion) == len(v.brightness) == 10
    assert all(0 <= m <= 1 for m in v.motion) and all(0 <= b <= 1 for b in v.brightness)
    assert max(v.motion[3:6]) < 0.005 < min(v.motion[0:3])     # static bars vs moving patterns


def test_unreadable_input_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(b"\x00" * 1024)
    with pytest.raises(RuntimeError):
        analyze_video(bogus)


def test_progress_exception_stops_decoding(three_scenes: Path) -> None:
    class Stop(Exception):
        pass

    def progress(f: float, m: str | None) -> None:
        if f > 0:
            raise Stop()

    with pytest.raises(Stop):
        analyze_video(three_scenes, fps=25.0, progress=progress)


def test_adaptive_threshold_ignores_steady_motion() -> None:
    rng = np.random.default_rng(1)
    scores = np.clip(rng.normal(0.35, 0.03, 200), 0, 1)     # constant fast motion (no cut)
    scores[120] = 0.95                                        # one real cut
    assert [i for i, _ in detect_cuts(scores, 10.0)] == [120]
    flat = np.full(50, 0.01)
    flat[[10, 12]] = 0.9                                      # two cuts closer than MIN_SCENE_SECONDS
    assert len(detect_cuts(flat, 10.0)) == 1


def test_summarise_buckets_and_excludes_cut_pairs() -> None:
    n = 40     # 4 s at 10 fps
    diffs = np.full(n, 0.01)
    hists = np.full(n, 0.02)
    diffs[25], hists[25] = 0.5, 0.9
    res = summarise(diffs, hists, np.full(n, 127.5), 10.0)
    assert [c.time for c in res.scenes] == [2.5]
    assert len(res.visual.motion) == 4 and res.visual.motion[2] == pytest.approx(0.01)   # cut pair excluded
    assert res.visual.brightness == [0.5] * 4


def test_mode_profiles_and_targets() -> None:
    assert set(MODES) == {"fast", "balanced", "best"} and get_profile(None).name == "balanced"
    assert get_profile("unknown").name == "balanced"
    fast, best = MODES["fast"], MODES["best"]
    assert fast.video_fps < MODES["balanced"].video_fps < best.video_fps
    assert fast.candidate_depth < best.candidate_depth and fast.face_sample_fps < best.face_sample_fps
    opts = AnalysisOptions(min_duration=20, max_duration=60)
    targets = clip_targets(opts)
    assert len(targets) == 3 and all(20 <= t <= 60 for t in targets)
    explicit = AnalysisOptions(min_duration=20, max_duration=60, clip_durations=[15, 30, 90])
    assert clip_targets(explicit) == [20.0, 30.0, 60.0]       # clamped to the hard limits
    assert clip_targets(AnalysisOptions(min_duration=30, max_duration=32)) == [31.0]
    assert transcription_model(AnalysisOptions(whisper_model="tiny"), "cuda") == "tiny"
    assert transcription_model(AnalysisOptions(mode="best"), "cuda") == best.gpu_model
    assert transcription_model(AnalysisOptions(mode="best"), "cpu") == best.cpu_model
