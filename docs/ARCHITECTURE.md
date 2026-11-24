# ClipFusion AI — Architecture & Contracts

This document is **normative**: backend modules, workers and the frontend must follow the
contracts below. Domain schemas live in code (`backend/app/schemas/*.py`) and are the source
of truth; this file explains how the pieces fit together.

## 1. System overview

```
 Browser (Next.js 16, React 19, Tailwind 4, shadcn/ui)
   │  REST/JSON  (cookies: cf_access/cf_refresh, header X-Requested-With: ClipFusion)
   ▼
 FastAPI  (backend/app/main.py, prefix /api/v1)
   │  SQLAlchemy 2 ──► PostgreSQL (SQLite for tests)
   │  enqueue() ─────► Redis ─► Celery workers (queues: analysis, render, publish, default)
   │                   └─(WORKER_MODE=local: in-process thread pools, same task code)
   │  Storage ───────► local disk  |  S3 / Cloudflare R2 / MinIO
   ▼
 External (official APIs only): YouTube Data API v3, YouTube Analytics API, Google OAuth,
 Instagram Graph API (Instagram Login or Facebook Login), Google Drive API, Anthropic API.
```

Media pipeline:

```
 upload / cloud import ──► validate (ext, magic bytes, ffprobe) ──► storage (original)
        │
        ▼  job: analysis (queue "analysis")
 probe → extract audio (16 kHz mono) → audio analysis (loudness, silences, peaks)
       → transcription (faster-whisper, word timestamps, VAD)
       → scene detection (PySceneDetect) → visual activity (frame-difference motion)
       → highlight detection (multi-signal scoring [+ Claude ranking if configured]
         [+ real YouTube retention if authorised]) → boundary refinement (sentence-aligned,
         silence-trimmed) → overlap suppression → GeneratedClip rows (+ metadata)
        │
        ▼  per clip, job: clip_vision (queue "analysis")
 landscape proxy for the editors + thumbnail → face detection (YuNet/MediaPipe/Haar)
       → tracking (IoU/centroid association) → speaker activity → facecam detection
       → suggested FramingConfig (never crops a face; falls back to full_frame)
       → crop-protection warnings → default captions from transcript words
        │
        ▼  user edits in Smart Framing Editor + Subtitle Studio (live canvas preview)
        ▼  job: render (queue "render")  — refused while blocking warnings are unacknowledged
 compile layout → per-frame compositor (OpenCV) + caption renderer (Pillow) → FFmpeg
 libx264 encode (1080x1920, yuv420p, faststart) + audio (loudnorm, silence removal)
 → intro/outro concat → thumbnail → storage (render-{hash}.mp4)
        │
        ▼  publishing queue (Postgres) ← schedules (interval/times/days, tz, daily limits)
 dispatcher (every 60 s) → job: publish (queue "publish") → YouTube resumable upload /
 Instagram Reels container + resumable upload → status polling → history + notifications
```

## 2. Repository layout

```
backend/
  app/main.py                 FastAPI app factory, routers, middleware, lifespan
  app/core/                   config, database, security, crypto, storage, ffmpeg, deps, errors,
                              logging, ratelimit   (shared — do not duplicate)
  app/models/                 SQLAlchemy models (all tables)        (shared)
  app/schemas/                media.py framing.py subtitles.py render.py (domain contracts; shared)
  app/workers/                registry.py local.py celery_app.py jobs.py (framework; shared)
                              tasks_processing.py  tasks_publishing.py  tasks_maintenance.py
  app/integrations/           HTTP clients for external APIs (Google, YouTube, Instagram, Drive, URL import)
  app/modules/<module>/       one package per requirement-§13 module:
     auth users uploads sources jobs clips framing subtitles rendering notifications dashboard
     assets fonts system media analysis transcription highlights clipping metadata faces
     tracking accounts publishing scheduling analytics
  alembic/                    migrations
  tests/                      pytest (conftest.py, helpers/media.py)
frontend/                     Next.js app (src/app, src/components, src/lib)
docs/                         this file, API.md, INTEGRATIONS.md, DEPLOYMENT.md
```

Module ownership inside a package: engine files (pure functions, no FastAPI) vs.
`router.py` / `service.py` / `schemas.py` (HTTP + DB glue). Engines never import routers.

## 3. Engine API (Python signatures — the boundary between workers/routers and engines)

`ProgressFn = Callable[[float, str | None], None]` — fraction 0..1 of the sub-step, optional message.
All engine functions are synchronous and raise exceptions on failure.

### 3.1 Analysis engine
```python
# app/modules/analysis/probe.py
def probe_media(path: Path) -> MediaInfo            # supports audio-only files (width=height=0, fps=0)
def extract_frame(path: Path, t: float, dest: Path, width: int = 640) -> Path   # JPEG thumbnail
def validate_video_file(path: Path) -> MediaInfo    # magic bytes + ffprobe; raises ValidationFailed

# app/modules/analysis/audio.py
def extract_audio_wav(src: Path, dest: Path, *, sample_rate: int = 16000,
                      start: float | None = None, end: float | None = None) -> Path
def analyze_audio(wav: Path, *, offset: float = 0.0) -> AudioAnalysis

# app/modules/analysis/scenes.py
def detect_scenes(video: Path, *, progress: ProgressFn | None = None) -> list[SceneCut]

# app/modules/analysis/visual.py
def analyze_visual_activity(video: Path, *, sample_hz: float = 1.0,
                            progress: ProgressFn | None = None) -> VisualActivity

# app/modules/transcription/service.py
def whisper_available() -> bool
def transcribe(audio: Path, *, model_name: str | None = None, language: str | None = None,
               offset: float = 0.0, progress: ProgressFn | None = None) -> TranscriptData

# app/modules/highlights/detector.py
def detect_highlights(*, transcript: TranscriptData, audio: AudioAnalysis, scenes: list[SceneCut],
                      visual: VisualActivity, media: MediaInfo, options: AnalysisOptions,
                      source_title: str | None = None,
                      progress: ProgressFn | None = None) -> list[HighlightCandidate]
# app/modules/highlights/llm.py  — Claude ranking (only when settings.llm_configured and options.use_llm)

# app/modules/clipping/boundaries.py
def refine_boundaries(start: float, end: float, *, transcript: TranscriptData, audio: AudioAnalysis,
                      media_duration: float, target: float | None = None,
                      min_len: float = 8.0, max_len: float = 180.0) -> tuple[float, float]
def suppress_overlaps(cands: list[HighlightCandidate], max_iou: float) -> list[HighlightCandidate]

# app/modules/clipping/proxy.py
def make_clip_proxy(source: Path, start: float, end: float, dest: Path, *, pad: float = 1.0,
                    height: int = 720) -> float          # returns proxy_start (source time at proxy t=0)

# app/modules/metadata/generator.py
def generate_clip_metadata(*, transcript_text: str, source_title: str | None = None,
                           category: str | None = None, hook: str | None = None,
                           use_llm: bool = True) -> ClipMetadata

# app/modules/analysis/pipeline.py  — orchestrates the whole source analysis
def analyze_source(source: Path, workdir: Path, options: AnalysisOptions, *,
                   source_title: str | None = None,
                   existing_transcript: TranscriptData | None = None,
                   on_stage_result: Callable[[str, Any], None] | None = None,   # "media"|"audio"|"transcript"|"scenes"|"visual"
                   progress: Callable[[str, float, str | None], None] | None = None,  # (stage, overall 0..1, msg)
                   ) -> SourceAnalysisResult   # dataclass: media, transcript, audio, scenes, visual, highlights
```

### 3.2 Vision / framing / subtitles / rendering engine
```python
# app/modules/faces/detector.py
class FaceDetection: rect: Rect (normalised); score: float; landmarks: list[tuple[float,float]] | None
def get_face_detector(kind: str = "auto") -> FaceDetector   # .name, .detect(frame_bgr) -> list[FaceDetection]
def available_detectors() -> list[str]

# app/modules/tracking/tracker.py
def analyze_clip_vision(video: Path, start: float, end: float, media: MediaInfo, *,
                        sample_fps: float = 4.0, speech: list[tuple[float, float]] | None = None,
                        progress: ProgressFn | None = None) -> ClipVisionAnalysis

# app/modules/framing/geometry.py  (SHARED, already implemented — read-only)
def interpolate_keyframes(keyframes, t) -> Rect ; def rect_at(layer: CompiledLayer, t: float) -> Rect
def fit_rects(src_px, dst_px, fit) -> tuple[src_crop_px, dst_draw_px]   # (x, y, w, h) tuples
def visible_source_rect_px(layer, t, source_w, source_h, canvas_w, canvas_h) -> Rect
def source_to_canvas(face_rect, layer, t, source_w, source_h, canvas_w, canvas_h) -> Rect | None
def intersection(a, b) -> Rect | None ; to_px / to_norm

# app/modules/framing/layout.py
def default_framing_config(source_w: int, source_h: int, mode: str = "full_frame") -> FramingConfig
def compile_layout(config: FramingConfig, source_w: int, source_h: int) -> CompiledLayout

# app/modules/framing/smart_crop.py
def compute_smart_keyframes(vision: ClipVisionAnalysis, smart: SmartCropConfig, *, source_w: int,
                            source_h: int, duration: float, output_aspect: float = 9 / 16) -> list[Keyframe]

# app/modules/framing/suggest.py
def suggest_framing(vision: ClipVisionAnalysis | None, media: MediaInfo, duration: float) -> FramingConfig

# app/modules/framing/protection.py
def analyze_protection(config: FramingConfig, vision: ClipVisionAnalysis | None, *, source_w: int,
                       source_h: int, duration: float, subtitle_style: SubtitleStyle | None = None,
                       captions: list[Caption] | None = None) -> list[FramingWarning]

# app/modules/subtitles/captions.py
def build_captions(words: list[TranscriptWord], *, clip_start: float, clip_end: float,
                   style: SubtitleStyle) -> list[Caption]            # source-time words -> clip-relative captions
def split_caption(c: Caption, at_word: int) -> tuple[Caption, Caption]
def merge_captions(a: Caption, b: Caption) -> Caption
def retext_caption(c: Caption, new_text: str) -> Caption            # keeps timing, redistributes words
def normalize_captions(captions: list[Caption], duration: float) -> list[Caption]  # sort, clamp, fix overlaps

# app/modules/subtitles/templates.py
TEMPLATES: dict[str, tuple[str, SubtitleStyle]]   # key -> (display name, style): gaming, podcast, minimal,
                                                  # bold_viral, professional, reaction  (+ "custom" in UI)

# app/modules/subtitles/fonts.py
BUILTIN_FONTS: list[dict]          # [{"family","category","weights":[...],"source":"google|system|fallback"}]
def resolve_font(family: str, weight: int, italic: bool, custom: list[FontFile] = ()) -> Path
def ensure_builtin_fonts() -> dict  # downloads OFL fonts into settings.fonts_dir (idempotent)

# app/modules/rendering/renderer.py
def render_clip(spec: RenderSpec, *, progress: ProgressFn | None = None) -> RenderResult
def render_preview_frame(spec: RenderSpec, t: float, dest: Path) -> Path   # single JPEG (server-side preview)
```

### 3.3 Integrations (external APIs)
```python
# app/integrations/youtube.py
def parse_video_id(url: str) -> str | None
def fetch_video_metadata(db: Session, user: User, video_id: str) -> dict
    # {"video_id","title","description","channel_id","channel_title","published_at","duration_seconds",
    #  "thumbnail_url","view_count","like_count","comment_count","tags","is_owner","owner_account_id"}
def fetch_retention_curve(db: Session, account: ConnectedAccount, video_id: str) -> list[RetentionPoint]
class YouTubeUploader: resumable upload, status polling, statistics

# app/integrations/cloud_import.py
def validate_import_url(url: str) -> None          # https only, SSRF-safe, refuses YouTube/streaming hosts
def download_url(url: str, dest_dir: Path, *, max_bytes: int,
                 progress: ProgressFn | None = None) -> tuple[Path, str, str | None]  # path, filename, mime

# app/integrations/google_drive.py
def download_drive_file(access_token: str, file_id: str, dest_dir: Path, *, max_bytes: int,
                        progress: ProgressFn | None = None) -> tuple[Path, str, str | None]

# app/modules/accounts/tokens.py
def get_access_token(db: Session, account: ConnectedAccount) -> str   # refreshes + persists when expired
def find_google_account(db: Session, user: User, *, need_scope: str | None = None) -> ConnectedAccount | None
```

## 4. Jobs & workers

* Every background unit of work is a `processing_jobs` row created with
  `app.workers.jobs.create_job` and dispatched with `dispatch_job(db, job, task_name)`.
* Task functions are registered with `@task(name, queue, job_type)` and implemented via
  `run_job(job_id, handler, task_name=..., on_final_failure=...)` (status, attempts,
  exponential retry, heartbeat, cancellation, idempotency).
* `idempotency_key` prevents duplicate active jobs (`analysis:{source_id}`, `render:{clip_id}`,
  `clip_vision:{clip_id}`, `publish:{queue_item_id}`).
* Periodic tasks (`periodic(name, every_seconds)`): `reap_stale_jobs` (300 s — re-dispatch
  jobs whose heartbeat is older than `JOB_HEARTBEAT_TIMEOUT_SECONDS` or that are queued but
  never started), `cleanup_temp` (3600 s), `cleanup_expired_uploads` (3600 s),
  `dispatch_due_publications` (60 s), `poll_remote_publications` (120 s),
  `refresh_account_tokens` (1800 s), `refresh_platform_analytics` (21600 s).
* `WORKER_MODE=local` runs the same tasks in-process (and re-dispatches queued/running
  jobs at startup). `WORKER_MODE=celery` uses Redis + Celery + beat.

Task names (fixed): `import_source`, `analyze_source`, `clip_vision`, `transcribe_clip`,
`generate_metadata`, `render_clip`, `publish_queue_item`, `dispatch_due_publications`,
`poll_remote_publications`, `refresh_account_tokens`, `refresh_platform_analytics`,
`reap_stale_jobs`, `cleanup_temp`, `cleanup_expired_uploads`.

## 5. Security model

* Auth: argon2 password hashes; 15-min JWT access token + rotating opaque refresh token
  (hash stored, family revocation on reuse). Delivered as httpOnly SameSite=Lax cookies
  (`cf_access`, `cf_refresh` scoped to `/api/v1/auth`) **and** in the JSON body for API clients.
* CSRF: cookie-authenticated unsafe requests require `X-Requested-With: ClipFusion`.
* Access control: every query is filtered by `user_id`; use `get_owned()` (404 on mismatch).
* OAuth tokens: Fernet-encrypted (`TOKEN_ENCRYPTION_KEYS`, rotation supported); OAuth `state`
  is an HMAC-signed payload (user id, provider, nonce, PKCE verifier, 10-min expiry).
* Uploads: extension allow-list, magic-byte sniffing, ffprobe validation (must contain a
  video stream, duration limits), size limits, server-generated storage keys, chunk files in
  TEMP_DIR, expired sessions cleaned up.
* Media access: short-lived HMAC-signed URLs (`/api/v1/media/{token}`, Range supported) or
  S3 presigned URLs. Raw storage keys are never exposed.
* Cloud import: https only, DNS-resolved IPs must be public (SSRF), redirects re-validated,
  size cap, YouTube/streaming-site hosts refused (no unauthorised downloading).
* Rate limiting: slowapi (Redis in production) — stricter on auth endpoints.
* Secrets only from environment; logs redact tokens.

## 6. Honesty rules (product requirements)

* AI highlight scores are labelled **"AI highlight score (estimate)"** everywhere and are
  never presented as views or retention. Real YouTube retention (Analytics API, owner only)
  is shown separately and labelled with its source.
* When the LLM is not configured the system says so (`detection_method="signals"`,
  metadata `source="extractive"`), it does not pretend.
* `/api/v1/system/capabilities` reports which features are really available (FFmpeg, Whisper
  model, face detector, LLM, YouTube/Instagram credentials, storage, worker mode); the UI
  disables or explains features that are unavailable instead of simulating them.
* The renderer refuses to export while blocking framing warnings are unacknowledged
  (HTTP 409 `framing_warnings_unacknowledged`). Faces are never silently cropped.

## 7. Framing & subtitles

See the module docstrings in `backend/app/schemas/framing.py` and
`backend/app/schemas/subtitles.py` — they define coordinate systems, fit modes, keyframe
interpolation, background blur, caption layout and animation semantics. The frontend
preview (`frontend/src/lib/framing`, `frontend/src/lib/subtitles`) and the backend renderer
implement the same rules so the live preview matches the export.

Mode compilation (`compile_layout`):

| mode | layers (z order) |
|---|---|
| smart | `main`: source = smart keyframes (or centred 9:16 when none), dest = full canvas, cover |
| manual | `main`: source = manual.crop (+ keyframes), dest = full canvas, fit = manual.fit |
| facecam_gameplay | `facecam` (role face) dest = top/bottom `split` band; `gameplay` (role content) dest = remaining band minus `gap`; both with their fits |
| pip | `main` dest = full canvas, fit = main_fit; `overlay` (role face) source = overlay_source, dest = overlay_dest, radius/border/shadow |
| full_frame | `main`: source = full frame, fit = contain, dest = full width × `scale`, vertically placed by `vertical_position` |

`background_source` = the main/gameplay layer source for blur (full frame for full_frame).

## 8. Publishing

* Connect accounts with official OAuth (Google for YouTube; Instagram Login or Facebook
  Login for Instagram professional accounts). Never passwords, never unofficial endpoints.
* Queue item lifecycle: `pending_approval` → `scheduled` → (`rendering`) → `publishing` →
  `processing_remote` → `published` | `failed` (retry with backoff up to `max_attempts`) |
  `cancelled`.
* Duplicate prevention: `idempotency_key = "{clip_id}:{account_id}"` (unique) + a check of
  `publishing_history` for an existing successful publication.
* Schedules: interval (60/120/180/360/custom minutes, optional daily window) or specific
  times; days of week; IANA timezone; `daily_limit`; pause/resume; review vs automatic
  mode. Slot assignment skips slots already used by the same account.
* Quotas: YouTube buckets tracked per Pacific day in `api_quota_usage` (uploads 100/day, search 100/day, other methods 10,000 units/day);
  Instagram `content_publishing_limit` checked before publishing.
