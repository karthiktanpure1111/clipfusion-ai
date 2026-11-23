"""Whole-source analysis orchestration (the ``analyze_source`` job's engine).

Stages (``progress(stage, overall, message)`` uses these names)::

    probe           ffprobe
    audio           16 kHz mono WAV + loudness analysis          \\  thread 1
    transcription   faster-whisper (mode profile, GPU if usable)  /
    video_analysis  ONE low-res FFmpeg decode -> scene cuts, motion, brightness   (thread 2)
    highlights      scoring, optional Claude ranking, boundary refinement, de-duplication

Audio + transcription run in one worker thread and the video analysis in another, so the
video decode overlaps speech recognition. ``overall`` is the weighted sum of the measured
fraction of every stage (weights in :data:`STAGE_WEIGHTS`), so it is monotonic even while two
stages run at once. All callbacks (``progress`` and ``on_stage_result``) are invoked on the
CALLER's thread -- worker threads only post events to a queue -- so callers may use
thread-bound resources (e.g. a DB session) inside them. An exception raised by a callback
(job cancellation) stops both workers and propagates.

``on_stage_result(name, value)`` is called as soon as each result exists ("media", "audio",
"transcript", "scenes", "visual") so the caller can persist partial results. Work already
done can be skipped with ``existing_transcript`` / ``existing_audio`` / ``existing_scenes`` /
``existing_visual``. All temporary files live inside ``workdir``; the WAV is deleted before
returning.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.modules.analysis.audio import analyze_audio, extract_audio_wav
from app.modules.analysis.modes import ModeProfile, profile_for, transcription_model
from app.modules.analysis.probe import probe_media
from app.modules.analysis.progress import ProgressFn
from app.modules.analysis.video_analysis import analyze_video
from app.schemas.media import (
    AnalysisOptions,
    AudioAnalysis,
    HighlightCandidate,
    MediaInfo,
    SceneCut,
    TranscriptData,
    VisualActivity,
)

log = logging.getLogger(__name__)

STAGE_ORDER = ("probe", "audio", "transcription", "video_analysis", "highlights")
STAGE_WEIGHTS: dict[str, float] = {"probe": 0.01, "audio": 0.04, "transcription": 0.55, "video_analysis": 0.25,
                                   "highlights": 0.15}
_GPU_WEIGHTS: dict[str, float] = {"probe": 0.01, "audio": 0.04, "transcription": 0.40, "video_analysis": 0.40,
                                  "highlights": 0.15}
# Nominal sequential ranges (kept for callers that display a per-stage band).
STAGES: dict[str, tuple[float, float]] = {}
_acc = 0.0
for _name in STAGE_ORDER:
    STAGES[_name] = (round(_acc, 4), round(_acc + STAGE_WEIGHTS[_name], 4))
    _acc += STAGE_WEIGHTS[_name]
VISUAL_SAMPLE_HZ = 1.0
WORKER_JOIN_S = 60.0
OverallProgress = Callable[[str, float, str | None], None]


@dataclass
class SourceAnalysisResult:
    """Everything the analysis produced for one source video."""

    media: MediaInfo
    transcript: TranscriptData
    audio: AudioAnalysis
    scenes: list[SceneCut]
    visual: VisualActivity
    highlights: list[HighlightCandidate]
    warnings: list[str] = field(default_factory=list)   # degraded stages, shown to the user
    stage_timings: dict[str, dict[str, Any]] = field(default_factory=dict)
    device: str | None = None             # "cuda" | "cpu" (None when transcription was skipped)
    transcription_model: str | None = None
    mode: str = "balanced"


class _Aborted(Exception):
    """Raised inside a worker thread when the analysis was cancelled / another worker failed."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Run:
    """Shared state of one analysis: event queue, weighted progress, stage timings."""

    def __init__(self, progress: OverallProgress | None, on_stage_result: Callable[[str, Any], None] | None,
                 weights: dict[str, float]) -> None:
        self.progress, self.on_stage_result, self.weights = progress, on_stage_result, weights
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.fractions: dict[str, float] = dict.fromkeys(weights, 0.0)
        self.timings: dict[str, dict[str, Any]] = {}
        self._t0: dict[str, float] = {}
        self._last_overall = 0.0
        self._caller = threading.get_ident()

    # ----------------------------------------------- called on the caller's thread
    def handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "progress":
            _, stage, fraction, message = event
            self.fractions[stage] = max(self.fractions.get(stage, 0.0), min(1.0, max(0.0, float(fraction))))
            overall = sum(self.weights[s] * f for s, f in self.fractions.items())
            overall = max(self._last_overall, min(1.0, overall))
            self._last_overall = overall
            if self.progress is not None:
                self.progress(stage, round(overall, 6), message)
        elif kind == "result" and self.on_stage_result is not None:
            self.on_stage_result(event[1], event[2])

    def pump(self, workers: list[threading.Thread]) -> None:
        """Deliver events until every worker finished and the queue is empty."""
        while True:
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                if not any(w.is_alive() for w in workers):
                    if self.events.empty():
                        return
                continue
            self.handle(event)

    def flush(self) -> None:
        while True:
            try:
                self.handle(self.events.get_nowait())
            except queue.Empty:
                return

    # ------------------------------------------------- called from any thread
    def post(self, *event: Any) -> None:
        """Deliver ``event`` now on the caller's thread (after anything queued), else queue it."""
        if threading.get_ident() == self._caller:
            self.flush()
            self.handle(event)
        else:
            self.events.put(event)

    def stage_progress(self, stage: str) -> ProgressFn:
        def _cb(fraction: float, message: str | None = None) -> None:
            if self.cancel.is_set():
                raise _Aborted()
            self.post("progress", stage, fraction, message)

        return _cb

    def start(self, stage: str, message: str) -> None:
        self._t0[stage] = time.perf_counter()
        self.timings[stage] = {"started_at": _now(), "finished_at": None, "seconds": None}
        self.post("progress", stage, 0.0, message)

    def finish(self, stage: str, message: str | None = None) -> None:
        t = self.timings.setdefault(stage, {"started_at": _now()})
        t["finished_at"] = _now()
        t["seconds"] = round(time.perf_counter() - self._t0.get(stage, time.perf_counter()), 3)
        self.post("progress", stage, 1.0, message)

    def skip(self, stage: str, message: str) -> None:
        stamp = _now()
        self.timings[stage] = {"started_at": stamp, "finished_at": stamp, "seconds": 0.0, "skipped": True}
        self.post("progress", stage, 1.0, message)


# --------------------------------------------------------------------- workers
@dataclass
class _SpeechOut:
    audio: AudioAnalysis | None = None
    transcript: TranscriptData | None = None
    device: str | None = None
    model: str | None = None
    error: BaseException | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _VideoOut:
    scenes: list[SceneCut] = field(default_factory=list)
    visual: VisualActivity = field(default_factory=lambda: VisualActivity(sample_hz=VISUAL_SAMPLE_HZ))
    error: BaseException | None = None
    warnings: list[str] = field(default_factory=list)


def _speech_worker(run: _Run, out: _SpeechOut, source: Path, workdir: Path, media: MediaInfo,
                   options: AnalysisOptions, existing_transcript: TranscriptData | None,
                   existing_audio: AudioAnalysis | None) -> None:
    wav: Path | None = None
    try:
        if not media.has_audio:
            out.audio = existing_audio or AudioAnalysis()
            run.skip("audio", "The video has no audio track")
            run.post("result", "audio", out.audio)
            out.warnings.append("The video has no audio track: transcription and audio signals were skipped.")
            out.transcript = existing_transcript or TranscriptData(
                language=options.language, model=transcription_model(options, "cpu"), duration=0.0, segments=[])
            run.skip("transcription", "No speech to transcribe")
            run.post("result", "transcript", out.transcript)
            return
        need_wav = existing_audio is None or existing_transcript is None
        if existing_audio is not None:
            out.audio = existing_audio
            run.skip("audio", "Using the saved audio analysis")
        else:
            run.start("audio", "Extracting and analysing audio")
        if need_wav:
            wav = extract_audio_wav(source, workdir / "analysis-audio-16k.wav")
        if out.audio is None:
            if run.cancel.is_set():
                raise _Aborted()
            out.audio = analyze_audio(wav)
            run.finish("audio", "Audio analysed")
        run.post("result", "audio", out.audio)
        if existing_transcript is not None:
            out.transcript = existing_transcript
            run.skip("transcription", "Using the existing transcript")
        else:
            from app.modules.ai.registry import get_transcription_provider

            run.start("transcription", "Transcribing speech")

            def _on_plan(plan: Any) -> None:
                out.device, out.model = getattr(plan, "device", None), getattr(plan, "model", None)

            out.transcript = get_transcription_provider().transcribe(
                wav, model_name=options.whisper_model, language=options.language, mode=options.mode,
                progress=run.stage_progress("transcription"), on_plan=_on_plan)
            run.finish("transcription", f"Transcribed {len(out.transcript.segments)} segments"
                       + (f" on {out.device.upper()}" if out.device else ""))
        run.post("result", "transcript", out.transcript)
    except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
        out.error = exc
        run.cancel.set()
    finally:
        if wav is not None:
            wav.unlink(missing_ok=True)


def _video_worker(run: _Run, out: _VideoOut, source: Path, media: MediaInfo, profile: ModeProfile,
                  existing_scenes: list[SceneCut] | None, existing_visual: VisualActivity | None) -> None:
    try:
        if existing_scenes is not None and existing_visual is not None:
            out.scenes, out.visual = existing_scenes, existing_visual
            run.skip("video_analysis", "Using the saved video analysis")
        elif media.width <= 0 or media.height <= 0:
            run.skip("video_analysis", "No video stream")
        else:
            run.start("video_analysis", "Analysing video (scene changes, motion)")
            try:
                res = analyze_video(source, fps=profile.video_fps, width=profile.video_width,
                                    height=profile.video_height, motion_hz=VISUAL_SAMPLE_HZ,
                                    duration=media.duration, progress=run.stage_progress("video_analysis"))
                out.scenes = existing_scenes if existing_scenes is not None else res.scenes
                out.visual = existing_visual if existing_visual is not None else res.visual
                run.finish("video_analysis", f"{len(out.scenes)} scene changes")
            except (RuntimeError, OSError, ValueError) as exc:
                log.warning("Video analysis failed for %s: %s", source.name, exc)
                out.warnings.append(f"Video analysis failed ({exc}); highlights use the other signals.")
                run.finish("video_analysis", "Video analysis failed")
        run.post("result", "scenes", out.scenes)
        run.post("result", "visual", out.visual)
    except BaseException as exc:  # noqa: BLE001 - _Aborted / unexpected: re-raised on the caller's thread
        out.error = exc
        run.cancel.set()


def _coerce_scenes(value: Any) -> list[SceneCut] | None:
    if value is None:
        return None
    return [v if isinstance(v, SceneCut) else SceneCut(time=float(v)) if isinstance(v, int | float)
            else SceneCut.model_validate(v) for v in value]


def _coerce(model: type, value: Any) -> Any:
    if value is None or isinstance(value, model):
        return value
    return model.model_validate(value)


# ---------------------------------------------------------------------- public
def analyze_source(source: Path, workdir: Path, options: AnalysisOptions, *,
                   source_title: str | None = None,
                   existing_transcript: TranscriptData | None = None,
                   existing_audio: AudioAnalysis | dict | None = None,
                   existing_scenes: list[SceneCut] | list[float] | None = None,
                   existing_visual: VisualActivity | dict | None = None,
                   on_stage_result: Callable[[str, Any], None] | None = None,
                   progress: OverallProgress | None = None) -> SourceAnalysisResult:
    """Run the complete analysis of ``source`` and return a :class:`SourceAnalysisResult`.

    Probe, audio and transcription failures raise (the job retries / fails with the
    message). Video analysis is a secondary signal: its failure is logged and reported in
    ``warnings`` and highlight detection continues without it."""
    from app.modules.highlights.detector import detect_highlights
    from app.modules.transcription.service import probable_device

    source, workdir = Path(source), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    profile = profile_for(options)
    audio_in = _coerce(AudioAnalysis, existing_audio)
    visual_in = _coerce(VisualActivity, existing_visual)
    scenes_in = _coerce_scenes(existing_scenes)
    weights = _GPU_WEIGHTS if existing_transcript is None and probable_device() == "cuda" else STAGE_WEIGHTS
    run = _Run(progress, on_stage_result, weights)

    run.start("probe", "Reading video information")
    media = probe_media(source)
    run.finish("probe", f"{media.width}x{media.height}, {media.duration:.0f} s")
    run.post("result", "media", media)

    speech, video = _SpeechOut(), _VideoOut()
    workers = [
        threading.Thread(target=_speech_worker, name="analysis-speech", daemon=True,
                         args=(run, speech, source, workdir, media, options, existing_transcript, audio_in)),
        threading.Thread(target=_video_worker, name="analysis-video", daemon=True,
                         args=(run, video, source, media, profile, scenes_in, visual_in)),
    ]
    for w in workers:
        w.start()
    try:
        run.pump(workers)
    except BaseException:
        run.cancel.set()
        for w in workers:
            w.join(timeout=WORKER_JOIN_S)
        raise
    for w in workers:
        w.join()
    for err in (speech.error, video.error):
        if err is not None and not isinstance(err, _Aborted):
            raise err
    if speech.error is not None or video.error is not None:
        raise RuntimeError("analysis aborted")
    assert speech.audio is not None and speech.transcript is not None
    warnings = speech.warnings + video.warnings

    run.start("highlights", "Finding highlights")
    highlights = detect_highlights(transcript=speech.transcript, audio=speech.audio, scenes=video.scenes,
                                   visual=video.visual, media=media, options=options, source_title=source_title,
                                   progress=run.stage_progress("highlights"))
    run.finish("highlights", f"Found {len(highlights)} highlights")
    log.info("Analysis of %s (%s mode) finished: %s", source.name, profile.name,
             {k: v.get("seconds") for k, v in run.timings.items()})
    return SourceAnalysisResult(media=media, transcript=speech.transcript, audio=speech.audio, scenes=video.scenes,
                                visual=video.visual, highlights=highlights, warnings=warnings,
                                stage_timings=run.timings, device=speech.device,
                                transcription_model=speech.model or speech.transcript.model, mode=profile.name)
