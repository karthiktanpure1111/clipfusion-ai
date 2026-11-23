"""Clip renderer: RenderSpec -> 1080x1920 (layout size) H.264/AAC MP4.

Pipeline: decode the source range through an FFmpeg pipe (CFR at ``min(source fps,
max_fps)``) -> skip frames inside removed silences -> compose each frame (background,
layers, captions, watermark) -> pipe to H.264 (NVENC when available, libx264 otherwise; an
NVENC failure at runtime -- e.g. the GPU's session limit -- re-encodes with libx264).
Caption work is only set up when subtitles are enabled AND burned in; with subtitles off no
caption object exists and no per-frame caption code runs. Intro/outro clips are fitted ``contain`` on
a blurred copy of themselves and encoded in the same stream. The audio track (same kept
ranges, loudnorm, AAC 48 kHz) is built in parallel and muxed with ``+faststart``.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import cv2
import numpy as np

from app.core import gpu
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.modules.framing.geometry import fit_rects, rect_at, to_px
from app.modules.rendering.audio import AudioPiece, build_audio_track, mux
from app.modules.rendering.caption_track import CaptionTrack
from app.modules.rendering.compositor import Compositor
from app.modules.rendering.timeline import FramePlan, output_fps, plan_frames, removed_silences
from app.modules.rendering.video_io import FrameReader, FrameWriter, read_single_frame
from app.schemas.framing import Background, CompiledLayer, CompiledLayout
from app.schemas.media import MediaInfo
from app.schemas.render import RenderResult, RenderSpec

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str | None], None]
VIDEO_SHARE = 0.93          # progress fraction spent on frames
PROGRESS_EVERY = 15         # frames between progress callbacks
THUMBNAIL_QUALITY = 88


@dataclass
class _Segment:
    """One stretch of output video: the main clip or an intro/outro."""

    kind: str                                   # intro | main | outro
    path: Path
    media: MediaInfo
    start: float                                # file seconds of frame 0
    decode_frames: int                          # frames decoded from the file
    keep: list[bool] | None                     # main clip: frames to output (silence removal)
    compositor: Compositor
    decode_w: int
    decode_h: int
    captions: CaptionTrack | None = None
    out_frames: int = 0
    audio: AudioPiece = field(default_factory=lambda: AudioPiece(duration=0.0))


# ----------------------------------------------------------------- planning
def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def decode_size(layout: CompiledLayout, media: MediaInfo) -> tuple[int, int]:
    """Smallest decode size that still feeds every layer at >= 1:1 (never above source)."""
    sw, sh = media.width, media.height
    need = 0.0
    for layer in layout.layers:
        times = [k.t for k in layer.keyframes] or [0.0]
        for t in times:
            crop, draw = fit_rects(to_px(rect_at(layer, t), sw, sh), to_px(layer.dest, layout.width, layout.height),
                                   layer.fit)
            if crop[2] > 0 and crop[3] > 0:
                need = max(need, draw[2] / crop[2], draw[3] / crop[3])
    scale = min(1.0, max(need * 1.05, 0.25))
    return _even(sw * scale), _even(sh * scale)


def _contain_layout(base: CompiledLayout) -> CompiledLayout:
    """Full-frame ``contain`` on a blurred background (used for intro/outro clips)."""
    bg = base.background if base.background.type == "blur" else Background()
    return CompiledLayout(width=base.width, height=base.height, background=bg,
                          layers=[CompiledLayer(id="main", fit="contain")])


def _validate(spec: RenderSpec) -> None:
    if spec.end - spec.start <= 0.05:
        raise ValidationFailed("The clip must be longer than 0.05 s.", code="invalid_clip_range")
    if not Path(spec.source_path).is_file():
        raise ValidationFailed("The source video file is missing.", code="source_missing")
    if spec.media.width <= 0 or spec.media.height <= 0:
        raise ValidationFailed("The source has no video stream.", code="no_video_stream")
    if spec.layout.width % 2 or spec.layout.height % 2:
        raise ValidationFailed("Output width and height must be even.", code="invalid_output_size")
    if not spec.layout.layers:
        raise ValidationFailed("The framing layout has no video layers.", code="empty_layout")
    for p in (spec.intro_path, spec.outro_path):
        if p is not None and not Path(p).is_file():
            raise ValidationFailed(f"The intro/outro file {Path(p).name} is missing.", code="asset_missing")


def _main_segment(spec: RenderSpec, fps: Fraction) -> tuple[_Segment, FramePlan]:
    duration = spec.end - spec.start
    removed: list[tuple[float, float]] = []
    if spec.options.remove_silence:
        removed = removed_silences(spec.silences, clip_start=spec.start, clip_end=spec.end,
                                   min_silence=spec.options.min_silence, padding=spec.options.silence_padding)
    plan = plan_frames(duration, fps, removed)
    dw, dh = decode_size(spec.layout, spec.media)
    comp = Compositor(spec.layout, dw, dh, background_image=spec.background_image_path)
    comp.set_watermark(spec.options.watermark, spec.watermark_path, spec.custom_fonts)
    captions = None
    if spec.subtitles_enabled and spec.options.burn_subtitles and spec.captions:
        captions = CaptionTrack(spec.captions, spec.style, spec.layout, source_w=spec.media.width,
                                source_h=spec.media.height, face_tracks=spec.face_tracks,
                                custom_fonts=spec.custom_fonts)
    last = plan.ranges[-1][1]
    keep = None if plan.ranges == [(0, plan.total_frames)] else plan.kept_mask()[:last]
    seg = _Segment(kind="main", path=Path(spec.source_path), media=spec.media, start=spec.start,
                   decode_frames=last, keep=keep,
                   compositor=comp, decode_w=dw, decode_h=dh, captions=captions, out_frames=plan.output_frames)
    seg.audio = AudioPiece(duration=plan.output_frames / float(fps),
                           path=Path(spec.source_path) if spec.media.has_audio else None,
                           ranges=[(spec.start + a, spec.start + b) for a, b in plan.kept_clip_ranges()])
    return seg, plan


def _bumper_segment(kind: str, path: Path, spec: RenderSpec, fps: Fraction) -> _Segment:
    from app.modules.analysis.probe import probe_media  # analysis engine (shared probe)

    media = probe_media(path)
    if media.width <= 0 or media.height <= 0:
        raise ValidationFailed(f"The {kind} file has no video stream.", code="asset_invalid")
    frames = max(1, round(media.duration * float(fps)))
    layout = _contain_layout(spec.layout)
    dw, dh = decode_size(layout, media)
    comp = Compositor(layout, dw, dh)
    comp.set_watermark(spec.options.watermark, spec.watermark_path, spec.custom_fonts)
    seg = _Segment(kind=kind, path=path, media=media, start=0.0, decode_frames=frames, keep=None,
                   compositor=comp, decode_w=dw, decode_h=dh, out_frames=frames)
    seg.audio = AudioPiece(duration=frames / float(fps), path=path if media.has_audio else None,
                           ranges=[(0.0, frames / float(fps))])
    return seg


# ---------------------------------------------------------------- rendering
def _segment_frames(seg: _Segment, fps: Fraction) -> Iterator[tuple[float, np.ndarray]]:
    """(clip time, composed canvas) for every output frame of a segment."""
    with FrameReader(seg.path, start=seg.start, frames=seg.decode_frames, fps=fps, width=seg.decode_w,
                     height=seg.decode_h, reuse_buffers=True) as reader:
        for i, frame in enumerate(reader):
            if seg.keep is not None and not seg.keep[i]:
                continue
            t = i / float(fps)
            canvas = seg.compositor.compose(frame, t)
            if seg.captions is not None:
                seg.captions.draw(canvas, t)
            seg.compositor.draw_watermark(canvas)
            yield t, canvas


class _EncoderFailed(RuntimeError):
    def __init__(self, encoder: str, exc: BaseException) -> None:
        super().__init__(f"{encoder} encoding failed: {exc}")
        self.encoder = encoder


def _encode_once(segments: list[_Segment], dest: Path, spec: RenderSpec, fps: Fraction,
                 progress: ProgressFn | None, encoder: str) -> np.ndarray | None:
    total = sum(s.out_frames for s in segments)
    writer = FrameWriter(dest, width=spec.layout.width, height=spec.layout.height, fps=fps,
                         crf=spec.options.video_crf, preset=spec.options.video_preset, encoder=encoder)
    done, thumb = 0, None
    try:
        for seg in segments:
            thumb_at = seg.out_frames // 3 if seg.kind == "main" else -1
            for j, (_, canvas) in enumerate(_segment_frames(seg, fps)):
                if j >= seg.out_frames:
                    break
                try:
                    writer.write(canvas)
                except RuntimeError as exc:
                    raise _EncoderFailed(writer.encoder, exc) from exc
                if j == thumb_at:
                    thumb = canvas
                done += 1
                if progress and done % PROGRESS_EVERY == 0:
                    progress(VIDEO_SHARE * done / total, f"Rendering frame {done}/{total}")
        try:
            writer.close()
        except RuntimeError as exc:
            raise _EncoderFailed(writer.encoder, exc) from exc
    except BaseException:
        writer.abort()
        raise
    return thumb


def _encode_video(segments: list[_Segment], dest: Path, spec: RenderSpec, fps: Fraction,
                  progress: ProgressFn | None) -> np.ndarray | None:
    """Encode all segments into ``dest``; returns the thumbnail canvas (main clip, ~1/3)."""
    encoder = "libx264" if spec.options.encoder == "libx264" else "auto"
    try:
        return _encode_once(segments, dest, spec, fps, progress, encoder)
    except _EncoderFailed as exc:
        if exc.encoder != "h264_nvenc":
            raise
        gpu.mark_nvenc_broken(str(exc))
        log.warning("NVENC failed for %s; re-encoding with libx264", dest.name)
        dest.unlink(missing_ok=True)
        return _encode_once(segments, dest, spec, fps, progress, "libx264")


def _write_thumbnail(canvas: np.ndarray | None, dest: Path) -> Path | None:
    if canvas is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, THUMBNAIL_QUALITY])
    if not ok:
        log.warning("Thumbnail encoding failed")
        return None
    dest.write_bytes(buf.tobytes())
    return dest


def _move(src: Path, dest: Path) -> None:
    """Atomic replace when possible (same volume), copy otherwise."""
    try:
        os.replace(src, dest)
    except OSError:
        shutil.move(str(src), str(dest))


def _probe_duration(path: Path, fallback: float) -> float:
    try:
        from app.modules.analysis.probe import probe_media

        return probe_media(path).duration or fallback
    except Exception:  # noqa: BLE001 - informational only
        return fallback


def render_clip(spec: RenderSpec, *, progress: ProgressFn | None = None) -> RenderResult:
    """Render ``spec`` to ``spec.output_path`` (atomic replace). Raises on failure."""
    _validate(spec)
    fps = output_fps(spec.media.fps, spec.options.max_fps)
    main, plan = _main_segment(spec, fps)
    segments = [main]
    if spec.intro_path is not None:
        segments.insert(0, _bumper_segment("intro", Path(spec.intro_path), spec, fps))
    if spec.outro_path is not None:
        segments.append(_bumper_segment("outro", Path(spec.outro_path), spec, fps))
    settings = get_settings()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="render-", dir=settings.temp_dir))
    started = time.perf_counter()
    audio_path = workdir / "audio.m4a"
    audio_error: list[BaseException] = []
    cancel_audio = threading.Event()

    def _audio() -> None:
        try:
            build_audio_track([s.audio for s in segments], audio_path, normalize=spec.options.normalize_audio,
                              target_lufs=spec.options.target_lufs, bitrate_kbps=spec.options.audio_bitrate_kbps,
                              cancel=cancel_audio)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller thread
            audio_error.append(exc)

    audio_thread = threading.Thread(target=_audio, daemon=True)
    try:
        audio_thread.start()
        video_path = workdir / "video.mp4"
        thumb = _encode_video(segments, video_path, spec, fps, progress)
        if progress:
            progress(VIDEO_SHARE, "Finishing audio")
        audio_thread.join()
        if audio_error:
            raise RuntimeError("audio rendering failed") from audio_error[0]
        out = Path(spec.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = workdir / f"out{out.suffix or '.mp4'}"
        mux(video_path, audio_path, tmp_out)
        _move(tmp_out, out)
        thumb_path = _write_thumbnail(thumb, Path(spec.thumbnail_path or out.with_suffix(".jpg")))
    finally:
        if audio_thread.is_alive():  # failure/cancellation: stop FFmpeg before removing its files
            cancel_audio.set()
            audio_thread.join(timeout=30)
        shutil.rmtree(workdir, ignore_errors=True)
    total_frames = sum(s.out_frames for s in segments)
    expected = total_frames / float(fps)
    elapsed = time.perf_counter() - started
    log.info("Rendered %s: %d frames @ %s fps in %.1fs (%.2fx real time)", out.name, total_frames, fps,
             elapsed, expected / max(elapsed, 1e-6))
    if progress:
        progress(1.0, "Render complete")
    return RenderResult(output_path=out, thumbnail_path=thumb_path, duration=round(_probe_duration(out, expected), 3),
                        width=spec.layout.width, height=spec.layout.height, size_bytes=out.stat().st_size,
                        fps=round(float(fps), 3),
                        kept_ranges=[(round(a, 4), round(b, 4)) for a, b in main.audio.ranges])


def render_preview_frame(spec: RenderSpec, t: float, dest: Path) -> Path:
    """Compose the frame at clip time ``t`` (captions, watermark included) as a JPEG."""
    _validate(spec)
    t = min(max(t, 0.0), max(spec.end - spec.start - 1e-3, 0.0))
    dw, dh = decode_size(spec.layout, spec.media)
    frame = read_single_frame(Path(spec.source_path), spec.start + t, dw, dh)
    comp = Compositor(spec.layout, dw, dh, background_image=spec.background_image_path)
    comp.set_watermark(spec.options.watermark, spec.watermark_path, spec.custom_fonts)
    canvas = comp.compose(frame, t)
    if spec.subtitles_enabled and spec.options.burn_subtitles and spec.captions:
        CaptionTrack(spec.captions, spec.style, spec.layout, source_w=spec.media.width, source_h=spec.media.height,
                     face_tracks=spec.face_tracks, custom_fonts=spec.custom_fonts).draw(canvas, t)
    comp.draw_watermark(canvas)
    written = _write_thumbnail(canvas, Path(dest))
    if written is None:
        raise RuntimeError("could not encode the preview JPEG")
    return written
