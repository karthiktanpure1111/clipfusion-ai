"""Scene-cut detection and visual activity on real concatenated test patterns."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.ffmpeg import ffmpeg_bin
from app.modules.analysis.scenes import detect_scenes
from app.modules.analysis.visual import analyze_visual_activity
from tests.helpers.media import _run


@pytest.fixture(scope="module")
def two_scenes(tmp_media_dir: Path) -> Path:
    out = tmp_media_dir / "two-scenes.mp4"
    _run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
          "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=3",
          "-f", "lavfi", "-i", "smptebars=size=640x360:rate=25:duration=3",
          "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
          "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])
    return out


def test_detects_the_cut_between_patterns(two_scenes: Path) -> None:
    seen: list[float] = []
    cuts = detect_scenes(two_scenes, progress=lambda f, m: seen.append(f))
    assert len(cuts) == 1
    assert cuts[0].time == pytest.approx(3.0, abs=0.15)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)


def test_visual_activity_motion_and_brightness(two_scenes: Path) -> None:
    activity = analyze_visual_activity(two_scenes, sample_hz=2.0)
    assert activity.sample_hz == 2.0
    assert len(activity.motion) == pytest.approx(12, abs=1) and len(activity.brightness) == len(activity.motion)
    assert activity.motion[0] == 0.0
    assert all(0.0 <= m <= 1.0 for m in activity.motion) and all(0.0 <= b <= 1.0 for b in activity.brightness)
    cut_index = max(range(len(activity.motion)), key=lambda i: activity.motion[i])
    assert cut_index / 2.0 == pytest.approx(3.0, abs=0.6)       # the hard cut is the biggest change
    assert activity.motion[cut_index] > 3 * sorted(activity.motion)[len(activity.motion) // 2]


def test_visual_activity_rejects_unreadable_input(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(b"\x00" * 1024)
    with pytest.raises(RuntimeError):
        analyze_visual_activity(bogus)


def test_chunked_detection_matches_single_pass(two_scenes: Path, monkeypatch) -> None:
    import app.modules.analysis.scenes as scenes

    monkeypatch.setattr(scenes, "CHUNK_SECONDS", 0.7)
    seen: list[float] = []
    cuts = scenes.detect_scenes(two_scenes, progress=lambda f, m: seen.append(f))
    assert [round(c.time, 1) for c in cuts] == [round(c.time, 1) for c in detect_scenes(two_scenes)]
    assert len(seen) >= 8
