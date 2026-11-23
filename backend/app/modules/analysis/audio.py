"""Audio extraction and loudness analysis.

``extract_audio_wav`` decodes the (first) audio stream to 16 kHz mono PCM, the format both
the loudness analysis and faster-whisper consume. ``analyze_audio`` computes a 10 Hz RMS
loudness envelope (dBFS) and derives silences, loudness peaks (reactions, hype moments),
the fraction of non-silent time and the mean active loudness.

``AudioAnalysis.laughter_like`` is intentionally left empty: no validated laughter
classifier ships with the product and we do not fake one. Laughter is detected from the
transcript (laughter markers) by the highlight detector instead.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path

import numpy as np

from app.core.ffmpeg import ffmpeg_bin, run
from app.schemas.media import AudioAnalysis

log = logging.getLogger(__name__)

SAMPLE_HZ = 10.0                 # envelope rate (100 ms windows)
DB_FLOOR = -100.0                # value used for digital silence
SILENCE_ABS_DB = -45.0           # silence threshold never lower than this
SILENCE_REL_DB = 18.0            # ... or median - 18 dB, whichever is higher
MIN_SILENCE_S = 0.4
PEAK_REL_DB = 9.0                # peaks must exceed median + 9 dB
PEAK_MIN_GAP_S = 2.0
PEAK_LOCAL_RADIUS_S = 0.5
_READ_WINDOWS = 600              # windows decoded per read (60 s of audio)
EXTRACT_TIMEOUT_S = 6 * 3600


def extract_audio_wav(src: Path, dest: Path, *, sample_rate: int = 16000,
                      start: float | None = None, end: float | None = None) -> Path:
    """Decode the first audio stream of ``src`` to a mono 16-bit PCM WAV at ``sample_rate``.

    ``start``/``end`` (source seconds) restrict the range; the WAV then starts at ``start``
    (pass ``offset=start`` to :func:`analyze_audio` / transcription to get source times).
    Raises ``RuntimeError`` when FFmpeg fails (e.g. the file has no audio stream)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    begin = max(0.0, float(start)) if start is not None else 0.0
    if begin > 0:
        cmd += ["-ss", f"{begin:.3f}"]
    cmd += ["-i", str(src)]
    if end is not None:
        length = float(end) - begin
        if length <= 0:
            raise ValueError(f"Invalid audio range: start={begin:.3f}s end={float(end):.3f}s")
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(int(sample_rate)),
            "-c:a", "pcm_s16le", "-f", "wav", str(dest)]
    try:
        run(cmd, timeout=EXTRACT_TIMEOUT_S)
    except RuntimeError as exc:
        raise RuntimeError(f"Could not extract audio from {Path(src).name}: {exc}") from exc
    if not dest.exists() or dest.stat().st_size <= 44:
        raise RuntimeError(f"No audio could be extracted from {Path(src).name}")
    return dest


# ------------------------------------------------------------- envelope
def _read_envelope(wav: Path) -> tuple[np.ndarray, float]:
    """Return (rms dBFS per 100 ms window, duration seconds), reading the file in chunks."""
    with wave.open(str(wav), "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        if width != 2:
            raise ValueError(f"{Path(wav).name}: expected 16-bit PCM audio (got {8 * width}-bit)")
        total_frames = wf.getnframes()
        win = max(1, int(round(rate / SAMPLE_HZ)))
        values: list[np.ndarray] = []
        carry = np.zeros(0, dtype=np.float64)
        while True:
            raw = wf.readframes(win * _READ_WINDOWS)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float64)
            if channels > 1:
                samples = samples[: len(samples) - len(samples) % channels].reshape(-1, channels).mean(axis=1)
            samples = np.concatenate([carry, samples]) if carry.size else samples
            full = len(samples) // win
            if full:
                block = samples[: full * win].reshape(full, win)
                values.append(np.sqrt(np.mean(block * block, axis=1)))
            carry = samples[full * win:]
        if carry.size >= win / 2:  # keep a trailing partial window of >= 50 ms
            values.append(np.array([np.sqrt(np.mean(carry * carry))]))
    rms = np.concatenate(values) if values else np.zeros(0)
    db = 20.0 * np.log10(np.maximum(rms / 32768.0, 1e-5))
    return np.maximum(db, DB_FLOOR), total_frames / float(rate or 1)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open index runs ``[a, b)`` where ``mask`` is True."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b)) for a, b in zip(edges[0::2], edges[1::2], strict=True)]


def _find_peaks(db: np.ndarray, threshold: float) -> list[int]:
    radius = max(1, int(round(PEAK_LOCAL_RADIUS_S * SAMPLE_HZ)))
    gap = int(round(PEAK_MIN_GAP_S * SAMPLE_HZ))
    candidates = []
    for i in np.flatnonzero(db > threshold):
        lo, hi = max(0, i - radius), min(len(db), i + radius + 1)
        if db[i] >= db[lo:hi].max():
            candidates.append(int(i))
    accepted: list[int] = []
    for i in sorted(candidates, key=lambda k: (-db[k], k)):
        if all(abs(i - j) >= gap for j in accepted):
            accepted.append(i)
    return sorted(accepted)


def analyze_audio(wav: Path, *, offset: float = 0.0) -> AudioAnalysis:
    """Analyse a 16-bit PCM WAV: loudness envelope, silences, peaks and speech ratio.

    * ``rms_db``: RMS loudness per 100 ms window in dBFS (``sample_hz = 10``).
    * ``silences``: runs below ``max(-45 dBFS, median - 18 dB)`` lasting >= 0.4 s.
    * ``peak_times``: local maxima louder than ``median + 9 dB`` (and not silent), >= 2 s apart.
    * ``speech_ratio``: fraction of the duration not covered by silences.
    * ``mean_db``: mean loudness of the non-silent windows.

    ``offset`` (source seconds of the WAV's t=0) is added to every reported time."""
    db, duration = _read_envelope(Path(wav))
    if db.size == 0:
        return AudioAnalysis(sample_hz=SAMPLE_HZ, rms_db=[], silences=[], speech_ratio=0.0,
                             mean_db=None, peak_times=[])
    step = 1.0 / SAMPLE_HZ
    median = float(np.median(db))
    silence_thr = max(SILENCE_ABS_DB, median - SILENCE_REL_DB)
    silent_mask = db < silence_thr
    min_windows = int(round(MIN_SILENCE_S * SAMPLE_HZ))
    silences: list[tuple[float, float]] = []
    silent_total = 0.0
    for a, b in _runs(silent_mask):
        if b - a < min_windows:
            continue
        start, end = a * step, min(b * step, duration)
        silent_total += end - start
        silences.append((round(start + offset, 3), round(end + offset, 3)))
    active = db[~silent_mask]
    peak_thr = max(median + PEAK_REL_DB, silence_thr)
    peaks = [round(i * step + step / 2 + offset, 3) for i in _find_peaks(db, peak_thr)]
    speech_ratio = 1.0 - silent_total / duration if duration > 0 else 0.0
    log.debug("audio analysis: %.1fs, median %.1f dB, %d silences, %d peaks",
              duration, median, len(silences), len(peaks))
    return AudioAnalysis(
        sample_hz=SAMPLE_HZ,
        rms_db=[round(float(v), 1) for v in db],
        silences=silences,
        speech_ratio=round(min(1.0, max(0.0, speech_ratio)), 4),
        mean_db=round(float(active.mean()), 2) if active.size else None,
        peak_times=peaks,
        laughter_like=[],
    )
