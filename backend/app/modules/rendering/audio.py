"""Audio track of a render: kept source ranges (+ intro/outro) -> one AAC track.

Every :class:`AudioPiece` lasts *exactly* ``duration`` seconds (the duration of the video
frames it accompanies): its kept ranges are cut with ``atrim`` and concatenated, then padded
or trimmed to ``duration``. Pieces without audio become digital silence. The concatenated
track is optionally loudness-normalised (EBU R128 ``loudnorm``, TP -1.5 dBTP, LRA 11) and
encoded as AAC at 48 kHz stereo.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ffmpeg import ffmpeg_bin, run

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
AUDIO_TIMEOUT_S = 3600
_FMT = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"


@dataclass
class AudioPiece:
    """``path`` = media file with an audio stream (None = silence). ``ranges`` are seconds in
    the file's own timeline; ``duration`` is the exact output length of the piece."""

    duration: float
    path: Path | None = None
    ranges: list[tuple[float, float]] = field(default_factory=list)


def _source_chain(idx: int, label: str, piece: AudioPiece, base: float) -> list[str]:
    """Filters for one input: cut the ranges (relative to the input seek ``base``), concat."""
    chains = [f"[{idx}:a:0]{_FMT},asetpts=PTS-STARTPTS"]
    n = len(piece.ranges)
    if n == 1:
        a, b = piece.ranges[0]
        chains[0] += f",atrim=start={a - base:.6f}:end={b - base:.6f},asetpts=PTS-STARTPTS[{label}c]"
    else:
        chains[0] += f",asplit={n}" + "".join(f"[{label}s{j}]" for j in range(n))
        for j, (a, b) in enumerate(piece.ranges):
            chains.append(f"[{label}s{j}]atrim=start={a - base:.6f}:end={b - base:.6f},asetpts=PTS-STARTPTS"
                          f"[{label}r{j}]")
        chains.append("".join(f"[{label}r{j}]" for j in range(n)) + f"concat=n={n}:v=0:a=1[{label}c]")
    chains.append(f"[{label}c]apad=whole_dur={piece.duration:.6f},atrim=duration={piece.duration:.6f},"
                  f"asetpts=PTS-STARTPTS[{label}]")
    return chains


def _silence_chain(label: str, duration: float) -> list[str]:
    return [f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE},{_FMT},"
            f"atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[{label}]"]


def build_audio_command(pieces: list[AudioPiece], dest: Path, *, normalize: bool, target_lufs: float,
                        bitrate_kbps: int) -> list[str]:
    """FFmpeg command producing ``dest`` (AAC in MP4/M4A) from ``pieces``."""
    if not pieces:
        raise ValueError("no audio pieces")
    inputs: list[str] = []
    graph: list[str] = []
    labels: list[str] = []
    n_inputs = 0
    for i, piece in enumerate(pieces):
        label = f"p{i}"
        labels.append(f"[{label}]")
        if piece.path is None or not piece.ranges:
            graph += _silence_chain(label, piece.duration)
            continue
        base = max(0.0, min(a for a, _ in piece.ranges) - 0.5)
        end = max(b for _, b in piece.ranges)
        inputs += ["-ss", f"{base:.6f}", "-t", f"{end - base + 0.5:.6f}", "-i", str(piece.path)]
        graph += _source_chain(n_inputs, label, piece, base)
        n_inputs += 1
    tail = "".join(labels) + (f"concat=n={len(labels)}:v=0:a=1" if len(labels) > 1 else "anull")
    if normalize and n_inputs > 0:
        tail += f",loudnorm=I={target_lufs:g}:TP=-1.5:LRA=11,{_FMT}"
    graph.append(tail + "[aout]")
    return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs,
            "-filter_complex", ";".join(graph), "-map", "[aout]", "-c:a", "aac", "-b:a", f"{bitrate_kbps}k",
            "-ar", str(SAMPLE_RATE), "-ac", "2", str(dest)]


def build_audio_track(pieces: list[AudioPiece], dest: Path, *, normalize: bool, target_lufs: float,
                      bitrate_kbps: int, cancel: threading.Event | None = None) -> Path:
    """Render the audio track for ``pieces`` to ``dest``. Raises RuntimeError on failure,
    timeout or when ``cancel`` is set (FFmpeg is killed)."""
    cmd = build_audio_command(pieces, dest, normalize=normalize, target_lufs=target_lufs,
                              bitrate_kbps=bitrate_kbps)
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + AUDIO_TIMEOUT_S
    while True:
        try:
            _, err = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if (cancel is not None and cancel.is_set()) or time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise RuntimeError("audio rendering cancelled or timed out") from None
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", "replace")[-1500:]
        raise RuntimeError(f"audio rendering failed ({proc.returncode}): {tail}")
    return dest


def mux(video: Path, audio: Path, dest: Path) -> Path:
    """Combine the encoded video and audio streams without re-encoding (+faststart)."""
    run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(video), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-movflags", "+faststart", str(dest)],
        timeout=AUDIO_TIMEOUT_S)
    return dest
