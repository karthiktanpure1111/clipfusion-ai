# ClipFusion REST API (v1) — contract

> **Upgraded by [API_V2.md](API_V2.md)** (no subtitles, no approve/reject, automatic processing,
> sequential YouTube uploads, verified cleanup, storage). This file is kept in sync with it.

Base URL: `{BACKEND_PUBLIC_URL}/api/v1`, by default **`http://localhost:8010/api/v1`**. JSON everywhere unless noted. IDs are UUID strings,
times are ISO-8601 UTC strings, durations/positions are seconds (float).

**Auth**: httpOnly cookies `cf_access` / `cf_refresh` (set by auth endpoints) or
`Authorization: Bearer`. Cookie-authenticated non-GET requests must send
`X-Requested-With: ClipFusion`. Frontend uses `credentials: "include"`.

**Errors**: `{"error": {"code": str, "message": str, "details": any}}` with the HTTP status.
Notable codes: `unauthorized`, `token_expired`, `forbidden`, `not_found`, `validation_failed`,
`conflict`, `framing_warnings_unacknowledged` (409, details = FramingWarning[]),
`integration_not_configured` (503, message explains what is missing), `dependency_missing`,
`quota_exceeded`, `rate_limited`, `insufficient_disk_space` (507), `rights_not_confirmed` (422),
`invalid_youtube_url` (422), `youtube_video_unavailable` (404), `youtube_auth_expired`, `platform_url_refused`
(422, YouTube/streaming links for attach-link/cloud import), `original_deleted` (409), `clip_removed` (409),
`clip_uploading` (409), `upload_in_progress` (409), `nothing_to_upload` (409), `clip_not_ready` (409).

**Pagination** (core endpoints): `?limit=&offset=` → `Page<T> = {items: T[], total, limit, offset}`.
Integration endpoints (`/accounts`, `/publishing`, `/schedules`, `/analytics`) return plain
arrays/objects — see their Pydantic models in `backend/app/modules/{accounts,publishing,scheduling,analytics}/schemas.py`
(or `service.py` for analytics).

Domain types (`FramingConfig`, `CompiledLayout`, `ClipVisionAnalysis`, `FramingWarning`,
`ClipMetadata`, `RenderOptions`, `TranscriptSegment`) are exactly the
Pydantic models in `backend/app/schemas/*.py`.

`*_url` fields are short-lived signed URLs (may be `null`); refetch the resource to renew.

---

## Auth — `app/modules/auth`
| Method | Path | Body | Response |
|---|---|---|---|
| POST | /auth/register | `{email, password, full_name, timezone?}` | 201 `AuthOut` |
| POST | /auth/login | `{email, password}` | `AuthOut` |
| POST | /auth/refresh | `{refresh_token?}` (cookie used when absent) | `AuthOut` |
| POST | /auth/logout | `{refresh_token?}` | 204 |
| GET | /auth/me | – | `UserOut` |
| GET | /auth/google/login | – | `{authorization_url}` (503 when Google not configured) |
| GET | /auth/google/callback | `?code&state` | 302 → `{FRONTEND_URL}/app` or `/login?error=` |

`AuthOut = {access_token, refresh_token, token_type: "bearer", expires_in, user: UserOut}`
`UserOut = {id, email, full_name, avatar_url, timezone, role, settings: UserSettings, has_password, google_linked, created_at}`
`UserSettings = {default_processing_mode: "fast"|"balanced"|"best" ("balanced"), default_min_duration: number (20), default_max_duration: number (60), default_max_clips: int (10), default_privacy: "public"|"unlisted"|"private" ("private"), auto_delete_original: bool (false), default_clip_durations: number[] (legacy, [30,60]), default_use_llm: bool (true), default_framing_preset_id: uuid|null, publishing_paused: bool, notify_email: bool}`

## Users — `app/modules/users`
GET /users/me → `UserOut` · PATCH /users/me `{full_name?, timezone?, settings?: Partial<UserSettings>}` → `UserOut` ·
POST /users/me/password `{current_password?, new_password}` → 204 · DELETE /users/me → 204

## Uploads (resumable, chunked) — `app/modules/uploads`
| Method | Path | Body | Response |
|---|---|---|---|
| POST | /uploads/init | `{filename, size, mime_type?}` | `UploadOut` (507 `insufficient_disk_space` when the disk cannot hold the file) |
| PUT | /uploads/{id}/chunks/{index} | raw bytes (`application/octet-stream`), exact chunk length | `{received_chunks: int[], complete: bool}` |
| GET | /uploads/{id} | – | `UploadOut` (resume: skip received chunks) |
| POST | /uploads/{id}/complete | `{title?, rights_confirmed: true, rights_statement?, video_source_id?, attribution?, auto_analyze: bool = true, analysis?: AnalysisRequest}` | `SourceOut` (+ `duplicate: true` when the same file already exists; processing starts automatically) |
| DELETE | /uploads/{id} | – | 204 |

`UploadOut = {upload_id, filename, size, chunk_size, total_chunks, received_chunks: int[], status, expires_at}`

## Sources — `app/modules/sources`
`SourceOut = {id, source_type: "upload"|"youtube"|"cloud", status: "pending_upload"|"metadata_only"|"importing"|"uploaded"|"queued"|"processing"|"ready"|"failed", title, description, original_filename, file_size, mime_type, duration, width, height, fps, has_audio, has_file, original_available, original_bytes, original_deleted_at, processing_mode, attribution, transcript_language, thumbnail_url, youtube_video_id, youtube_url, youtube_metadata: object, has_retention: bool, retention_points: RetentionPoint[]|null (detail only), cloud_provider, rights_confirmed, rights_confirmed_at, analysis_options: object, warnings: string[], error, processed_at, clip_count, active_job: JobOut|null, duplicate?: bool, created_at, updated_at}`

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | /sources | `?limit&offset&status&q` | `Page<SourceOut>` |
| GET / PATCH / DELETE | /sources/{id} | PATCH `{title?, description?, attribution?}` | `SourceOut` / 204 |
| POST | /sources/youtube | `{url, rights_confirmed: true, rights_statement?, attribution?}` | `SourceOut` (official metadata only; no ownership needed; never downloads from YouTube) |
| POST | /sources/{id}/attach-link | `{url, rights_confirmed: true, auto_analyze: true, analysis?}` | `SourceOut` (importing; direct HTTPS file links only) |
| POST | /sources/{id}/delete-original | – | `{id, status: "deleted", freed_bytes, source: SourceOut}` |
| GET | /sources/{id}/pipeline | – | `PipelineOut` (see API_V2.md) |
| POST | /sources/{id}/retention/refresh | – | `SourceOut` |
| POST | /sources/cloud | `{provider: "url"|"google_drive", url?, file_id?, title?, rights_confirmed: true, rights_statement?, attribution?, auto_analyze: true, analysis?}` | `SourceOut` (status importing, `active_job`) |
| POST | /sources/{id}/analyze | `AnalysisRequest + {rights_confirmed: true}` (`AnalysisRequest` in API_V2.md; `whisper_model?`, `min_score?` also accepted) | `JobOut` |
| GET | /sources/{id}/transcript | – | `{language, model, text, word_count, duration, segments: TranscriptSegment[]}` |
| GET | /sources/{id}/media-url | – | `{url, browser_playable, expires_in}` |
| POST | /sources/{id}/clips | `{start, end, title?, refine?: bool}` | `ClipDetail` (manual clip; metadata + clip_vision job created) |

## Jobs — `app/modules/jobs`
`JobOut = {id, job_type, status: "queued"|"running"|"retrying"|"succeeded"|"failed"|"cancelled", stage, progress (0..1), message, error, attempts, max_attempts, video_source_id, clip_id, queue_item_id, source_title, clip_title, params, result, created_at, started_at, finished_at, next_attempt_at}`
GET /jobs `?status&job_type&video_source_id&clip_id&active=true&limit&offset` → `Page<JobOut>` · GET /jobs/{id} ·
POST /jobs/{id}/cancel → `JobOut` · POST /jobs/{id}/retry → `JobOut`

## Clips — `app/modules/clips`
`ClipOut = {id, video_source_id, source_title, sequence, status, start_time, end_time, duration, target_duration, highlight_score (AI estimate 0-100), score_breakdown: {feature: number}, category, reasons: string[], transcript_text, hook_text, detection_method: "signals"|"signals+llm"|"manual", retention_score (REAL YouTube audience watch ratio or null), title, social_metadata: ClipMetadata, render_status: "not_rendered"|"queued"|"rendering"|"rendered"|"failed"|"stale", render_is_current, needs_framing_review, thumbnail_url, proxy_url, proxy_start, output_url, output_duration, rendered_at, framing_mode, warning_count, blocking_warning_count, warnings_acknowledged, upload_status, youtube_video_id, youtube_url, uploaded_at, upload_error, cleanup_status, media_available, media_bytes, freed_bytes, removed_at, created_at, updated_at}` (field meanings: API_V2.md)
`ClipDetail = ClipOut + {render_options: RenderOptions, render_error, active_jobs: JobOut[], queue_items: [{id, platform, status, scheduled_at, published_at, external_url, last_error}], source: {id, title, duration, width, height, fps, attribution, original_available}}`

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | /clips | `?video_source_id&status&render_status&min_score&q&sort=sequence|score|created|start&include_removed=false&limit&offset` | `Page<ClipOut>` (default sort `sequence` with `video_source_id`) |
| GET | /clips/{id} | – | `ClipDetail` |
| DELETE | /clips/{id} | – | `{id, status: "removed", freed_bytes}` (Remove Clip; 409 `clip_uploading`) |
| PATCH | /clips/{id} | `{start_time?, end_time?, title?, social_metadata?, render_options?}` | `ClipDetail` (render becomes stale; framing re-analysed and re-rendered automatically when the range changes; 409 `clip_removed`) |
| POST | /clips/bulk | `{ids, action: "render"|"remove", acknowledge_warnings?}` | `{results: [{id, ok, error: {code, message}|null}]}` |
| POST | /clips/{id}/metadata/regenerate | `{use_llm?}` | `JobOut` |

## Framing — `app/modules/framing` (router has no prefix; paths are absolute)
`FramingOut = {clip_id, mode, config: FramingConfig, vision: ClipVisionAnalysis|null, warnings: FramingWarning[], warnings_acknowledged, auto_generated, compiled: CompiledLayout, source: {width, height, fps, duration}, clip_start, clip_end, clip_duration, proxy_url, proxy_start}`
(`compiled` keyframes and `vision` times are clip-relative; the proxy video time of clip time t is `t + clip_start - proxy_start`.)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | /clips/{id}/framing | – | `FramingOut` |
| PUT | /clips/{id}/framing | `{config}` | `FramingOut` (smart keyframes recomputed server-side; warnings recomputed; acknowledgement reset if warnings changed) |
| POST | /clips/{id}/framing/check | `{config}` | `{config, warnings, compiled}` (dry run, used live by the editor) |
| POST | /clips/{id}/framing/suggest | – | `{config, warnings, compiled}` (AI suggestion, not saved) |
| POST | /clips/{id}/framing/analyze | – | `JobOut` (re-run face detection/tracking) |
| POST | /clips/{id}/framing/acknowledge | – | `FramingOut` |
| POST | /framing/apply | `{clip_ids, config?, preset_id?}` | `{results: [{clip_id, ok, warnings: FramingWarning[], error}]}` |
| GET / POST | /framing-presets | POST `{name, config, is_default?}` | `FramingPresetOut[]` / `FramingPresetOut` |
| PATCH / DELETE | /framing-presets/{id} | `{name?, config?, is_default?}` | `FramingPresetOut` / 204 |

`FramingPresetOut = {id, name, mode, config, is_default, created_at, updated_at}` (keyframes stripped; normalised rects are re-fitted to each clip's source aspect when applied).

## Subtitles — removed in v2
Clips are exported clean. `/clips/{id}/subtitles*`, `/subtitles/*`, `/subtitle-presets*` and `/fonts*` no longer exist.

## Assets (custom fonts were removed in v2)

`AssetOut = {id, kind: "intro"|"outro"|"watermark"|"background", name, original_filename, mime_type, file_size, duration, width, height, url, created_at}`
GET /assets `?kind` → `AssetOut[]` · POST /assets multipart `file`, `kind`, `name?` → `AssetOut` · DELETE /assets/{id} → 204

## Rendering — `app/modules/rendering` (no prefix)
| Method | Path | Body | Response |
|---|---|---|---|
| POST | /clips/{id}/render | `{acknowledge_warnings: bool}` | `JobOut` · 409 `framing_warnings_unacknowledged` (details: FramingWarning[]) |
| GET | /clips/{id}/render | – | `{render_status, render_is_current, job: JobOut|null, output_url, output_size, output_duration, rendered_at, render_error}` |
| GET | /clips/{id}/download | – | `{url, filename}` |
| POST | /clips/{id}/preview-frame | `{t, framing?: FramingConfig}` | `image/jpeg` — one frame rendered by the real export renderer (exact WYSIWYG check, no subtitles) |

## Notifications, dashboard, system, media
`NotificationOut = {id, type, title, body, link, data, read_at, created_at}`
GET /notifications `?unread_only&limit&offset` → `{items, total, unread_count}` · POST /notifications/{id}/read → `NotificationOut` · POST /notifications/read-all → `{updated}`

GET /dashboard/summary → `{total_videos, total_clips (non-removed), processing_videos, ready_to_upload (generated & not uploaded), uploaded_clips, scheduled_posts, published_clips, failed_uploads, next_publish_at, clips_per_day: [{date, count}], published_per_day: [{date, youtube, instagram}], recent_clips: ClipOut[], active_jobs: JobOut[], upcoming: QueueItemOut[]}`

GET /health → `{status: "ok", app: "clipfusion"}` · GET /health/ready → `{status, checks: {database, redis, ffmpeg}}` ·
GET /system/capabilities → `{ffmpeg: {ffmpeg, ffprobe, libx264, libass}, whisper: {available, model, device}, face_detectors: string[], llm: {configured, model}, youtube_configured, instagram_configured, instagram_auth_flow, google_missing_env: string[], instagram_missing_env: string[], storage_backend, worker_mode, fonts_installed, app: "clipfusion", api_version: 2, youtube_api_key_configured, subtitles: false, disk_free_bytes}`

## Upload runs & storage (v2)
`/upload-runs` (POST, GET `?video_source_id&active`, GET `/{id}`, POST `/{id}/retry`, POST `/{id}/cancel`) and
GET `/storage` — see [API_V2.md](API_V2.md).

GET /media/{token} — signed media file with HTTP Range support (no auth header needed).

## Integrations (implemented — read the code for exact models)
`/accounts` (list, `/requirements`, `/connect/{platform}?include_drive=`, `/callback/{provider}`, DELETE `/{id}`, POST `/{id}/check`),
`/publishing` (`/queue` GET/POST, PATCH `/queue/{id}`, POST `/queue/{id}/cancel|retry|publish-now`, `/history`, `/calendar?start&end`, `/next`, POST `/pause|/resume`),
`/schedules` (CRUD, POST `/{id}/pause|resume`, GET `/{id}/preview?count=`),
`/analytics` (`/overview?days=`, `/clips/{clip_id}`, POST `/refresh`).
