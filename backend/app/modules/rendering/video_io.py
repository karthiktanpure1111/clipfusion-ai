"""FFmpeg raw-video pipes: CFR BGR frame decoding and libx264 encoding.

* :class:`FrameReader` decodes ``[start, start + duration)`` of a file (accurate input
  seek, display orientation via FFmpeg's autorotate, resampled to a constant ``fps`` and
  scaled to an exact size) and yields ``(h, w, 3)`` uint8 BGR frames. A background thread
  prefetches frames so decoding overlaps compositing. Exactly ``frames`` frames are
  produced: if the source ends early the last frame is repeated (keeps A/V sync).
* :class:`FrameWriter` feeds frames to H.264 (yuv420p, High profile, BT.709, CFR,
  ``+faststart``) from a writer thread: ``h264_nvenc`` on the GPU when FFmpeg's NVENC probe
  succeeds (:mod:`app.core.gpu`), else ``libx264``.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
from collections import deque
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from types import TracebackType

import numpy as np

from app.core import gpu
from app.core.ffmpeg import ffmpeg_bin

log = logging.getLogger(__name__)

_SENTINEL = object()
_PIPE_BUFFER = 1 << 20
OS_PIPE_SIZE = 1 << 20      # Windows' default anonymous-pipe buffer is tiny: 1 MB is ~1.5x faster here


def os_pipe(size: int = OS_PIPE_SIZE) -> tuple[int, int]:
    """``(read_fd, write_fd)`` of a pipe with a large kernel buffer (non-inheritable; Popen
    duplicates the end it hands to the child). Raw frame pipes move GBs per clip, and on
    Windows a 1 MB pipe moves 1080p BGR frames ~1.5x faster than ``subprocess.PIPE``."""
    if os.name == "nt":
        import _winapi
        import msvcrt

        r, w = _winapi.CreatePipe(None, size)
        return msvcrt.open_osfhandle(r, os.O_RDONLY), msvcrt.open_osfhandle(w, 0)
    return os.pipe()


def read_exact(stream, buf: memoryview) -> int:  # noqa: ANN001 - raw binary file
    """Fill ``buf`` from ``stream`` (``readinto``, no intermediate bytes); returns bytes read."""
    got, size = 0, len(buf)
    while got < size:
        n = stream.readinto(buf[got:])
        if not n:
            break
        got += n
    return got


def fps_fraction(fps: float) -> Fraction:
    """Exact rational for common NTSC rates (29.97 -> 30000/1001), else a close fraction."""
    for base in (24, 30, 48, 60, 120):
        if abs(fps - base * 1000 / 1001) < 0.005:
            return Fraction(base * 1000, 1001)
    if abs(fps - round(fps)) < 0.005:
        return Fraction(round(fps))
    return Fraction(fps).limit_denominator(1001)


def _fps_arg(fps: Fraction) -> str:
    return str(fps.numerator) if fps.denominator == 1 else f"{fps.numerator}/{fps.denominator}"


class _StderrDrain(threading.Thread):
    def __init__(self, stream) -> None:  # noqa: ANN001 - binary pipe
        super().__init__(daemon=True)
        self._stream = stream
        self.lines: deque[str] = deque(maxlen=40)

    def run(self) -> None:
        for raw in iter(self._stream.readline, b""):
            self.lines.append(raw.decode("utf-8", "replace").rstrip())

    def tail(self) -> str:
        return "\n".join(self.lines)


class FrameReader:
    """Iterate exactly ``frames`` BGR frames of ``path`` starting at ``start`` seconds.

    With ``reuse_buffers`` the frames are read (``readinto``) into a fixed ring of
    ``prefetch + 3`` buffers: a yielded frame is only valid until the consumer has advanced
    ``prefetch + 2`` frames, so consumers that keep frames must copy them."""

    def __init__(self, path: Path, *, start: float, frames: int, fps: Fraction, width: int, height: int,
                 prefetch: int = 6, reuse_buffers: bool = False) -> None:
        self.path, self.start, self.frames, self.fps = Path(path), max(0.0, start), int(frames), fps
        self.width, self.height = int(width), int(height)
        self._frame_bytes = self.width * self.height * 3
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, prefetch))
        self._ring = max(1, prefetch) + 3 if reuse_buffers else 0
        self._stdout = None
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._drain: _StderrDrain | None = None
        self._closed = False
        self.decoded = 0

    def _command(self) -> list[str]:
        duration = (self.frames + 1) / float(self.fps)
        vf = (f"fps={_fps_arg(self.fps)},scale={self.width}:{self.height}:flags=bicubic,setsar=1,"
              f"format=bgr24")
        return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-ss", f"{self.start:.6f}", "-t", f"{duration:.6f}", "-i", str(self.path),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf, "-frames:v", str(self.frames),
                "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]

    def _pump(self) -> None:
        assert self._stdout is not None
        shape = (self.height, self.width, 3)
        ring = [np.empty(shape, np.uint8) for _ in range(self._ring)]
        try:
            for i in range(self.frames):
                frame = ring[i % self._ring] if ring else np.empty(shape, np.uint8)
                if read_exact(self._stdout, memoryview(frame).cast("B")) < self._frame_bytes:
                    break
                self._queue.put(frame)
        except Exception as exc:  # noqa: BLE001 - forwarded to the consumer
            self._queue.put(exc)
        finally:
            self._queue.put(_SENTINEL)

    def start_decoding(self) -> FrameReader:
        if self._proc is None:
            rfd, wfd = os_pipe()
            try:
                self._proc = subprocess.Popen(self._command(), stdout=wfd, stderr=subprocess.PIPE,
                                              stdin=subprocess.DEVNULL)
            except BaseException:
                os.close(rfd)
                raise
            finally:
                os.close(wfd)
            self._stdout = open(rfd, "rb", buffering=0)  # noqa: SIM115 - closed in close()
            self._drain = _StderrDrain(self._proc.stderr)
            self._drain.start()
            self._thread = threading.Thread(target=self._pump, daemon=True)
            self._thread.start()
        return self

    def __iter__(self) -> Iterator[np.ndarray]:
        self.start_decoding()
        last: np.ndarray | None = None
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise RuntimeError(f"decoding {self.path.name} failed") from item
            last = item
            self.decoded += 1
            yield item
        if last is None:
            detail = self._drain.tail() if self._drain else ""
            raise RuntimeError(f"FFmpeg produced no frames for {self.path.name} at {self.start:.3f}s\n{detail}")
        if self.decoded < self.frames:
            log.info("%s: source ended after %d/%d frames; holding the last frame", self.path.name,
                     self.decoded, self.frames)
        for _ in range(self.frames - self.decoded):
            yield last

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            while self._thread is not None and self._thread.is_alive():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    self._thread.join(timeout=0.05)
            proc.wait(timeout=30)
            for stream in (self._stdout, proc.stderr):
                if stream is not None:
                    stream.close()

    def __enter__(self) -> FrameReader:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()


class FrameWriter:
    """Encode BGR frames to an H.264 MP4 (video only) via a writer thread."""

    def __init__(self, dest: Path, *, width: int, height: int, fps: Fraction, crf: int, preset: str,
                 queue_size: int = 6, encoder: str = "auto") -> None:
        self.dest, self.width, self.height, self.fps = Path(dest), int(width), int(height), fps
        self.crf, self.preset = int(crf), preset
        gop = max(1, round(float(self.fps) * 2))
        self.encoder, self._encoder_args = gpu.video_encoder_args(crf=self.crf, preset=preset, gop=gop,
                                                                  prefer_gpu=encoder != "libx264")
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, queue_size))
        self._error: BaseException | None = None
        self.frames_written = 0
        rfd, wfd = os_pipe()
        try:
            self._proc = subprocess.Popen(self._command(), stdin=rfd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE)
        except BaseException:
            os.close(wfd)
            raise
        finally:
            os.close(rfd)
        self._stdin = open(wfd, "wb", buffering=0)  # noqa: SIM115 - closed by the writer thread
        self._drain = _StderrDrain(self._proc.stderr)
        self._drain.start()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _command(self) -> list[str]:
        fps = _fps_arg(self.fps)
        return [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{self.width}x{self.height}", "-framerate", fps,
                "-i", "pipe:0", "-an",
                "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic,format=yuv420p",
                *self._encoder_args, "-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-color_range", "tv", "-fps_mode", "cfr", "-r", fps,
                "-movflags", "+faststart", str(self.dest)]

    def _pump(self) -> None:
        stdin = self._stdin
        try:
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    break
                if self._error is None:
                    view = memoryview(item).cast("B")
                    while view:
                        n = stdin.write(view)
                        if not n:
                            raise OSError("encoder pipe closed")
                        view = view[n:]
        except BaseException as exc:  # noqa: BLE001 - surfaced by write()/close()
            self._error = exc
            while self._queue.get() is not _SENTINEL:
                pass
        finally:
            try:
                stdin.close()
            except OSError:
                pass

    def write(self, frame: np.ndarray) -> None:
        if self._error is not None:
            raise RuntimeError(f"encoder failed: {self._drain.tail()}") from self._error
        if frame.shape != (self.height, self.width, 3) or frame.dtype != np.uint8:
            raise ValueError(f"frame shape {frame.shape} does not match {self.height}x{self.width}x3")
        self._queue.put(np.ascontiguousarray(frame))
        self.frames_written += 1

    def close(self, timeout: float = 600.0) -> None:
        """Flush, wait for FFmpeg and raise on encoder failure."""
        self._queue.put(_SENTINEL)
        self._thread.join()
        code = self._proc.wait(timeout=timeout)
        self._drain.join(timeout=5)
        if self._error is not None or code != 0:
            raise RuntimeError(f"H.264 encoding failed (exit {code}): {self._drain.tail()}")

    def abort(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
        if self._thread.is_alive():
            self._error = self._error or RuntimeError("aborted")
            self._queue.put(_SENTINEL)
            self._thread.join(timeout=10)
        self._proc.wait(timeout=30)


def read_single_frame(path: Path, t: float, width: int, height: int) -> np.ndarray:
    """Decode the frame displayed at ``t`` seconds (scaled to ``width`` x ``height``)."""
    with FrameReader(path, start=t, frames=1, fps=Fraction(30), width=width, height=height) as reader:
        for frame in reader:
            return frame.copy()
    raise RuntimeError(f"no frame decoded at {t:.3f}s")
