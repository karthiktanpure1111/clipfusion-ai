"""Synthetic media generation for tests (real FFmpeg encodes, no mocks).

* :func:`make_test_video` — testsrc2 pattern + optional sine tone (with optional silent gaps).
* :func:`make_speech_wav` — real spoken English via the OS TTS engine (Windows SAPI through
  PowerShell, ``say`` on macOS, ``espeak``/``espeak-ng`` on Linux). Returns ``None`` when no
  TTS engine exists so tests can ``pytest.skip``.
* :func:`mux` — combine a video with an audio track.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from app.core.ffmpeg import ffmpeg_bin


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-1500:])


def make_test_video(path: Path, *, duration: float = 6.0, width: int = 1280, height: int = 720,
                    fps: int = 30, with_audio: bool = True, tone_hz: int = 440,
                    silent_ranges: list[tuple[float, float]] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={duration}"]
        if silent_ranges:
            expr = "+".join(f"between(t,{a},{b})" for a, b in silent_ranges)
            cmd += ["-af", f"volume=enable='{expr}':volume=0"]
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    _run(cmd)
    return path


def make_speech_wav(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    try:
        if system == "Windows":
            ps = shutil.which("powershell") or shutil.which("pwsh")
            if not ps:
                return None
            safe = text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile('{path}'); $s.Rate = 0; $s.Speak('{safe}'); $s.Dispose()"
            )
            _run([ps, "-NoProfile", "-NonInteractive", "-Command", script])
        elif system == "Darwin" and shutil.which("say"):
            aiff = path.with_suffix(".aiff")
            _run(["say", "-o", str(aiff), text])
            _run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(aiff), str(path)])
        else:
            tts = shutil.which("espeak-ng") or shutil.which("espeak")
            if not tts:
                return None
            _run([tts, "-w", str(path), text])
    except Exception:
        return None
    return path if path.exists() and path.stat().st_size > 1000 else None


def mux(video: Path, audio: Path, out: Path) -> Path:
    _run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(audio),
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out)])
    return out


def make_speech_video(path: Path, text: str, *, width: int = 1280, height: int = 720) -> Path | None:
    """A testsrc2 video whose audio is real TTS speech (duration follows the speech)."""
    wav = make_speech_wav(path.with_suffix(".speech.wav"), text)
    if wav is None:
        return None
    from app.modules.analysis.probe import probe_media  # provided by the analysis engine

    dur = probe_media(wav).duration
    silent = make_test_video(path.with_suffix(".silent.mp4"), duration=dur + 0.2, width=width, height=height,
                             with_audio=False)
    return mux(silent, wav, path)
