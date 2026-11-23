"""Benchmark the clipping pipeline on a realistic generated stream (or your own video).

    .venv/Scripts/python.exe scripts/benchmark_pipeline.py                 # ~10 min synthetic stream
    .venv/Scripts/python.exe scripts/benchmark_pipeline.py --video my.mp4  # your own file
    .venv/Scripts/python.exe scripts/benchmark_pipeline.py --minutes 3 --mode fast --label after

Prints wall-clock time per stage (analysis stages, face tracking, render, proxy), the device
and model used, and the transcript's word error rate against the known script of the
generated stream, and writes a JSON report (incl. the transcript and every highlight with
its reasons) to data/bench/<label>.json so before/after runs can be compared.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

PARAGRAPHS = [
    "Hey everyone, welcome back to the stream. Today we are trying the hardest level in the game. "
    "I have failed this level twelve times already, so let me explain how the final boss works.",
    "The boss has three phases. In the first phase you should never attack from the front. "
    "You wait for the shield to drop, and then you strike twice. That is the secret nobody tells you.",
    "Okay chat, here we go. He is low, he is so low! Wait, wait, wait. No way! No way! "
    "I can not believe that just happened! That was the craziest clutch of my entire life!",
    "Did you guys see that? Why did that work? It worked because the shield resets after the second hit. "
    "Thank you so much for the donation, that is incredibly kind of you.",
    "Let me tell you a story. Three years ago I almost quit streaming. Nobody was watching, and I was "
    "working two jobs. Then one night a single viewer told me my videos helped him through a hard time.",
    "So here is my honest opinion, and some of you will disagree. Most beginners waste money on expensive "
    "gear. The truth is, your microphone matters far more than your camera. Fix your audio first.",
    "Alright, quick break. We are going to save the game before we leave the arena. Trust me, "
    "I learned that the hard way when I lost four hours of progress.",
    "What is the biggest mistake people make in this game? They rush. Patience beats speed every single time. "
    "If you remember only one thing from today, remember that.",
    "This next part is really funny. My cat just walked across the keyboard and bought a legendary sword "
    "with all of my gold. I am not even angry, that is honestly impressive.",
    "Let us talk about the update. The developers changed the healing system, and I think it is a mistake. "
    "Healing now takes twice as long, which makes the final fight almost impossible for new players.",
    "So what did we learn today? Never attack from the front, save your game, and fix your audio. "
    "If you enjoyed this, tell me in the comments which boss we should fight next. See you tomorrow.",
]


_NUMBER_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
    "8": "eight", "9": "nine", "10": "ten", "11": "eleven", "12": "twelve", "20": "twenty", "100": "hundred",
}


def normalise_words(text: str) -> list[str]:
    """Lowercase words without punctuation; digits spelled out; "can not" == "cannot"."""
    import re

    text = text.lower().replace("’", "'").replace("can not", "cannot")
    words = re.findall(r"[a-z0-9']+", text)
    return [_NUMBER_WORDS.get(w, w) for w in words]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein word error rate (substitutions + deletions + insertions) / reference words."""
    ref, hyp = normalise_words(reference), normalise_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def reference_script(video: Path) -> str | None:
    """The known spoken text of a generated benchmark stream (None for other videos)."""
    parts = video.parent / (video.stem + "-parts")
    n = len(list(parts.glob("p*.wav"))) if parts.is_dir() else 0
    return " ".join(PARAGRAPHS[i % len(PARAGRAPHS)] for i in range(n)) if n else None


def make_stream(dest: Path, minutes: float) -> Path:
    """Real TTS speech (Windows SAPI) over 1080p gameplay with a real-face facecam."""
    from app.core.ffmpeg import ffmpeg_bin
    from app.modules.analysis.probe import probe_media
    from tests.helpers.media import make_speech_wav
    from tests.helpers.vision_media import make_streamer_video

    work = dest.parent / (dest.stem + "-parts")
    work.mkdir(parents=True, exist_ok=True)
    wavs, total, i = [], 0.0, 0
    while total < minutes * 60:
        text = PARAGRAPHS[i % len(PARAGRAPHS)]
        wav = make_speech_wav(work / f"p{i}.wav", text)
        if wav is None:
            raise SystemExit("No text-to-speech engine found (Windows SAPI). Use --video instead.")
        wavs.append(wav)
        total += probe_media(wav).duration + 1.5
        i += 1
    inputs, chains = [], []
    for k, w in enumerate(wavs):
        inputs += ["-i", str(w)]
        gain = "volume=9dB," if PARAGRAPHS[k % len(PARAGRAPHS)].count("!") >= 3 else ""
        chains.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,{gain}apad=pad_dur=1.5[a{k}]")
    graph = ";".join(chains) + ";" + "".join(f"[a{k}]" for k in range(len(wavs))) + f"concat=n={len(wavs)}:v=0:a=1[out]"
    speech = work / "speech.wav"
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "[out]",
                    str(speech)], check=True)
    video = make_streamer_video(work / "video.mp4", duration=probe_media(speech).duration)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(video), "-i", str(speech), "-map", "0:v",
                    "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(dest)], check=True)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--mode", default="balanced", help="fast | balanced | best")
    ap.add_argument("--label", default="run")
    ap.add_argument("--clips", type=int, default=2, help="how many clips to face-track, proxy and render")
    ap.add_argument("--captions", action="store_true", help="also burn captions (pre-v2 behaviour) for comparison")
    ap.add_argument("--legacy-transcription", action="store_true",
                    help="transcribe with the pre-mode path (settings model, sequential beam 5) for WER comparison")
    args = ap.parse_args()

    from app.core import gpu
    from app.modules.analysis.modes import get_profile
    from app.modules.analysis.pipeline import analyze_source
    from app.modules.analysis.probe import probe_media
    from app.schemas.media import AnalysisOptions

    out_dir = BACKEND / "data" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    video = args.video or out_dir / f"stream-{args.minutes:g}min.mp4"
    if not video.exists():
        print(f"Generating a {args.minutes:g}-minute test stream at {video} ...", flush=True)
        make_stream(video, args.minutes)
    media = probe_media(video)
    profile = get_profile(args.mode)
    print(f"Source: {video.name} {media.width}x{media.height} {media.duration:.0f}s; mode={profile.name}; "
          f"gpu={gpu.gpu_info()}; cuda={gpu.cuda_available()}; nvenc={gpu.nvenc_available()}", flush=True)

    opts = {"clip_durations": [30.0, 60.0], "max_clips": 8, "use_llm": False, "mode": profile.name}
    if args.legacy_transcription:
        opts.pop("mode")
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=out_dir) as work:
        if args.legacy_transcription:
            import app.modules.transcription.service as service

            real = service.transcribe
            service.transcribe = lambda audio, **kw: real(audio, **{**kw, "mode": None})
        result = analyze_source(video, Path(work), AnalysisOptions(**opts))
    analysis_s = time.perf_counter() - t0
    stages = {k: round(float(v.get("seconds") or 0.0), 1) for k, v in result.stage_timings.items()}
    reference = reference_script(video)
    wer = round(word_error_rate(reference, result.transcript.text), 4) if reference else None
    print(f"Analysis: {analysis_s:.1f}s ({media.duration / analysis_s:.1f}x realtime) stages={stages}", flush=True)
    print(f"  transcript: {result.transcription_model} on {result.device} lang={result.transcript.language} "
          f"words={len(result.transcript.words())} WER={wer}; clips found: {len(result.highlights)}", flush=True)

    clip_rows = []
    for c in sorted(result.highlights, key=lambda h: -h.score)[: args.clips]:
        clip_rows.append(_clip_bench(video, media, c.start, c.end, result, out_dir, profile, args.captions))
    report = {"label": args.label, "mode": profile.name, "machine": platform.processor(), "gpu": gpu.gpu_info(),
              "cuda": gpu.cuda_available(), "nvenc": gpu.nvenc_available(),
              "source_seconds": round(media.duration, 1), "analysis_seconds": round(analysis_s, 1),
              "stages": stages, "stage_timings": result.stage_timings, "device": result.device,
              "transcript_model": result.transcription_model, "language": result.transcript.language,
              "wer": wer, "words": len(result.transcript.words()), "clips_found": len(result.highlights),
              "warnings": result.warnings, "clips": clip_rows,
              "highlights": [{"start": h.start, "end": h.end, "score": h.score, "category": h.category,
                              "text": h.transcript[:120], "reasons": h.reasons, "breakdown": h.breakdown}
                             for h in sorted(result.highlights, key=lambda h: h.start)],
              "transcript": result.transcript.text}
    path = out_dir / f"{args.label}.json"
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"Report: {path}")


def _clip_bench(video: Path, media, start: float, end: float, result, out_dir: Path, profile,
                captions: bool) -> dict:
    from app.modules.analysis.probe import probe_media
    from app.modules.clipping.proxy import make_clip_proxy
    from app.modules.framing.layout import compile_layout
    from app.modules.framing.protection import prepare_config
    from app.modules.framing.suggest import suggest_framing
    from app.modules.rendering.renderer import render_clip
    from app.modules.tracking.tracker import analyze_clip_vision
    from app.schemas.render import RenderOptions, RenderSpec

    t = time.perf_counter()
    vision = analyze_clip_vision(video, start, end, media, sample_fps=profile.face_sample_fps)
    vision_s = time.perf_counter() - t
    duration = end - start
    config = prepare_config(suggest_framing(vision, media, duration), vision, source_w=media.width,
                            source_h=media.height, duration=duration)
    extra: dict = {}
    if captions:
        from app.modules.subtitles.captions import build_captions
        from app.modules.subtitles.templates import TEMPLATES

        style = TEMPLATES["bold_viral"][1]
        extra = {"captions": build_captions(result.transcript.words(), clip_start=start, clip_end=end, style=style),
                 "style": style}
    out = out_dir / f"bench-{start:.0f}.mp4"
    spec = RenderSpec(source_path=video, start=start, end=end, media=media, subtitles_enabled=captions,
                      layout=compile_layout(config, media.width, media.height), face_tracks=vision.tracks,
                      options=RenderOptions(video_preset=profile.render_preset, video_crf=profile.render_crf),
                      output_path=out, **extra)
    t = time.perf_counter()
    rendered = render_clip(spec)
    render_s = time.perf_counter() - t
    out_info = probe_media(out) if out.exists() else None
    out.unlink(missing_ok=True)
    if rendered.thumbnail_path:
        Path(rendered.thumbnail_path).unlink(missing_ok=True)
    proxy = out_dir / f"proxy-{start:.0f}.mp4"
    t = time.perf_counter()
    make_clip_proxy(video, start, end, proxy, preset=profile.proxy_preset)
    proxy_s = time.perf_counter() - t
    proxy.unlink(missing_ok=True)
    row = {"start": round(start, 1), "duration": round(duration, 1), "mode": config.mode,
           "face_tracking_s": round(vision_s, 1), "render_s": round(render_s, 1), "proxy_s": round(proxy_s, 1),
           "captions": captions, "output": {"w": out_info.width, "h": out_info.height, "v": out_info.video_codec,
                                            "a": out_info.audio_codec, "duration": round(out_info.duration, 2)}
           if out_info else None}
    print(f"  clip {start:.0f}s ({duration:.0f}s, {config.mode}): face tracking {vision_s:.1f}s, render "
          f"{render_s:.1f}s, proxy {proxy_s:.1f}s", flush=True)
    return row


if __name__ == "__main__":
    main()
