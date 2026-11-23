"""GPU detection and acceleration helpers (never raise; everything falls back to the CPU).

* :func:`register_cuda_dlls` makes the CUDA runtime DLLs shipped by the ``nvidia-*-cu12`` wheels
  (``site-packages/nvidia/*/bin``) loadable on Windows *before* CTranslate2 needs them
  (``os.add_dll_directory`` + ``PATH`` prepend; idempotent).
* :func:`cuda_available` / :func:`gpu_info` are cached and safe to call from any thread.
* :func:`nvenc_available` probes FFmpeg's ``h264_nvenc`` once with a tiny real encode;
  :func:`video_encoder_args` returns the matching encoder arguments (NVENC or libx264).

Overrides (env / ``.env``): ``VIDEO_ENCODER=auto|nvenc|libx264`` and ``WHISPER_DEVICE`` (see
``app.core.config``).
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DLL_HANDLES: list[Any] = []          # keep add_dll_directory cookies alive
_REGISTERED: list[str] = []
_REGISTER_DONE = False
_NVENC_STATE: dict[str, Any] = {}
NVENC_PROBE_TIMEOUT_S = 20
NVIDIA_SMI_TIMEOUT_S = 8
CUDA_RUNTIME_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll")


class _EngineEnv(BaseSettings):
    """Engine overrides read from the environment / the same .env files as the app settings."""

    model_config = SettingsConfigDict(env_file=Settings.model_config.get("env_file"), env_file_encoding="utf-8",
                                      extra="ignore")
    video_encoder: str = "auto"       # auto | nvenc | libx264


@lru_cache
def engine_env() -> _EngineEnv:
    return _EngineEnv()


# ------------------------------------------------------------------ CUDA DLLs
def _nvidia_bin_dirs() -> list[Path]:
    roots: list[Path] = []
    try:
        import nvidia  # type: ignore[import-not-found]  # namespace package from the nvidia-*-cu12 wheels

        roots += [Path(p) for p in getattr(nvidia, "__path__", [])]
    except Exception:  # noqa: BLE001 - wheels not installed
        pass
    roots.append(Path(sys.prefix) / "Lib" / "site-packages" / "nvidia")
    dirs: list[Path] = []
    for root in roots:
        if root.is_dir():
            for sub in sorted(root.iterdir()):
                b = sub / "bin"
                if b.is_dir() and b not in dirs:
                    dirs.append(b)
    return dirs


def register_cuda_dlls() -> list[str]:
    """Register the venv's ``nvidia/*/bin`` folders for DLL loading (Windows only; idempotent).

    Returns the registered folders. Safe to call many times and from several threads."""
    global _REGISTER_DONE
    if _REGISTER_DONE:
        return list(_REGISTERED)
    with _LOCK:
        if _REGISTER_DONE:
            return list(_REGISTERED)
        if os.name == "nt":
            path_parts = os.environ.get("PATH", "").split(os.pathsep)
            for d in _nvidia_bin_dirs():
                s = str(d)
                try:
                    _DLL_HANDLES.append(os.add_dll_directory(s))
                except (OSError, AttributeError) as exc:
                    log.debug("add_dll_directory(%s) failed: %s", s, exc)
                    continue
                if s not in path_parts:
                    path_parts.insert(0, s)
                _REGISTERED.append(s)
            os.environ["PATH"] = os.pathsep.join(path_parts)
            if _REGISTERED:
                log.info("Registered %d CUDA DLL folders from the venv", len(_REGISTERED))
        _REGISTER_DONE = True
    return list(_REGISTERED)


def _dll_loadable(name: str) -> bool:
    if os.name != "nt":
        return True     # Linux wheels resolve through the loader / rpath
    try:
        ctypes.WinDLL(name)
    except OSError:
        return False
    return True


@lru_cache
def cuda_device_count() -> int:
    """CUDA devices visible to CTranslate2 (0 when unavailable; never raises)."""
    register_cuda_dlls()
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception as exc:  # noqa: BLE001
        log.debug("CTranslate2 CUDA query failed: %s", exc)
        return 0


@lru_cache
def cuda_libraries_present() -> bool:
    """True when the cuBLAS runtime needed by CTranslate2 can be loaded."""
    register_cuda_dlls()
    return all(_dll_loadable(n) for n in CUDA_RUNTIME_DLLS)


def cuda_available() -> bool:
    """A CUDA GPU plus the cuBLAS runtime DLLs are present (cached). The first real inference
    can still fail (e.g. cuDNN missing / out of memory); callers keep their CPU fallback."""
    return cuda_device_count() > 0 and cuda_libraries_present()


@lru_cache
def gpu_info() -> dict[str, Any] | None:
    """``{"name", "vram_total_mb", "vram_free_mb", "driver"}`` of GPU 0 via ``nvidia-smi``
    (cached; the free VRAM is the value at the first call). None without an NVIDIA GPU."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, timeout=NVIDIA_SMI_TIMEOUT_S, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    if out.returncode != 0 or not line:
        return None
    parts = [p.strip() for p in line[0].split(",")]
    if len(parts) < 4:
        return None
    try:
        return {"name": parts[0], "vram_total_mb": int(float(parts[1])), "vram_free_mb": int(float(parts[2])),
                "driver": parts[3]}
    except ValueError:
        return {"name": parts[0], "vram_total_mb": None, "vram_free_mb": None, "driver": parts[3]}


def vram_total_mb() -> int | None:
    info = gpu_info()
    return info.get("vram_total_mb") if info else None


# ---------------------------------------------------------------------- NVENC
def _probe_nvenc() -> bool:
    from app.core.ffmpeg import ffmpeg_bin

    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi",
           "-i", "color=c=gray:size=256x256:rate=30", "-frames:v", "8", "-pix_fmt", "yuv420p",
           "-c:v", "h264_nvenc", "-preset", "p4", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=NVENC_PROBE_TIMEOUT_S, check=False,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("NVENC probe could not run: %s", exc)
        return False
    if proc.returncode != 0:
        log.info("NVENC unavailable: %s", (proc.stderr or b"").decode("utf-8", "replace").strip()[-300:])
        return False
    return True


def nvenc_available() -> bool:
    """FFmpeg can encode H.264 on the NVIDIA GPU (probed once; ``VIDEO_ENCODER`` overrides)."""
    pref = engine_env().video_encoder.strip().lower()
    if pref in ("libx264", "cpu", "x264"):
        return False
    with _LOCK:
        if "ok" not in _NVENC_STATE:
            _NVENC_STATE["ok"] = _probe_nvenc()
            if _NVENC_STATE["ok"]:
                log.info("Using NVENC (h264_nvenc) for video encoding")
        return bool(_NVENC_STATE["ok"])


def mark_nvenc_broken(reason: str) -> None:
    """Disable NVENC for the rest of the process (e.g. session limit reached at runtime)."""
    with _LOCK:
        if _NVENC_STATE.get("ok"):
            log.warning("NVENC disabled after a runtime failure: %s", reason[-300:])
        _NVENC_STATE["ok"] = False


# NVENC presets mapped from the libx264 preset names the render options use.
_NVENC_PRESETS = {"ultrafast": "p1", "veryfast": "p2", "faster": "p3", "fast": "p4", "medium": "p5", "slow": "p6"}


def video_encoder_args(*, crf: int, preset: str, gop: int, prefer_gpu: bool = True) -> tuple[str, list[str]]:
    """``(encoder_name, ffmpeg_args)`` for an H.264 High-profile yuv420p encode.

    NVENC uses constant-quality VBR (``-cq`` = ``crf`` + 1, which lands at a similar size and
    quality as libx264 at ``crf`` for screen/game content) with spatial AQ and B-frames."""
    if prefer_gpu and nvenc_available():
        return "h264_nvenc", [
            "-c:v", "h264_nvenc", "-preset", _NVENC_PRESETS.get(preset, "p5"), "-tune", "hq",
            "-rc", "vbr", "-cq", str(int(crf) + 1), "-b:v", "0", "-maxrate", "40M", "-bufsize", "80M",
            "-spatial-aq", "1", "-bf", "2", "-b_ref_mode", "disabled", "-profile:v", "high",
            "-g", str(gop), "-pix_fmt", "yuv420p",
        ]
    return "libx264", ["-c:v", "libx264", "-preset", preset, "-crf", str(int(crf)), "-profile:v", "high",
                       "-g", str(gop), "-pix_fmt", "yuv420p"]


def describe() -> dict[str, Any]:
    """Summary for logs / capability endpoints."""
    return {"cuda": cuda_available(), "cuda_devices": cuda_device_count(), "gpu": gpu_info(),
            "nvenc": nvenc_available(), "dll_dirs": len(register_cuda_dlls())}
