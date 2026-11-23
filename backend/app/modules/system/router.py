"""Health checks and the honest capability report (no prefix: /health, /health/ready, /system/capabilities)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

_FFMPEG_TTL = 300.0
_ffmpeg_cache: tuple[float, dict[str, bool]] | None = None
_ffmpeg_lock = threading.Lock()


def _ffmpeg_caps() -> dict[str, bool]:
    """FFmpeg feature flags (cached for 5 minutes; probing runs subprocesses)."""
    global _ffmpeg_cache
    with _ffmpeg_lock:
        if _ffmpeg_cache is not None and time.monotonic() - _ffmpeg_cache[0] < _FFMPEG_TTL:
            return dict(_ffmpeg_cache[1])
    keys = ("ffmpeg", "ffprobe", "libx264", "libass")
    try:
        from app.core.ffmpeg import capabilities

        raw = capabilities()
        caps = {k: bool(raw.get(k)) for k in keys}  # the binary path is deliberately not exposed
    except Exception as exc:  # noqa: BLE001
        log.warning("FFmpeg capability probe failed: %s", exc)
        caps = dict.fromkeys(keys, False)
    with _ffmpeg_lock:
        _ffmpeg_cache = (time.monotonic(), caps)
    return dict(caps)


def _safe(fn, default):  # noqa: ANN001, ANN202
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - capability reporting must never fail the endpoint
        log.warning("Capability check %s failed: %s", getattr(fn, "__name__", fn), exc)
        return default


def _whisper() -> dict[str, Any]:
    s = get_settings()

    def available() -> bool:
        from app.modules.transcription.service import whisper_available

        return whisper_available()

    return {"available": _safe(available, False), "model": s.whisper_model, "device": s.whisper_device}


def _face_detectors() -> list[str]:
    from app.modules.faces.detector import available_detectors

    return list(available_detectors())


def _google_missing() -> list[str]:
    from app.integrations.google_oauth import missing_env

    return list(missing_env())


def _instagram_missing() -> list[str]:
    from app.integrations.meta_oauth import missing_env

    return list(missing_env())


def _disk_free() -> int:
    from app.modules.storage.service import disk_free_bytes

    return disk_free_bytes()


def _fonts_installed() -> int:
    d = get_settings().fonts_dir
    if not d.is_dir():
        return 0
    return sum(1 for p in d.rglob("*") if p.is_file() and p.suffix.lower() in (".ttf", ".otf"))


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe (``app`` lets scripts tell ClipFusion apart from other programs on the port)."""
    return {"status": "ok", "app": "clipfusion"}


def _check_database() -> str:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return "ok"


def _check_redis() -> str:
    s = get_settings()
    uses_redis = s.worker_mode == "celery" or (s.rate_limit_storage_uri or "").startswith("redis")
    if not uses_redis:
        return "not_used"
    import redis

    client = redis.Redis.from_url(s.rate_limit_storage_uri if (s.rate_limit_storage_uri or "").startswith("redis")
                                  else s.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    finally:
        client.close()
    return "ok"


def _check_ffmpeg() -> str:
    from app.core.ffmpeg import ffmpeg_bin

    ffmpeg_bin()
    return "ok"


@router.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness probe: database, Redis (when used) and FFmpeg. 503 when any check fails."""
    checks: dict[str, str] = {}
    for name, fn in (("database", _check_database), ("redis", _check_redis), ("ffmpeg", _check_ffmpeg)):
        try:
            checks[name] = fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("Readiness check %s failed: %s", name, exc)
            checks[name] = f"error: {exc.__class__.__name__}"
    ok = all(v in ("ok", "not_used") for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded",
                                                                 "checks": checks})


def _gpu() -> dict[str, Any]:
    from app.core.gpu import describe

    return describe()


def _ai_providers() -> dict[str, str]:
    from app.modules.ai.registry import describe

    return describe()


@router.get("/system/capabilities")
def capabilities() -> dict[str, Any]:
    """Which features are really available on this deployment (no secrets are exposed)."""
    s = get_settings()
    google_missing = _safe(_google_missing, ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
    instagram_missing = _safe(_instagram_missing, [])
    return {
        "app": "clipfusion",
        "api_version": 2,
        "ffmpeg": _ffmpeg_caps(),
        "whisper": _whisper(),
        "face_detectors": _safe(_face_detectors, []),
        "llm": {"configured": s.llm_configured, "model": s.llm_model},
        "youtube_configured": not google_missing,
        "instagram_configured": not instagram_missing,
        "instagram_auth_flow": s.instagram_auth_flow,
        "google_missing_env": google_missing,
        "instagram_missing_env": instagram_missing,
        "storage_backend": s.storage_backend,
        "worker_mode": s.worker_mode,
        "youtube_api_key_configured": bool(s.youtube_api_key),
        "subtitles": False,  # API v2: clips are exported clean
        "gpu": _safe(_gpu, {"cuda": False}),
        "ai_providers": _safe(_ai_providers, {}),
        "disk_free_bytes": _safe(_disk_free, None),
        "fonts_installed": _safe(_fonts_installed, 0),
    }
