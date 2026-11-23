"""Browser-friendly per-clip proxy videos for the framing / subtitle editors.

The proxy covers the clip plus ``pad`` seconds on each side, keeps the source's *display*
orientation (FFmpeg auto-rotates), is at most ``height`` px tall and is encoded as
H.264/AAC MP4 with ``+faststart`` and a short GOP so the editors can seek quickly. NVENC is
used when FFmpeg can (:func:`app.core.gpu.video_encoder_args`), libx264 otherwise or when the
NVENC encode fails at runtime.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core import gpu
from app.core.ffmpeg import ffmpeg_bin, run

log = logging.getLogger(__name__)

PROXY_TIMEOUT_S = 3600
PROXY_CRF = 23
PROXY_GOP = 30


def _has_audio(source: Path) -> bool:
    from app.modules.analysis.probe import probe_media

    try:
        return probe_media(source).has_audio
    except Exception:  # noqa: BLE001 - let FFmpeg report unreadable inputs
        return True


def _command(source: Path, dest: Path, proxy_start: float, length: float, h: int, has_audio: bool,
             encoder_args: list[str]) -> list[str]:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-ss", f"{proxy_start:.3f}", "-i", str(source), "-t", f"{length:.3f}",
           "-map", "0:v:0", "-vf", f"scale=-2:'min({h},ih)':flags=bicubic,format=yuv420p", *encoder_args]
    if has_audio:
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    else:
        cmd += ["-an"]
    return cmd + ["-movflags", "+faststart", "-sn", "-dn", str(dest)]


def make_clip_proxy(source: Path, start: float, end: float, dest: Path, *, pad: float = 1.0,
                    height: int = 720, preset: str = "veryfast") -> float:
    """Encode the proxy for ``[start, end]`` (source seconds) to ``dest``.

    Returns ``proxy_start``: the source time shown at proxy ``t = 0`` (``max(0, start - pad)``);
    editors convert with ``source_t = proxy_start + proxy_t``."""
    if end <= start:
        raise ValueError(f"Invalid clip range: start={start:.3f}s end={end:.3f}s")
    source, dest = Path(source), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    proxy_start = max(0.0, float(start) - max(0.0, pad))
    length = float(end) - proxy_start + max(0.0, pad)
    h = max(2, int(height) - int(height) % 2)
    has_audio = _has_audio(source)
    encoder, args = gpu.video_encoder_args(crf=PROXY_CRF, preset=preset, gop=PROXY_GOP)
    try:
        run(_command(source, dest, proxy_start, length, h, has_audio, args), timeout=PROXY_TIMEOUT_S)
    except RuntimeError as exc:
        if encoder != "h264_nvenc":
            raise
        gpu.mark_nvenc_broken(str(exc))
        _, args = gpu.video_encoder_args(crf=PROXY_CRF, preset=preset, gop=PROXY_GOP, prefer_gpu=False)
        run(_command(source, dest, proxy_start, length, h, has_audio, args), timeout=PROXY_TIMEOUT_S)
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"Proxy encoding produced no output for {source.name}")
    log.debug("proxy %s: %.2f-%.2f (proxy_start=%.3f, %s)", dest.name, start, end, proxy_start, encoder)
    return round(proxy_start, 3)
