"""GPU helpers never crash and always leave a working CPU path."""

from __future__ import annotations

import app.core.gpu as gpu


def test_detection_is_safe_and_cached() -> None:
    first = gpu.register_cuda_dlls()
    assert gpu.register_cuda_dlls() == first           # idempotent
    assert isinstance(gpu.cuda_available(), bool)
    assert isinstance(gpu.cuda_device_count(), int)
    info = gpu.gpu_info()
    assert info is None or (info["name"] and "vram_total_mb" in info)
    summary = gpu.describe()
    assert set(summary) == {"cuda", "cuda_devices", "gpu", "nvenc", "dll_dirs"}


def test_encoder_args_follow_the_probe(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "_NVENC_STATE", {"ok": True})
    name, args = gpu.video_encoder_args(crf=18, preset="medium", gop=60)
    assert name == "h264_nvenc" and args[args.index("-c:v") + 1] == "h264_nvenc"
    assert args[args.index("-cq") + 1] == "19" and args[args.index("-preset") + 1] == "p5"
    name, args = gpu.video_encoder_args(crf=18, preset="medium", gop=60, prefer_gpu=False)
    assert name == "libx264" and args[args.index("-crf") + 1] == "18"
    gpu.mark_nvenc_broken("session limit")
    assert gpu.video_encoder_args(crf=20, preset="fast", gop=30)[0] == "libx264"


def test_video_encoder_override(monkeypatch) -> None:
    monkeypatch.setattr(gpu, "_NVENC_STATE", {"ok": True})
    monkeypatch.setattr(gpu.engine_env(), "video_encoder", "libx264")
    assert gpu.nvenc_available() is False
    assert gpu.video_encoder_args(crf=18, preset="medium", gop=60)[0] == "libx264"
