"""Transcription planning: mode profiles, device fallback, VRAM-safe model cache."""

from __future__ import annotations

from typing import Any

import app.modules.transcription.service as service
from app.modules.analysis.modes import MODES


def test_cpu_plan_follows_the_mode(monkeypatch) -> None:
    monkeypatch.setattr(service.get_settings(), "whisper_device", "cpu")
    for name, profile in MODES.items():
        plan = service.plan_transcription(None, name)
        assert plan.device == "cpu" and plan.model == profile.cpu_model
        assert plan.beam_size == profile.cpu_beam_size and plan.batch_size == profile.cpu_batch_size
    forced = service.plan_transcription("tiny", "best")
    assert forced.model == "tiny" and forced.device == "cpu"
    legacy = service.plan_transcription(None, None)          # no mode: previous behaviour
    assert legacy.model == service.get_settings().whisper_model and legacy.batch_size == 0
    assert legacy.beam_size == service.BEAM_SIZE
    assert service.probable_device() == "cpu"


def test_cuda_plan_uses_the_gpu_profile(monkeypatch) -> None:
    monkeypatch.setattr(service.get_settings(), "whisper_device", "auto")
    monkeypatch.setattr(service.get_settings(), "whisper_compute_type", "auto")
    monkeypatch.setattr(service, "_cuda_ready", lambda name, compute=None: True)
    plan = service.plan_transcription(None, "best")
    assert (plan.device, plan.model, plan.compute_type) == ("cuda", MODES["best"].gpu_model,
                                                             MODES["best"].gpu_compute_type)
    assert plan.batch_size == MODES["best"].gpu_batch_size > 0


def test_oom_is_not_treated_as_broken_cuda() -> None:
    assert service._is_oom(RuntimeError("CUDA failed with error out of memory"))
    assert not service._is_oom(RuntimeError("Library cublas64_12.dll is not found"))


def test_loading_a_cuda_model_releases_the_previous_one(monkeypatch) -> None:
    import faster_whisper

    loaded: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, name: str, device: str, **kw: Any) -> None:
            loaded.append((name, device))

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(service, "_MODELS", {})
    service._load_model("small", "cuda", "float16")
    service._load_model("small", "cpu", "int8")
    service._load_model("large-v3", "cuda", "int8_float16")
    assert set(service._MODELS) == {("small", "cpu", "int8"), ("large-v3", "cuda", "int8_float16")}
    service._load_model("large-v3", "cuda", "int8_float16")          # cached: not loaded again
    assert loaded.count(("large-v3", "cuda")) == 1
    service.release_models("cuda")
    assert set(service._MODELS) == {("small", "cpu", "int8")}
