"""Real test media for the vision/framing engine (owned by the framing engine tests).

Public-domain NASA portraits are downloaded once and cached under ``tests/assets/``:

* ``astronaut.png`` — Eileen Collins (NASA, public domain; scikit-image sample data)
* ``armstrong.jpg`` — Neil Armstrong (NASA, public domain; Wikimedia Commons)

Synthetic videos are real FFmpeg encodes composed from those photos:

* :func:`make_streamer_video` — testsrc2 "gameplay" 1920x1080 with a small bordered facecam
  in the bottom-right corner.
* :func:`make_podcast_video` — one person moving horizontally across a 1920x1080 frame.
* :func:`make_two_person_video` — two people far apart.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import httpx
import numpy as np

from app.core.ffmpeg import ffmpeg_bin

ASSETS = Path(__file__).resolve().parents[1] / "assets"
_SOURCES: dict[str, tuple[str, ...]] = {
    "astronaut.png": (
        "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.19.3/skimage/data/astronaut.png",
        "https://commons.wikimedia.org/wiki/Special:FilePath/Eileen_Collins.jpg?width=800",
    ),
    "armstrong.jpg": (
        "https://commons.wikimedia.org/wiki/Special:FilePath/Neil_Armstrong_pose.jpg?width=800",
        "https://commons.wikimedia.org/wiki/Special:FilePath/Buzz_Aldrin.jpg?width=800",
    ),
}
# head-and-shoulders crops (normalised x1, y1, x2, y2) chosen around the detected faces
_CROPS = {"astronaut.png": (0.13, 0.0, 0.75, 0.62), "armstrong.jpg": (0.36, 0.06, 0.86, 0.62)}


def face_photo(name: str = "astronaut.png") -> Path | None:
    """Cached public-domain portrait, downloaded on first use. None when offline."""
    path = ASSETS / name
    if path.is_file() and path.stat().st_size > 10_000:
        return path
    ASSETS.mkdir(parents=True, exist_ok=True)
    for url in _SOURCES[name]:
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=30,
                             headers={"User-Agent": "ClipFusionTests/1.0 (test asset fetch)"})
            resp.raise_for_status()
            if cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR) is None:
                continue
            path.write_bytes(resp.content)
            return path
        except Exception:  # noqa: BLE001 - try the next mirror
            continue
    return None


def head_and_shoulders(name: str, dest: Path, height: int) -> Path:
    """Crop the portrait around the person and scale it to ``height`` px (PNG)."""
    src = face_photo(name)
    if src is None:
        raise RuntimeError(f"test photo {name} unavailable")
    img = cv2.imread(str(src))
    h, w = img.shape[:2]
    x1, y1, x2, y2 = _CROPS[name]
    crop = img[int(y1 * h):int(y2 * h), int(x1 * w):int(x2 * w)]
    scale = height / crop.shape[0]
    crop = cv2.resize(crop, (int(crop.shape[1] * scale) // 2 * 2, height), interpolation=cv2.INTER_AREA)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dest), crop)
    return dest


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-1500:])


_ENC = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "30"]


def make_streamer_video(path: Path, *, duration: float = 8.0, cam_w: int = 400, margin: int = 40,
                        border: int = 8) -> Path:
    """testsrc2 gameplay with a bordered facecam (4:3) in the bottom-right corner."""
    cam = head_and_shoulders("astronaut.png", path.with_suffix(".cam.png"), height=int(cam_w * 0.75))
    vf = (f"[1:v]scale={cam_w}:{int(cam_w * 0.75)},pad=w=iw+{2 * border}:h=ih+{2 * border}:"
          f"x={border}:y={border}:color=white[cam];"
          f"[0:v][cam]overlay=x=W-w-{margin}:y=H-h-{margin}:shortest=1[v]")
    _ffmpeg(["-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={duration}",
             "-loop", "1", "-i", str(cam), "-filter_complex", vf, "-map", "[v]", "-t", str(duration),
             *_ENC, str(path)])
    return path


def make_podcast_video(path: Path, *, duration: float = 10.0, speed_px: float = 110.0,
                       person_h: int = 560) -> Path:
    """One person (head and shoulders) sliding left-to-right over a static backdrop."""
    person = head_and_shoulders("astronaut.png", path.with_suffix(".person.png"), height=person_h)
    vf = (f"[0:v]drawbox=x=0:y=700:w=1920:h=380:color=0x3a2f28:t=fill[bg];"
          f"[bg][1:v]overlay=x='200+{speed_px}*t':y=H-h-60:shortest=1[v]")
    _ffmpeg(["-f", "lavfi", "-i", f"color=c=0x24303c:size=1920x1080:rate=30:duration={duration}",
             "-loop", "1", "-i", str(person), "-filter_complex", vf, "-map", "[v]", "-t", str(duration),
             *_ENC, str(path)])
    return path


def make_two_person_video(path: Path, *, duration: float = 8.0, person_h: int = 520) -> Path:
    """Two different people, one near each side of a 1920x1080 frame."""
    a = head_and_shoulders("astronaut.png", path.with_suffix(".a.png"), height=person_h)
    b = head_and_shoulders("armstrong.jpg", path.with_suffix(".b.png"), height=person_h)
    # gentle sway (people are not pasted rectangles: their edges must not be temporally stable)
    vf = ("[0:v][1:v]overlay=x='120+24*sin(2.1*t)':y='H-h-80+10*sin(1.3*t)'[t];"
          "[t][2:v]overlay=x='W-w-120+24*sin(1.7*t+1)':y='H-h-80+10*sin(1.1*t)':shortest=1[v]")
    _ffmpeg(["-f", "lavfi", "-i", f"color=c=0x2b2b33:size=1920x1080:rate=30:duration={duration}",
             "-loop", "1", "-i", str(a), "-loop", "1", "-i", str(b), "-filter_complex", vf,
             "-map", "[v]", "-t", str(duration), *_ENC, str(path)])
    return path


# ------------------------------------------------------------------ cached analyses
_CACHE: dict[str, tuple[Path, object, object]] = {}
_BUILDERS = {"streamer": make_streamer_video, "podcast": make_podcast_video, "two": make_two_person_video}


def analysed(kind: str, media_dir: Path):
    """``(video_path, MediaInfo, ClipVisionAnalysis)`` for a synthetic video, built and analysed
    once per test session (shared by the vision/framing test modules)."""
    if kind not in _CACHE:
        from app.modules.analysis.probe import probe_media
        from app.modules.tracking.tracker import analyze_clip_vision

        path = _BUILDERS[kind](media_dir / f"vision-{kind}.mp4")
        media = probe_media(path)
        _CACHE[kind] = (path, media, analyze_clip_vision(path, 0.0, media.duration, media))
    return _CACHE[kind]
