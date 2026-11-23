"""Speech-to-text with faster-whisper (word timestamps + Silero VAD).

Device selection (``WHISPER_DEVICE=auto``): the venv's CUDA DLLs are registered first
(:mod:`app.core.gpu`); CUDA is used when CTranslate2 sees a GPU *and* loading the model plus
a first inference succeed. Any CUDA failure -- typically missing cuBLAS/cuDNN DLLs on Windows,
which only surface at the first inference -- is logged and transcription falls back to CPU.
Out-of-memory on the GPU is not treated as "CUDA broken": the next attempt may use a smaller
model. Loaded models are cached per ``(name, device, compute_type)``; loading a CUDA model
first releases any other cached CUDA model (4 GB GPUs cannot hold two).

With a processing ``mode`` (:mod:`app.modules.analysis.modes`) the model, precision, beam
size and batching come from the mode profile; on CUDA decoding is batched with
faster-whisper's ``BatchedInferencePipeline`` (VAD-segmented windows decoded together).
"""

from __future__ import annotations

import gc
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.core import gpu
from app.core.config import get_settings
from app.core.errors import DependencyMissing
from app.core.ffmpeg import ffmpeg_bin, run
from app.modules.analysis.modes import ModeProfile, get_profile
from app.modules.analysis.progress import ProgressFn, report
from app.modules.analysis.timecode import format_timestamp
from app.modules.transcription.cleanup import clean_text, clean_word, ends_sentence
from app.schemas.media import TranscriptData, TranscriptSegment, TranscriptWord

log = logging.getLogger(__name__)
gpu.register_cuda_dlls()   # before ctranslate2 is imported anywhere

BEAM_SIZE = 5
VAD_MIN_SILENCE_MS = 500
SAMPLE_RATE = 16000
CHUNK_SECONDS = 1200.0          # long recordings are transcribed in ~20 min chunks
SPLIT_SEARCH_SECONDS = 60.0
DECODE_TIMEOUT_S = 3 * 3600
LANGUAGE_PROBE_SECONDS = 120.0
# Whisper drops punctuation in loud, excited or overlapping speech, which destroys sentence
# boundaries. A short, neutral, punctuated text in the same language (given to every 30 s window)
# makes it keep punctuating. Languages without an entry are transcribed without a prompt.
STYLE_PROMPTS: dict[str, str] = {
    "en": "Hello, and welcome. How are you today? I'm doing great, thanks! Let's begin.",
    "es": "Hola, y bienvenidos. ¿Cómo estás hoy? ¡Muy bien, gracias! Empecemos.",
    "pt": "Olá, e bem-vindos. Como você está hoje? Estou ótimo, obrigado! Vamos começar.",
    "fr": "Bonjour, et bienvenue. Comment allez-vous ? Très bien, merci ! Commençons.",
    "de": "Hallo, und willkommen. Wie geht es dir heute? Sehr gut, danke! Fangen wir an.",
    "it": "Ciao, e benvenuti. Come stai oggi? Molto bene, grazie! Cominciamo.",
    "nl": "Hallo, en welkom. Hoe gaat het vandaag? Heel goed, dank je! Laten we beginnen.",
    "id": "Halo, dan selamat datang. Apa kabar hari ini? Baik sekali, terima kasih! Mari kita mulai.",
    "hi": "नमस्ते, और स्वागत है। आप कैसे हैं? मैं ठीक हूँ, धन्यवाद! चलिए शुरू करते हैं।",
}

_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str, str], Any] = {}
_CUDA_VERIFIED: set[tuple[str, str]] = set()
_CUDA_BROKEN = False
_OOM_MARKERS = ("out of memory", "cudaerrormemoryallocation", "cublas_status_alloc_failed")


@dataclass(frozen=True)
class DecodePlan:
    """How one transcription runs: device, model, precision and decoding parameters."""

    device: str              # "cuda" | "cpu"
    model: str
    compute_type: str
    beam_size: int = BEAM_SIZE
    batch_size: int = 0      # > 0 -> faster-whisper BatchedInferencePipeline


def whisper_available() -> bool:
    """True when the faster-whisper package can be imported (models download on first use)."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means unavailable
        return False
    return True


def _download_root() -> str:
    settings = get_settings()
    root = Path(settings.whisper_cache_dir) if settings.whisper_cache_dir else Path(settings.models_dir) / "whisper"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _compute_type(device: str, profile: ModeProfile | None = None) -> str:
    configured = (get_settings().whisper_compute_type or "auto").strip().lower()
    if configured != "auto":
        return configured
    if profile is not None:
        return profile.gpu_compute_type if device == "cuda" else profile.cpu_compute_type
    return "float16" if device == "cuda" else "int8"


def _free_other_cuda_models(keep: tuple[str, str, str]) -> None:
    """Drop cached CUDA models other than ``keep`` (4 GB GPUs cannot hold two models). Caller holds _LOCK."""
    stale = [k for k in _MODELS if k[1] == "cuda" and k != keep]
    for k in stale:
        log.info("Releasing Whisper model %r from the GPU", k[0])
        _MODELS.pop(k, None)
    if stale:
        gc.collect()


def _load_model(name: str, device: str, compute_type: str) -> Any:
    key = (name, device, compute_type)
    with _LOCK:
        model = _MODELS.get(key)
        if model is None:
            from faster_whisper import WhisperModel

            if device == "cuda":
                _free_other_cuda_models(key)
            log.info("Loading Whisper model %r on %s (%s)", name, device, compute_type)
            threads = min(8, os.cpu_count() or 4) if device == "cpu" else 0
            model = WhisperModel(name, device=device, compute_type=compute_type, cpu_threads=threads,
                                 download_root=_download_root())
            _MODELS[key] = model
        return model


def _forget_model(name: str, device: str, compute_type: str) -> None:
    with _LOCK:
        if _MODELS.pop((name, device, compute_type), None) is not None:
            gc.collect()


def release_models(device: str | None = None) -> None:
    """Free cached models (all, or only those on ``device``)."""
    with _LOCK:
        for k in [k for k in _MODELS if device is None or k[1] == device]:
            _MODELS.pop(k, None)
    gc.collect()


def _cuda_device_count() -> int:
    return gpu.cuda_device_count()


def _is_oom(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(m in text for m in _OOM_MARKERS)


def _mark_cuda_broken(exc: BaseException) -> None:
    global _CUDA_BROKEN
    _CUDA_BROKEN = True
    log.warning("CUDA transcription unavailable (%s: %s); falling back to CPU.", type(exc).__name__, exc)


def _cuda_ready(name: str, compute: str | None = None) -> bool:
    """Load ``name`` on CUDA and run a 1 s warm-up inference once; False on any failure."""
    if _CUDA_BROKEN or _cuda_device_count() <= 0:
        return False
    if not gpu.cuda_libraries_present():
        log.info("CUDA GPU found but the cuBLAS runtime is not loadable; transcribing on CPU.")
        return False
    compute = compute or _compute_type("cuda")
    if (name, compute) in _CUDA_VERIFIED:
        return True
    try:
        model = _load_model(name, "cuda", compute)
        segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1,
                                       vad_filter=False, without_timestamps=True)
        list(segments)
    except Exception as exc:  # noqa: BLE001 - missing DLLs raise RuntimeError/OSError
        _forget_model(name, "cuda", compute)
        if _is_oom(exc):
            log.warning("Whisper %r (%s) does not fit in GPU memory: %s", name, compute, exc)
        else:
            _mark_cuda_broken(exc)
        return False
    _CUDA_VERIFIED.add((name, compute))
    return True


def probable_device() -> str:
    """Device a new transcription would most likely use, without loading anything."""
    if get_settings().whisper_device == "cpu" or _CUDA_BROKEN:
        return "cpu"
    return "cuda" if gpu.cuda_available() else "cpu"


def _cpu_plan(name: str | None, profile: ModeProfile | None) -> DecodePlan:
    p = profile or get_profile(None)
    return DecodePlan(device="cpu", model=name or p.cpu_model, compute_type=_compute_type("cpu", profile),
                      beam_size=p.cpu_beam_size if profile else BEAM_SIZE,
                      batch_size=p.cpu_batch_size if profile else 0)


def plan_transcription(model_name: str | None = None, mode: str | None = None) -> DecodePlan:
    """Pick device + model + decoding parameters (verifies CUDA with a warm-up inference).

    ``model_name`` (explicit) is used on both devices; otherwise the mode profile decides
    (:mod:`app.modules.analysis.modes`). Without a mode the legacy behaviour applies
    (``settings.whisper_model``, beam 5, sequential decoding)."""
    profile = get_profile(mode) if mode else None
    legacy = profile is None
    preference = get_settings().whisper_device
    if preference != "cpu":
        gpu_name = model_name or (get_settings().whisper_model if legacy else profile.gpu_model)
        compute = _compute_type("cuda", profile)
        if _cuda_ready(gpu_name, compute):
            return DecodePlan(device="cuda", model=gpu_name, compute_type=compute,
                              beam_size=BEAM_SIZE if legacy else profile.gpu_beam_size,
                              batch_size=0 if legacy else profile.gpu_batch_size)
        if preference == "cuda":
            log.warning("WHISPER_DEVICE=cuda but CUDA is not usable; transcribing on CPU.")
    return _cpu_plan(model_name or (get_settings().whisper_model if legacy else None), profile)


def _select_device(name: str) -> tuple[str, str]:
    """Backward-compatible helper: (device, compute_type) for ``name``."""
    plan = plan_transcription(name)
    return plan.device, plan.compute_type


def _convert_words(raw_words: list[Any] | None, offset: float, language: str | None,
                   state: dict[str, Any]) -> list[TranscriptWord]:
    words: list[TranscriptWord] = []
    for w in raw_words or []:
        text = clean_word(str(w.word), sentence_start=state["sentence_start"], language=language)
        if not text.strip():
            continue
        start = max(float(w.start) + offset, state["last_start"])
        end = max(float(w.end) + offset, start)
        words.append(TranscriptWord(start=round(start, 3), end=round(end, 3), word=text,
                                    probability=round(float(w.probability or 0.0), 4)))
        state["last_start"] = start
        state["sentence_start"] = ends_sentence(text)
    return words


def _load_pcm16(audio: Path) -> np.ndarray:
    """Decode ``audio`` to 16 kHz mono int16 samples with FFmpeg.

    We decode ourselves instead of letting faster-whisper use PyAV: faster-whisper 1.2.1
    passes ``metadata_errors`` to ``av.open``, which the installed PyAV rejects."""
    proc = run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(audio),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
               timeout=DECODE_TIMEOUT_S)
    return np.frombuffer(proc.stdout or b"", dtype="<i2")


def _chunk_bounds(pcm: np.ndarray) -> list[tuple[int, int]]:
    """Split long audio into ~20 min chunks, cutting at the quietest 100 ms in the last minute."""
    n, chunk, search, frame = len(pcm), int(CHUNK_SECONDS * SAMPLE_RATE), int(SPLIT_SEARCH_SECONDS * SAMPLE_RATE), SAMPLE_RATE // 10
    bounds: list[tuple[int, int]] = []
    start = 0
    while n - start > chunk + search:
        lo = start + chunk - search
        window = pcm[lo:lo + search - search % frame].astype(np.float64).reshape(-1, frame)
        cut = lo + int(np.argmin((window * window).mean(axis=1))) * frame + frame // 2
        bounds.append((start, cut))
        start = cut
    bounds.append((start, n))
    return bounds


def _detect_language(model: Any, pcm: np.ndarray) -> tuple[str | None, float | None]:
    """Language of the first minutes of speech (needed before decoding to pick the prompt)."""
    detect = getattr(model, "detect_language", None)
    if detect is None:
        return None, None
    try:
        samples = pcm[: int(LANGUAGE_PROBE_SECONDS * SAMPLE_RATE)].astype(np.float32) / 32768.0
        lang, prob, _ = detect(audio=samples, vad_filter=True, language_detection_segments=3)
        return lang, float(prob)
    except Exception as exc:  # noqa: BLE001 - transcribe() then detects the language itself
        log.warning("Language detection failed (%s); letting the transcriber decide.", exc)
        return None, None


def _style_prompt(language: str | None) -> str | None:
    return STYLE_PROMPTS.get((language or "").lower().split("-")[0])


def _is_prompt_echo(text: str, no_speech_prob: float | None, prompt: str | None) -> bool:
    """The model repeating its prompt over non-speech (music, noise) instead of transcribing."""
    if not prompt or no_speech_prob is None or no_speech_prob < 0.4:
        return False
    spoken = "".join(c for c in text.lower() if c.isalnum())
    return bool(spoken) and spoken in "".join(c for c in prompt.lower() if c.isalnum())


def _decode(model: Any, samples: np.ndarray, plan: DecodePlan, lang: str | None) -> tuple[Any, Any]:
    kwargs: dict[str, Any] = {
        "language": lang or None, "beam_size": plan.beam_size, "word_timestamps": True, "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": VAD_MIN_SILENCE_MS}, "hotwords": _style_prompt(lang),
    }
    if plan.batch_size > 0:
        from faster_whisper import BatchedInferencePipeline

        # without_timestamps=False keeps sentence-sized segments instead of one per 30 s window
        return BatchedInferencePipeline(model).transcribe(samples, batch_size=plan.batch_size,
                                                          without_timestamps=False, **kwargs)
    return model.transcribe(samples, condition_on_previous_text=False, **kwargs)


def _run(model: Any, pcm: np.ndarray, *, plan: DecodePlan, language: str | None, offset: float,
         progress: ProgressFn | None) -> TranscriptData:
    total = len(pcm) / SAMPLE_RATE
    lang, lang_prob = (language, None) if language else _detect_language(model, pcm)
    segments: list[TranscriptSegment] = []
    state: dict[str, Any] = {"sentence_start": True, "last_start": float("-inf")}
    for a, b in _chunk_bounds(pcm):
        chunk_offset = a / SAMPLE_RATE
        samples = pcm[a:b].astype(np.float32) / 32768.0
        segments_iter, info = _decode(model, samples, plan, lang)
        if lang is None or lang_prob is None:
            lang = lang or info.language
            lang_prob = getattr(info, "language_probability", None)
        for seg in segments_iter:
            seg_offset = offset + chunk_offset
            if _is_prompt_echo(str(seg.text), seg.no_speech_prob, _style_prompt(lang)):
                continue
            starts_sentence = state["sentence_start"]
            words = _convert_words(seg.words, seg_offset, lang, state)
            text = clean_text("".join(w.word for w in words) if words else str(seg.text), lang,
                              sentence_start=starts_sentence)
            if not text:
                continue
            start = float(seg.start) + seg_offset
            end = max(float(seg.end) + seg_offset, start)
            segments.append(TranscriptSegment(
                id=len(segments), start=round(start, 3), end=round(end, 3), text=text, words=words,
                avg_logprob=float(seg.avg_logprob) if seg.avg_logprob is not None else None,
                no_speech_prob=float(seg.no_speech_prob) if seg.no_speech_prob is not None else None,
            ))
            done = chunk_offset + float(seg.end)
            if total > 0:
                report(progress, done / total, f"Transcribing {format_timestamp(done)} / {format_timestamp(total)}")
    report(progress, 1.0, None)
    return TranscriptData(language=lang, language_probability=lang_prob, model=plan.model,
                          duration=round(total, 3), segments=segments)


def transcribe(audio: Path, *, model_name: str | None = None, language: str | None = None,
               offset: float = 0.0, progress: ProgressFn | None = None, mode: str | None = None,
               on_plan: Callable[[DecodePlan], None] | None = None) -> TranscriptData:
    """Transcribe ``audio`` (any FFmpeg-readable file; 16 kHz mono WAV preferred).

    Word timestamps, VAD (min silence 500 ms), no conditioning on previous text. ``mode``
    selects model/precision/beam/batching (see :func:`plan_transcription`); ``model_name``
    forces a model. ``offset`` is added to every time so a clip-range WAV yields source times.
    ``on_plan`` receives the device/model actually used (again after a CPU fallback).
    Raises :class:`DependencyMissing` when faster-whisper or the model is unavailable."""
    if not whisper_available():
        raise DependencyMissing("faster-whisper is not installed; transcription is unavailable.")
    report(progress, 0.0, "Loading speech model")
    plan = plan_transcription(model_name, mode)
    report(progress, 0.0, f"Loading speech model ({plan.model}, {plan.device})")
    try:
        model = _load_model(plan.model, plan.device, plan.compute_type)
    except Exception as exc:  # noqa: BLE001 - download/IO/format errors
        raise DependencyMissing(f"Whisper model '{plan.model}' could not be loaded: {exc}") from exc
    pcm = _load_pcm16(Path(audio))
    if on_plan is not None:
        on_plan(plan)
    if pcm.size < SAMPLE_RATE // 10:
        report(progress, 1.0, None)
        return TranscriptData(language=language, model=plan.model, duration=round(pcm.size / SAMPLE_RATE, 3),
                              segments=[])
    try:
        return _run(model, pcm, plan=plan, language=language, offset=offset, progress=progress)
    except (RuntimeError, OSError, MemoryError) as exc:
        if plan.device != "cuda":
            raise
        _forget_model(plan.model, plan.device, plan.compute_type)
        if _is_oom(exc):
            log.warning("GPU ran out of memory while transcribing with %r; retrying on the CPU", plan.model)
        else:
            _mark_cuda_broken(exc)
    plan = _cpu_plan(model_name, get_profile(mode) if mode else None)
    if on_plan is not None:
        on_plan(plan)
    try:
        model = _load_model(plan.model, "cpu", plan.compute_type)
    except Exception as exc:  # noqa: BLE001
        raise DependencyMissing(f"Whisper model '{plan.model}' could not be loaded: {exc}") from exc
    return _run(model, pcm, plan=plan, language=language, offset=offset, progress=progress)
