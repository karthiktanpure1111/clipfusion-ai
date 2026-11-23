# ClipFusion API — v2 changes (upgrade contract)

Applies on top of [API.md](API.md). Base URL is now **`http://localhost:8010/api/v1`** (port moved from 8000).
Product decisions behind this upgrade:

* **No subtitles.** Clips are exported clean (nothing burned in). The Subtitle Studio, subtitle presets/
  templates and custom fonts are removed from the API and UI. Each clip instead exposes its **transcript
  text**, a **title/caption**, a **description** and **hashtags** (`social_metadata`).
* **No translation.** Transcripts are in the spoken language.
* **No approve/reject.** Workflow: input → automatic processing → clips (in source-timestamp order) →
  review → sequential YouTube upload → that clip's media is deleted after a verified upload.
* **Free local AI only** by default (Claude stays optional if `ANTHROPIC_API_KEY` is set).

## Removed endpoints
`POST /clips/{id}/approve`, `POST /clips/{id}/reject`, bulk actions `approve`/`reject`,
everything under `/clips/{id}/subtitles`, `/subtitles/*`, `/subtitle-presets*`, `/fonts*`,
and the `subtitles` field of `POST /clips/{id}/preview-frame`.

## Processing options (shared by every "start processing" request)
```
AnalysisRequest = {
  mode: "fast" | "balanced" | "best"      (default: user setting, else "balanced"),
  min_duration: number (5..180, default user setting or 20),
  max_duration: number (min..180, default user setting or 60),
  max_clips: int (1..50, default user setting or 10),
  clip_durations?: number[]                (optional explicit targets; default derived from min/max),
  language?: string                        (default auto-detect),
  use_llm?: bool                           (only effective when an LLM key is configured)
}
```

## Inputs — processing starts automatically
| Method | Path | Body | Response |
|---|---|---|---|
| POST | /uploads/{id}/complete | `{title?, rights_confirmed: true, rights_statement?, video_source_id?, attribution?, auto_analyze: bool = true, analysis?: AnalysisRequest}` | `SourceOut` (`active_job` = the analysis job when started) |
| POST | /sources/youtube | `{url, rights_confirmed: true, rights_statement?, attribution?}` | `SourceOut` (status `metadata_only`): title, channel, duration, thumbnail from the official YouTube Data API. No channel ownership is required. Media is **not** downloaded from YouTube (YouTube's Terms do not permit it); attach the creator-provided file (uploads `video_source_id`) or a direct link (below). |
| POST | /sources/{id}/attach-link | `{url, rights_confirmed: true, auto_analyze: true, analysis?}` | `SourceOut` (status `importing`) — direct HTTPS file link (Dropbox, Google Drive public file, OneDrive, S3…) shared by the creator; YouTube/streaming hosts are refused |
| POST | /sources/cloud | `{provider, url?, file_id?, title?, rights_confirmed: true, attribution?, auto_analyze: true, analysis?}` | `SourceOut` |
| POST | /sources/{id}/analyze | `AnalysisRequest + {rights_confirmed: true}` | `JobOut` |

`rights_confirmed` text in the UI: *"I have the creator's permission, a licence, or other rights to reuse and
edit this video."* (ownership of the channel is not required).

`SourceOut` gains: `processing_mode`, `attribution` (credit line, defaults to the YouTube channel + URL for
YouTube sources), `transcript_language`, `original_available` (bool), `original_bytes`.
`PATCH /sources/{id}` also accepts `attribution`.

## Pipeline status (real progress only)
`GET /sources/{id}/pipeline` →
```
PipelineOut = {
  source_id, status: "idle"|"running"|"completed"|"failed", mode, started_at, finished_at, elapsed_seconds,
  eta_seconds: number|null,        // only when derived from measured progress of the running stage
  stages: [{
    key: "ingestion"|"audio"|"transcription"|"video_analysis"|"highlights"|"clip_generation"|"metadata"|"completed",
    label, status: "pending"|"running"|"done"|"failed"|"skipped",
    progress: number|null,         // 0..1 measured, null when not measurable
    started_at, finished_at, seconds, message, error
  }],
  clips: {total, pending, processing, generated, upload_failed, uploaded, removed, cleaned},
  device: {transcription: "cuda"|"cpu"|null, model: string|null}
}
```
`clip_generation` covers face tracking + framing + rendering of every clip (progress = finished/total);
`metadata` = titles/captions/descriptions/hashtags.

Notes: stages can run concurrently (transcription overlaps the video analysis), so several stages may be
`running` at once. `eta_seconds` is the estimated time left **in the running stage** (from its measured
progress; null when no running stage has measured progress). Stages the engine did not need (stored
transcript/analysis reused) are `skipped`. For uploaded files `ingestion` is `done` without timings;
for link imports it carries the import job's measured download progress. A failed stage carries the
user-facing `error` (e.g. FFmpeg could not decode the video); `clip_generation.error` summarises clips
waiting for a framing review or failed renders.

## Clips
`ClipOut` gains:
* `sequence: int|null` — 1-based position by **source start time** among the source's non-removed clips
  (always chronological; independent of processing/completion order; `null` for removed clips).
* `social_metadata: ClipMetadata` — also in list items (title/caption, description, hashtags per platform).
* `needs_framing_review: bool` — blocking framing warnings stopped the automatic render (status stays
  `processing` until the framing is fixed or acknowledged and rendered).
* `status`: `"pending"|"processing"|"generated"|"uploading"|"uploaded"|"upload_failed"|"removed"|"cleaned"`
  (`cleaned` = uploaded and its media deleted; `generated` = rendered MP4 ready and current;
  `processing` = tracking/framing/render running or waiting for a framing review; `pending` = nothing
  running and no current render, e.g. a failed render (`render_error`) or a clip edited since its render).
* `upload_status: "none"|"uploading"|"uploaded"|"failed"`, `youtube_video_id`, `youtube_url`, `uploaded_at`,
  `upload_error`, `cleanup_status: "none"|"cleaned"|"failed"`, `media_available: bool`, `media_bytes`,
  `freed_bytes`, `removed_at`.
* For cleaned clips `thumbnail_url` falls back to the YouTube thumbnail.
* `review_status` is removed.

| Method | Path | Body / query | Response |
|---|---|---|---|
| GET | /clips | `?video_source_id&status&q&sort=sequence\|score\|created&include_removed=false&limit&offset` (default sort `sequence` when `video_source_id` is given) | `Page<ClipOut>` |
| DELETE | /clips/{id} | – | `{id, status: "removed", freed_bytes}` — **Remove Clip**: deletes only this clip's MP4, proxy, thumbnail and temp files; keeps a small record (title, timestamps, upload history). Refused (409 `clip_uploading`) while it is uploading. Idempotent. Pending publications/jobs of the clip are cancelled; re-analysis never re-creates it. |
| POST | /clips/bulk | `{ids, action: "render"\|"remove"}` | `{results}` |

## Sequential YouTube uploads
Only one upload runs at a time per connected account. A run uploads clips **in sequence order**; it
**stops at the first failure** (never skips it), and Retry resumes from the failed clip.

`UploadRunOut = {id, video_source_id, connected_account_id, mode: "single"|"next"|"remaining", privacy,
status: "running"|"completed"|"failed"|"cancelled", cancel_requested: bool, total, uploaded, remaining, current_clip_id,
failed_clip_id, last_error, created_at, updated_at,
items: [{clip_id, sequence, title, status: "pending"|"uploading"|"uploaded"|"failed"|"cancelled",
         progress: number|null, youtube_url, error}]}`

| Method | Path | Body | Response |
|---|---|---|---|
| POST | /upload-runs | `{video_source_id, connected_account_id, mode: "single"\|"next"\|"remaining", clip_id? (single), privacy?: "public"\|"unlisted"\|"private" (default: user setting), rights_confirmed: true}` | 201 `UploadRunOut` (409 `upload_in_progress` when the account already has a running run; 409 `nothing_to_upload`, `clip_not_ready`, `already_uploaded`, `clip_removed`; 409 `youtube_auth_expired` / `permission_missing` when the account must be reconnected) |
| GET | /upload-runs | `?video_source_id&active=true\|false&limit=50` | `UploadRunOut[]`, newest first (without `active`: all runs incl. failed ones) |
| GET | /upload-runs/{id} | – | `UploadRunOut` |
| POST | /upload-runs/{id}/retry | – | `UploadRunOut` (re-uploads the failed clip, then continues) |
| POST | /upload-runs/{id}/cancel | – | `UploadRunOut` (stops after the current upload; never interrupts a transfer midway) |

"next" = the first clip in sequence that is generated and not yet uploaded; "remaining" = all such clips
(clips whose previous upload failed count as "not yet uploaded"). A run item is `uploaded` as soon as
YouTube returned the video id; `progress` is the measured byte progress of the transfer. The YouTube
description gets the source's `attribution` credit line when it is not already in it. Every failure stops
the run (including quota: `last_error` says when to retry; and a video YouTube rejects right after the
upload); the clip is never skipped. Retry re-uploads that clip first (resuming the interrupted resumable
session when there is one). Restarts are safe: an interrupted transfer is resumed with its stored session
URI, so a clip is never uploaded twice.

**Cleanup after upload:** a clip's media is deleted only after YouTube returned a video id **and** a
follow-up `videos.list` confirms the video exists and is not failed/rejected. Uncertain or failed uploads
never delete anything. Cleanup is idempotent and only touches `users/{uid}/clips/{clip_id}/`.
Implementation: deletion requires `uploadStatus == "processed"`; `uploaded` (YouTube still processing),
network errors, quota errors and "not listed yet" keep the files and are re-checked by the worker every
`UPLOAD_VERIFY_INTERVAL_SECONDS` (300). `failed`/`rejected`/`deleted` mark the clip `upload_failed`
(files kept, upload can be retried). Media is not deleted while another publication of the clip is pending.

## Storage
`GET /storage` →
```
{managed_bytes, originals_bytes, renders_bytes, proxies_bytes, thumbnails_bytes, temp_bytes,
 freed_bytes_total, disk_free_bytes,
 clips: {total, generated, uploaded, removed, cleaned},
 sources: [{id, title, original_bytes, clip_bytes, clips_total, clips_done, all_done, original_available}]}
```
`POST /sources/{id}/delete-original` → `{id, status: "deleted", freed_bytes, source: SourceOut}`: deletes only
the original long video (and its thumbnail/proxies under the source folder) once you no longer need it;
clips' records/media/links stay. Idempotent; 409 `source_busy` while processing; 409 `no_file` when there was
no original. Afterwards analysis/render return 409 `original_deleted` until a file is attached again
(upload with `video_source_id` or attach-link). User setting `auto_delete_original` (default **false**) does
this automatically when every clip of the source is removed, or uploaded **and verified/cleaned**.

Sizes come only from ClipFusion's managed storage (`users/{uid}/...`) and its temp directory. `clips.total`
counts removed clips too; `clips_done` = uploaded or removed. Disk-space checks (upload init/complete,
imports, analysis, render) return 507 `insufficient_disk_space` with a readable message
(`MIN_FREE_DISK_MB`, default 2048, must stay free).

## User settings (PATCH /users/me `settings`)
Adds `default_processing_mode` ("balanced"), `default_min_duration` (20), `default_max_duration` (60),
`default_max_clips` (10), `default_privacy` ("private"), `auto_delete_original` (false).
`default_subtitle_preset_id` was removed (it is dropped from stored settings on the next PATCH).

## Other changes
* `GET /dashboard/summary`: `awaiting_approval` is replaced by `ready_to_upload` (generated, current render,
  not uploaded) and `uploaded_clips`; `total_clips` excludes removed clips.
* `GET /health` → `{status: "ok", app: "clipfusion"}`; `GET /system/capabilities` adds `app: "clipfusion"`,
  `api_version: 2`, `youtube_api_key_configured`, `subtitles: false`, `disk_free_bytes` (start.ps1 uses this to
  verify that port 8010 is really ClipFusion).
* Notification types added: `clips_ready` (all clips of a video generated), `framing_review`, `upload_run`,
  `storage`. `clips_awaiting_approval` is no longer sent.
* Error codes: `rights_not_confirmed` (422), `invalid_youtube_url` (422), `youtube_video_unavailable` (404),
  `youtube_auth_expired`, `youtube_permission_missing` (403), `quota_exceeded` (429), `platform_url_refused`
  (422 — YouTube/streaming link given to attach-link/cloud), `file_already_attached`, `importing`,
  `original_deleted`, `clip_removed`, `clip_uploading`, `clip_uploaded` (trim after upload), `upload_in_progress`,
  `nothing_to_upload`, `clip_not_ready`, `already_uploaded`, `media_deleted` (download of a cleaned clip;
  details.youtube_url), `insufficient_disk_space` (507).
* A transcription that fell back from GPU to CPU adds a warning to `SourceOut.warnings`; `PipelineOut.device`
  reports the device and model actually used.
