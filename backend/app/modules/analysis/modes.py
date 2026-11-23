"""Processing modes (``AnalysisOptions.mode``): one :class:`ModeProfile` per speed/quality level.

Values chosen from measurements on the reference machine (12-core laptop CPU, RTX 3050 Laptop
4 GB, no CUDA runtime installed -> CPU transcription; FFmpeg NVENC works) with
``scripts/benchmark_pipeline.py`` on a 10-minute 1080p30 TTS stream (WER vs the known script):

==========================================  ==========  =======
Whisper on CPU (int8), 612 s of speech       time        WER
==========================================  ==========  =======
small, sequential, beam 5 (previous engine)  131.5 s     1.46 %
small, batched x8, beam 5                    87.5 s      2.48 %
small, batched x8, greedy                    50.8 s      1.31 %
tiny,  batched x8, greedy                    9.7 s       8.47 %
==========================================  ==========  =======
(batched beam 5 measured inside the full pipeline, i.e. with the video scan running alongside)

* ``fast``     -- quickest usable result: ``tiny`` on CPU (5x faster than small, but ~6x the
  word error rate), coarse video sampling, shallow candidate search, fastest encoder preset.
* ``balanced`` -- the default: ``small`` batched greedy on CPU (faster AND as accurate as the
  previous beam-5 decoding on this machine), one-pass video analysis, NVENC rendering.
* ``best``     -- selection quality first: denser video sampling and face
  tracking, the deepest candidate search; ``large-v3`` when a CUDA GPU is usable.

GPU columns (``gpu_*``) apply automatically once CTranslate2 can use CUDA (install
``nvidia-cublas-cu12`` + ``nvidia-cudnn-cu12`` into the venv); they were sized for 4 GB of VRAM
(``int8_float16`` for large-v3) but could not be benchmarked here.
``options.whisper_model`` (explicit request) overrides the profile's model on both devices.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.media import AnalysisOptions


@dataclass(frozen=True)
class ModeProfile:
    name: str
    # transcription
    gpu_model: str
    gpu_compute_type: str
    cpu_model: str
    cpu_compute_type: str
    gpu_beam_size: int
    cpu_beam_size: int
    gpu_batch_size: int           # BatchedInferencePipeline batch on CUDA
    cpu_batch_size: int           # 0 = sequential decoding on the CPU
    # one-pass video analysis
    video_fps: float
    video_width: int
    video_height: int
    # per-clip vision
    face_sample_fps: float
    # highlight search
    candidate_depth: int          # candidate pool = depth x max_clips (before refinement / LLM)
    window_step: int              # use every n-th sentence start as a window start
    # rendering / proxies
    render_preset: str            # libx264 preset name (mapped to NVENC p1..p7 when on the GPU)
    render_crf: int
    proxy_preset: str


MODES: dict[str, ModeProfile] = {
    "fast": ModeProfile(
        name="fast", gpu_model="small", gpu_compute_type="int8_float16", cpu_model="tiny",
        cpu_compute_type="int8", gpu_beam_size=1, cpu_beam_size=1, gpu_batch_size=16, cpu_batch_size=8,
        video_fps=5.0, video_width=128, video_height=72, face_sample_fps=2.0,
        candidate_depth=2, window_step=2, render_preset="veryfast", render_crf=21, proxy_preset="ultrafast",
    ),
    "balanced": ModeProfile(
        name="balanced", gpu_model="small", gpu_compute_type="float16", cpu_model="small",
        cpu_compute_type="int8", gpu_beam_size=5, cpu_beam_size=1, gpu_batch_size=12, cpu_batch_size=8,
        video_fps=10.0, video_width=160, video_height=90, face_sample_fps=3.0,
        candidate_depth=3, window_step=1, render_preset="fast", render_crf=19, proxy_preset="veryfast",
    ),
    "best": ModeProfile(
        name="best", gpu_model="large-v3", gpu_compute_type="int8_float16", cpu_model="small",
        cpu_compute_type="int8", gpu_beam_size=5, cpu_beam_size=1, gpu_batch_size=4, cpu_batch_size=8,
        video_fps=15.0, video_width=192, video_height=108, face_sample_fps=4.0,
        candidate_depth=5, window_step=1, render_preset="medium", render_crf=18, proxy_preset="veryfast",
    ),
}
DEFAULT_MODE = "balanced"


def get_profile(mode: str | None) -> ModeProfile:
    """Profile for ``mode`` (unknown / empty -> balanced)."""
    return MODES.get((mode or DEFAULT_MODE).strip().lower(), MODES[DEFAULT_MODE])


def profile_for(options: AnalysisOptions) -> ModeProfile:
    return get_profile(options.mode)


def transcription_model(options: AnalysisOptions, device: str) -> str:
    """Whisper model used for ``options`` on ``device`` ("cuda" | "cpu")."""
    if options.whisper_model:
        return options.whisper_model
    p = profile_for(options)
    return p.gpu_model if device == "cuda" else p.cpu_model


def expected_transcription_model(options: AnalysisOptions) -> str:
    """The model a new transcription would use right now (GPU if CUDA looks usable). Workers use
    it to decide whether a stored transcript can be reused (``Transcript.model`` must match)."""
    from app.modules.transcription.service import probable_device

    return transcription_model(options, probable_device())


def clip_targets(options: AnalysisOptions) -> list[float]:
    """Target clip durations: explicit ``clip_durations`` when the request set them, otherwise
    derived from ``min_duration`` / ``max_duration`` (short, middle and long targets)."""
    lo, hi = sorted((float(options.min_duration), float(options.max_duration)))
    explicit = "clip_durations" in options.model_fields_set and options.clip_durations
    if explicit:
        return sorted({min(hi, max(lo, float(d))) for d in options.clip_durations if d}) or [lo]
    if hi - lo < 4.0:
        return [round((lo + hi) / 2, 1)]
    # Short-form sweet spot: a target near the short end, one in the middle, one near the top.
    mid = (lo + hi) / 2
    targets = {round(lo + 0.25 * (hi - lo), 1), round(mid, 1), round(hi - 0.1 * (hi - lo), 1)}
    return sorted(targets)
