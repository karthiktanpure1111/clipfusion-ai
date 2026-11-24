"""Application configuration.

All settings come from environment variables (or a `.env` file). Nothing secret is
hardcoded; see the repository-level `.env.example` for the complete list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "ClipFusion AI"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    backend_public_url: str = "http://localhost:8010"  # ClipFusion API port (8000 is often taken)
    frontend_url: str = "http://localhost:3000"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    log_level: str = "INFO"
    log_json: bool = False

    # ------------------------------------------------------------- security
    # False (default): no login. Every request acts as one local user, only requests addressed
    # to this machine are accepted (see app.core.deps), and production refuses to start.
    # True: accounts with email/password (and optional Google sign-in).
    auth_required: bool = False
    # No-login mode only serves connections coming from this machine. Containers see the Docker
    # gateway instead of 127.0.0.1: list its network here (CIDR) and publish ports on 127.0.0.1 only.
    no_login_trusted_networks: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # REQUIRED in production. In development a random key is generated per process
    # (sessions are invalidated on restart) and a warning is logged.
    secret_key: str = ""
    # Fernet key(s) used to encrypt OAuth tokens at rest. Comma-separated for rotation:
    # the first key encrypts, all keys decrypt. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_keys: str = ""
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    cookie_secure: bool = False
    cookie_domain: str | None = None
    media_url_ttl_seconds: int = 6 * 3600

    # ------------------------------------------------------------- database
    database_url: str = "postgresql+psycopg://clipfusion:clipfusion@localhost:5432/clipfusion"
    database_echo: bool = False

    # --------------------------------------------------------------- workers
    # "celery" -> tasks are sent to Redis/Celery workers (production).
    # "local"  -> tasks run in an in-process thread pool (single-machine development).
    worker_mode: Literal["celery", "local"] = "celery"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    local_worker_threads: int = 2
    job_heartbeat_timeout_seconds: int = 900
    job_max_attempts: int = 3

    # -------------------------------------------------------------- storage
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_dir: Path = BACKEND_DIR / "data" / "storage"
    temp_dir: Path = BACKEND_DIR / "data" / "tmp"
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_endpoint_url: str | None = None  # e.g. https://<account>.r2.cloudflarestorage.com
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_presign_ttl_seconds: int = 6 * 3600

    # --------------------------------------------------------------- uploads
    max_upload_bytes: int = 20 * 1024 * 1024 * 1024  # 20 GB
    upload_chunk_bytes: int = 16 * 1024 * 1024
    allowed_video_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"]
    )
    max_video_duration_seconds: int = 6 * 3600
    max_font_bytes: int = 10 * 1024 * 1024
    max_asset_bytes: int = 500 * 1024 * 1024
    # Free space that must remain on the storage/temp disks after ingesting or rendering.
    min_free_disk_mb: int = 2048

    # ----------------------------------------------------------------- media
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    models_dir: Path = BACKEND_DIR / "models"
    fonts_dir: Path = BACKEND_DIR / "assets" / "fonts"

    # --------------------------------------------------------- transcription
    whisper_model: str = "small"
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    whisper_compute_type: str = "auto"  # int8 on CPU, float16 on CUDA
    whisper_cache_dir: Path | None = None

    # ----------------------------------------------------------- face models
    face_detector: Literal["auto", "yunet", "mediapipe", "haar"] = "auto"

    # ------------------------------------------------------------------ LLM
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_enabled: bool = True
    llm_max_transcript_chars: int = 600_000

    # ------------------------------------------------------- Google/YouTube
    google_client_id: str = ""
    google_client_secret: str = ""
    youtube_api_key: str = ""  # optional: public metadata lookups without OAuth
    # YouTube Data API quota buckets (https://developers.google.com/youtube/data/determine_quota_cost):
    # videos.insert and search.list have their own buckets (default 100 calls/day each, 1 unit per call);
    # every other method shares the default 10,000 units/day. All reset at midnight Pacific Time.
    youtube_daily_quota: int = 10_000  # units/day for all other methods (videos.list, channels.list, ...)
    youtube_daily_upload_limit: int = 100  # videos.insert calls/day (own bucket)
    youtube_daily_search_limit: int = 100  # search.list calls/day (own bucket)
    youtube_upload_chunk_bytes: int = 8 * 1024 * 1024
    # Clip media is deleted only after videos.list confirms the upload; unconfirmed clips are
    # re-checked by the maintenance worker at this interval.
    upload_verify_interval_seconds: int = 300

    # -------------------------------------------------------- Meta/Instagram
    # "instagram_login" -> Instagram API with Instagram Login (graph.instagram.com)
    # "facebook_login"  -> Instagram Graph API via Facebook Login + linked Page
    # "unified_login"   -> Instagram API with Unified Login (Facebook Login for Business configuration,
    #                      Instagram or Facebook credentials, no Facebook Page needed; graph.facebook.com)
    instagram_auth_flow: Literal["instagram_login", "facebook_login", "unified_login"] = "instagram_login"
    meta_app_id: str = ""
    meta_app_secret: str = ""
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_config_id: str = ""  # unified_login: the login configuration ID (not a secret)
    # Auto-schedule (rendered clips go to YouTube: clip 1 right away, then one per hour) for users who never
    # switched it on or off themselves
    auto_schedule_default: bool = True
    # Redirect URI registered with Meta when it differs from {BACKEND_PUBLIC_URL}/api/v1/accounts/callback/instagram
    # (e.g. https://localhost:8011/api/v1/accounts/callback/instagram served by scripts/https_callback.py)
    instagram_redirect_uri: str = ""
    meta_graph_version: str = "v23.0"
    instagram_daily_publish_limit: int = 50

    # ------------------------------------------------------------ rate limits
    rate_limit_default: str = "300/minute"
    rate_limit_auth: str = "10/minute"
    rate_limit_upload: str = "600/minute"
    rate_limit_storage_uri: str | None = None  # defaults to redis_url in celery mode

    # --------------------------------------------------------------- derived
    @field_validator("cookie_domain", "s3_endpoint_url", "whisper_cache_dir", "ffmpeg_path", "ffprobe_path",
                     "celery_broker_url", "celery_result_backend", "rate_limit_storage_uri", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("cors_origins", "allowed_video_extensions", "no_login_trusted_networks", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _no_login_is_local_only(self) -> Settings:
        if self.environment == "production" and not self.auth_required:
            raise ValueError(
                "AUTH_REQUIRED must be true in production: without login, anyone who can reach the API "
                "could control your videos and connected accounts.")
        return self

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def instagram_configured(self) -> bool:
        if self.instagram_auth_flow == "instagram_login":
            return bool(self.instagram_app_id and self.instagram_app_secret)
        if self.instagram_auth_flow == "unified_login":
            return bool(self.meta_app_id and self.meta_app_secret and self.instagram_config_id)
        return bool(self.meta_app_id and self.meta_app_secret)

    @property
    def llm_configured(self) -> bool:
        return self.llm_enabled and bool(self.anthropic_api_key)

    def oauth_redirect_uri(self, provider: str) -> str:
        """Redirect URI registered with the provider, e.g. .../api/v1/accounts/callback/google."""
        if provider == "instagram" and self.instagram_redirect_uri:
            return self.instagram_redirect_uri.strip()
        return f"{self.backend_public_url.rstrip('/')}{self.api_prefix}/accounts/callback/{provider}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for d in (settings.local_storage_dir, settings.temp_dir, settings.models_dir, settings.fonts_dir):
        Path(d).mkdir(parents=True, exist_ok=True)
    return settings
