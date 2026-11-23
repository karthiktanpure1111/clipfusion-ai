/**
 * TypeScript types for the ClipFusion REST API (docs/API.md) and the integration
 * Pydantic models (backend/app/modules/{accounts,publishing,scheduling}/schemas.py,
 * backend/app/modules/analytics/service.py, backend/app/schemas/*.py).
 */

export type UUID = string;
export type ISODate = string;

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ------------------------------------------------------------------ user (single local user, no login)
export type ProcessingMode = "fast" | "balanced" | "best";
export type Privacy = "public" | "unlisted" | "private";

export interface UserSettings {
  default_clip_durations?: number[];
  default_max_clips: number;
  default_use_llm: boolean;
  default_framing_preset_id: UUID | null;
  publishing_paused: boolean;
  notify_email: boolean;
  // v2 (docs/API_V2.md) — optional so an older backend still type-checks at runtime
  default_processing_mode?: ProcessingMode;
  default_min_duration?: number;
  default_max_duration?: number;
  default_privacy?: Privacy;
  auto_delete_original?: boolean;
  youtube_download_enabled?: boolean; // download public YouTube videos from their link (opt-in)
  youtube_download_quality?: "best" | "2160" | "1440" | "1080" | "720";
}

/** Processing options shared by every "start processing" request (API_V2 AnalysisRequest). */
export interface AnalysisRequest {
  mode: ProcessingMode;
  min_duration: number;
  max_duration: number;
  max_clips: number;
  clip_durations?: number[];
  language?: string | null;
  use_llm?: boolean;
}

export interface UserOut {
  id: UUID;
  email: string;
  full_name: string;
  avatar_url: string | null;
  timezone: string;
  role: string;
  settings: UserSettings;
  has_password: boolean;
  google_linked: boolean;
  created_at: ISODate;
}

// ------------------------------------------------------------------ uploads
export interface UploadOut {
  upload_id: UUID;
  filename: string;
  size: number;
  chunk_size: number;
  total_chunks: number;
  received_chunks: number[];
  status: string;
  expires_at: ISODate;
}

export interface ChunkOut {
  received_chunks: number[];
  complete: boolean;
}

// ------------------------------------------------------------------ jobs
export type JobStatus = "queued" | "running" | "retrying" | "succeeded" | "failed" | "cancelled";
export type JobType =
  | "import"
  | "analysis"
  | "clip_vision"
  | "transcribe_clip"
  | "metadata"
  | "render"
  | "publish"
  | "analytics";

export interface JobOut {
  id: UUID;
  job_type: JobType | string;
  status: JobStatus;
  stage: string | null;
  progress: number;
  message: string | null;
  error: string | null;
  attempts: number;
  max_attempts: number;
  video_source_id: UUID | null;
  clip_id: UUID | null;
  queue_item_id: UUID | null;
  source_title: string | null;
  clip_title: string | null;
  params: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  created_at: ISODate;
  started_at: ISODate | null;
  finished_at: ISODate | null;
  next_attempt_at: ISODate | null;
}

// ------------------------------------------------------------------ sources
export type SourceType = "upload" | "youtube" | "cloud";
export type SourceStatus =
  | "pending_upload"
  | "metadata_only"
  | "importing"
  | "uploaded"
  | "queued"
  | "processing"
  | "ready"
  | "failed";

export interface RetentionPoint {
  ratio: number;
  audience_watch_ratio: number;
  relative_performance: number | null;
}

export interface YouTubeVideoMetadata {
  video_id?: string;
  title?: string;
  description?: string;
  channel_id?: string | null;
  channel_title?: string;
  published_at?: string | null;
  duration_seconds?: number | null;
  thumbnail_url?: string | null;
  view_count?: number | null;
  like_count?: number | null;
  comment_count?: number | null;
  tags?: string[];
  is_owner?: boolean;
  owner_account_id?: string | null;
}

export interface SourceOut {
  id: UUID;
  source_type: SourceType;
  status: SourceStatus;
  title: string;
  description: string | null;
  original_filename: string | null;
  file_size: number | null;
  mime_type: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_audio: boolean | null;
  has_file: boolean;
  thumbnail_url: string | null;
  youtube_video_id: string | null;
  youtube_url: string | null;
  youtube_metadata: YouTubeVideoMetadata | null;
  has_retention: boolean;
  retention_points?: RetentionPoint[] | null;
  cloud_provider: string | null;
  rights_confirmed: boolean;
  rights_confirmed_at: ISODate | null;
  analysis_options: Record<string, unknown> | null;
  warnings: string[];
  error: string | null;
  processed_at: ISODate | null;
  clip_count: number;
  active_job: JobOut | null;
  duplicate?: boolean;
  created_at: ISODate;
  updated_at: ISODate;
  // v2
  processing_mode?: ProcessingMode | null;
  attribution?: string | null;
  transcript_language?: string | null;
  original_available?: boolean;
  original_bytes?: number | null;
}

// ------------------------------------------------------------------ pipeline (v2)
export type PipelineStageKey =
  | "ingestion"
  | "audio"
  | "transcription"
  | "video_analysis"
  | "highlights"
  | "clip_generation"
  | "metadata"
  | "completed";

export interface PipelineStage {
  key: PipelineStageKey | string;
  label: string;
  status: "pending" | "running" | "done" | "failed" | "skipped";
  /** 0..1 measured progress, null when not measurable. */
  progress: number | null;
  started_at: ISODate | null;
  finished_at: ISODate | null;
  seconds: number | null;
  message: string | null;
  error: string | null;
}

export interface ClipCounts {
  total: number;
  pending?: number; // clips not rendered yet (e.g. created before automatic rendering)
  processing: number;
  generated: number;
  upload_failed: number;
  uploaded: number;
  removed: number;
  cleaned: number;
}

export interface PipelineOut {
  source_id: UUID;
  status: "idle" | "running" | "completed" | "failed";
  mode: ProcessingMode | null;
  started_at: ISODate | null;
  finished_at: ISODate | null;
  elapsed_seconds: number | null;
  eta_seconds: number | null;
  stages: PipelineStage[];
  clips: ClipCounts;
  device: { transcription: "cuda" | "cpu" | null; model: string | null };
}

// ------------------------------------------------------------------ storage (v2)
export interface StorageSource {
  id: UUID;
  title: string;
  original_bytes: number | null;
  clip_bytes: number | null;
  clips_total: number;
  clips_done: number;
  all_done: boolean;
  original_available: boolean;
}

export interface StorageOut {
  managed_bytes: number;
  originals_bytes: number;
  renders_bytes: number;
  proxies_bytes: number;
  thumbnails_bytes: number;
  temp_bytes: number;
  freed_bytes_total: number;
  disk_free_bytes: number | null;
  clips: { total: number; generated: number; uploaded: number; removed: number; cleaned: number };
  sources: StorageSource[];
}

// ------------------------------------------------------------------ sequential YouTube uploads (v2)
export type UploadRunMode = "single" | "next" | "remaining";
export type UploadRunStatus = "running" | "completed" | "failed" | "cancelled";

export interface UploadRunItem {
  clip_id: UUID;
  sequence: number | null;
  title: string;
  status: "pending" | "uploading" | "uploaded" | "failed" | "cancelled";
  progress: number | null;
  youtube_url: string | null;
  error: string | null;
}

export interface UploadRunOut {
  id: UUID;
  video_source_id: UUID;
  connected_account_id: UUID;
  mode: UploadRunMode;
  privacy: Privacy;
  status: UploadRunStatus;
  /** Not in API_V2.md; sent by the backend after Cancel until the current transfer ends. */
  cancel_requested?: boolean;
  total: number;
  uploaded: number;
  remaining: number;
  current_clip_id: UUID | null;
  failed_clip_id: UUID | null;
  last_error: string | null;
  created_at: ISODate;
  updated_at: ISODate;
  items: UploadRunItem[];
}

export interface UploadRunCreate {
  video_source_id: UUID;
  connected_account_id: UUID;
  mode: UploadRunMode;
  clip_id?: UUID;
  privacy: Privacy;
  rights_confirmed: true;
}

export interface TranscriptWord {
  start: number;
  end: number;
  word: string;
  probability: number;
}

export interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  words: TranscriptWord[];
  avg_logprob?: number | null;
  no_speech_prob?: number | null;
}

export interface TranscriptOut {
  language: string | null;
  model: string;
  text: string;
  word_count: number;
  duration: number | null;
  segments: TranscriptSegment[];
}

export interface MediaUrlOut {
  url: string;
  browser_playable: boolean;
  expires_in: number;
}

export type AnalyzeBody = AnalysisRequest & { rights_confirmed: true };

// ------------------------------------------------------------------ clips
export type ClipStatus = "pending" | "processing" | "generated" | "uploading" | "uploaded" | "upload_failed" | "removed" | "cleaned";
export type RenderStatus = "not_rendered" | "queued" | "rendering" | "rendered" | "failed" | "stale";

export interface ClipOut {
  id: UUID;
  video_source_id: UUID;
  source_title: string;
  start_time: number;
  end_time: number;
  duration: number;
  target_duration: number | null;
  highlight_score: number;
  score_breakdown: Record<string, number>;
  category: string;
  reasons: string[];
  transcript_text: string;
  hook_text: string | null;
  detection_method: "signals" | "signals+llm" | "manual";
  retention_score: number | null;
  title: string;
  render_status: RenderStatus;
  render_is_current: boolean;
  thumbnail_url: string | null;
  proxy_url: string | null;
  proxy_start: number | null;
  output_url: string | null;
  output_duration: number | null;
  rendered_at: ISODate | null;
  framing_mode: string | null;
  warning_count: number;
  blocking_warning_count: number;
  warnings_acknowledged: boolean;
  created_at: ISODate;
  updated_at: ISODate;
  // v2
  sequence?: number | null;
  status?: ClipStatus;
  upload_status?: "none" | "uploading" | "uploaded" | "failed";
  /** Next scheduled upload of the clip (timeline / calendar) and its queue item. */
  scheduled_at?: ISODate | null;
  scheduled_item_id?: string | null;
  youtube_video_id?: string | null;
  youtube_url?: string | null;
  uploaded_at?: ISODate | null;
  upload_error?: string | null;
  cleanup_status?: "none" | "cleaned" | "failed";
  media_available?: boolean;
  media_bytes?: number | null;
  freed_bytes?: number | null;
  removed_at?: ISODate | null;
  /** Present when the list endpoint embeds it; always present on ClipDetail. */
  social_metadata?: ClipMetadata;
}

export interface YouTubeMeta {
  title: string;
  description: string;
  tags: string[];
  hashtags: string[];
  hook: string;
  privacy: Privacy;
  made_for_kids: boolean;
}

export interface InstagramMeta {
  caption: string;
  hashtags: string[];
  hook: string;
  share_to_feed: boolean;
}

export interface ClipMetadata {
  youtube: YouTubeMeta;
  instagram: InstagramMeta;
  source: "llm" | "extractive" | "user";
  generated_at: string | null;
}

export interface ClipQueueRef {
  id: UUID;
  platform: Platform;
  status: QueueStatus;
  scheduled_at: ISODate | null;
  external_url: string | null;
}

export interface ClipDetail extends ClipOut {
  social_metadata: ClipMetadata;
  render_options: Record<string, unknown>;
  render_error: string | null;
  active_jobs: JobOut[];
  queue_items: ClipQueueRef[];
  source: { id: UUID; title: string; duration: number | null; width: number | null; height: number | null; fps: number | null };
}

export interface FramingWarning {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  track_id?: number | null;
  visible_fraction?: number | null;
  affected_fraction?: number | null;
  time_ranges?: [number, number][];
  suggestions?: unknown[];
}

export type BulkAction = "render" | "remove";
export interface RemoveClipOut {
  id: UUID;
  status: "removed";
  freed_bytes: number | null;
}
export interface RemoveClipOut {
  id: UUID;
  status: "removed";
  freed_bytes: number | null;
}
export interface BulkResult {
  results: { id: UUID; ok: boolean; error: { code: string; message: string } | null }[];
}

export interface RenderStatusOut {
  render_status: RenderStatus;
  render_is_current: boolean;
  job: JobOut | null;
  output_url: string | null;
  output_size: number | null;
  output_duration: number | null;
  rendered_at: ISODate | null;
  render_error: string | null;
}

// ------------------------------------------------------------------ accounts
export type Platform = "youtube" | "instagram";

export interface AccountOut {
  id: UUID;
  platform: Platform;
  provider: string;
  external_account_id: string;
  display_name: string;
  username: string | null;
  avatar_url: string | null;
  status: "active" | "expired" | "revoked" | "error" | string;
  scopes: string[];
  has_drive_scope: boolean;
  has_analytics_scope: boolean | null;
  can_publish: boolean;
  metadata: Record<string, unknown>;
  token_expires_at: ISODate | null;
  last_error: string | null;
  last_checked_at: ISODate | null;
  created_at: ISODate;
  updated_at: ISODate;
}

export interface RequirementScope {
  scope: string;
  purpose: string;
  classification?: string;
  required?: boolean;
}

export interface PlatformRequirements {
  platform: Platform;
  provider: string;
  configured: boolean;
  missing_env: string[];
  api_key_configured?: boolean;
  redirect_uris: string[];
  scopes: RequirementScope[];
  eligibility: string[];
  app_review: string[];
  notes: string[];
  quota?: { daily_units: number; upload_cost: number };
  publish_limit?: number;
  docs_links: { title: string; url: string }[];
}

export type RequirementsOut = Record<Platform, PlatformRequirements>;

// ------------------------------------------------------------------ publishing
export type QueueStatus =
  | "pending_approval"
  | "scheduled"
  | "rendering"
  | "publishing"
  | "processing_remote"
  | "published"
  | "failed"
  | "cancelled";

export interface QueueItemOut {
  id: UUID;
  clip_id: UUID;
  clip_title: string;
  thumbnail_url: string | null;
  connected_account_id: UUID;
  account_name: string;
  schedule_id: UUID | null;
  platform: Platform;
  status: QueueStatus;
  scheduled_at: ISODate | null;
  payload: Record<string, unknown>;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  external_id: string | null;
  external_url: string | null;
  remote_status: string | null;
  published_at: ISODate | null;
  created_at: ISODate;
  updated_at: ISODate;
}

export interface QueueCreate {
  clip_id: UUID;
  account_ids: UUID[];
  scheduled_at?: ISODate | null;
  youtube?: Partial<Omit<YouTubeMeta, "hook">> | null;
  instagram?: Partial<Omit<InstagramMeta, "hook">> | null;
  rights_confirmed: boolean;
}

export interface CalendarEntry {
  kind: "queue" | "history";
  id: UUID;
  at: ISODate;
  platform: Platform;
  status: string;
  clip_id: UUID | null;
  clip_title: string;
  thumbnail_url: string | null;
  account_id: UUID | null;
  account_name: string;
  external_url: string | null;
}

export interface AccountNext {
  account_id: UUID;
  platform: Platform;
  display_name: string;
  next_publish_at: ISODate | null;
  next_free_slot: ISODate | null;
}

export interface NextOut {
  paused: boolean;
  next_publish_at: ISODate | null;
  accounts: AccountNext[];
}

// ------------------------------------------------------------------ schedules
export interface ScheduleOut {
  id: UUID;
  connected_account_id: UUID;
  platform: Platform;
  name: string;
  is_active: boolean;
  mode: "review" | "automatic";
  frequency_type: "interval" | "times";
  interval_minutes: number | null;
  times_of_day: string[];
  days_of_week: number[];
  timezone: string;
  daily_limit: number;
  window_start: string | null;
  window_end: string | null;
  start_at: ISODate | null;
  auto_min_score: number;
  default_privacy: "public" | "unlisted" | "private";
  rights_confirmed_at: ISODate | null;
  next_run_at: ISODate | null;
  created_at: ISODate;
  updated_at: ISODate;
}

export interface ScheduleInput {
  connected_account_id?: UUID;
  name?: string | null;
  mode?: "review" | "automatic";
  frequency_type?: "interval" | "times";
  interval_minutes?: number | null;
  times_of_day?: string[] | null;
  days_of_week?: number[] | null;
  timezone?: string | null;
  daily_limit?: number | null;
  window_start?: string | null;
  window_end?: string | null;
  auto_min_score?: number | null;
  default_privacy?: "public" | "unlisted" | "private";
  rights_confirmed?: boolean;
}

export interface PreviewOut {
  schedule_id: UUID;
  timezone: string;
  slots: { at: ISODate; local: string }[];
}

// ------------------------------------------------------------------ analytics
export type MetricTotals = Record<"views" | "likes" | "comments" | "shares" | "saves" | "reach", number | null>;

export interface AnalyticsOverview {
  period: { days: number; start: string; end: string; timezone: string };
  internal: {
    totals: {
      videos: number;
      clips_generated: number;
      clips_awaiting_approval?: number;
      scheduled: number;
      published: number;
      failed: number;
      approved_in_period?: number;
      rejected_in_period?: number;
      approval_rate?: number | null;
    };
    series: {
      date: string;
      clips_generated: number;
      published_youtube: number;
      published_instagram: number;
      publish_failures: number;
    }[];
    source: string;
  };
  platform: Record<Platform, { source: string; publications_tracked: number; last_fetched_at: ISODate | null; totals: MetricTotals }>;
  top_published_clips: {
    clip_id: UUID | null;
    title: string;
    platform: Platform;
    external_id: string;
    views: number | null;
    likes: number | null;
    comments: number | null;
    fetched_at: ISODate;
    source: string;
  }[];
  ai_highlight_scores: { label: string; is_estimate: boolean; note: string; buckets: { range: string; count: number }[] };
}

// ------------------------------------------------------------------ dashboard / notifications / system
export interface DashboardSummary {
  total_videos: number;
  total_clips: number;
  processing_videos: number;
  awaiting_approval?: number;
  /** Not in the v2 contract; used when a backend provides it. */
  ready_to_upload?: number;
  scheduled_posts: number;
  published_clips: number;
  failed_uploads: number;
  next_publish_at: ISODate | null;
  clips_per_day: { date: string; count: number }[];
  published_per_day: { date: string; youtube: number; instagram: number }[];
  recent_clips: ClipOut[];
  active_jobs: JobOut[];
  upcoming: QueueItemOut[];
}

export interface NotificationOut {
  id: UUID;
  type: string;
  title: string;
  body: string | null;
  link: string | null;
  data: Record<string, unknown> | null;
  read_at: ISODate | null;
  created_at: ISODate;
}

export interface NotificationsOut {
  items: NotificationOut[];
  total: number;
  unread_count: number;
}

export interface Capabilities {
  ffmpeg: { ffmpeg: boolean; ffprobe: boolean; libx264: boolean; libass?: boolean };
  whisper: { available: boolean; model: string | null; device: string | null };
  face_detectors: string[];
  llm: { configured: boolean; model: string | null };
  youtube_configured: boolean;
  instagram_configured: boolean;
  instagram_auth_flow: string | null;
  google_missing_env: string[];
  instagram_missing_env: string[];
  storage_backend: string;
  worker_mode: string;
  fonts_installed?: number;
}

// ------------------------------------------------------------------ presets, fonts, assets
export interface FramingPresetOut {
  id: UUID;
  name: string;
  mode: string;
  config: Record<string, unknown>;
  is_default: boolean;
  created_at: ISODate;
  updated_at: ISODate;
}

export type AssetKind = "intro" | "outro" | "watermark" | "background";
export interface AssetOut {
  id: UUID;
  kind: AssetKind;
  name: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  duration: number | null;
  width: number | null;
  height: number | null;
  url: string | null;
  created_at: ISODate;
}

/** A quality a YouTube video can be downloaded in (GET /sources/{id}/youtube-qualities). */
export interface YouTubeQuality {
  quality: string; // max height to request, e.g. "2160"
  height: number;
  fps: number;
  label: string; // e.g. "2160p60 (4K)"
  codec: string;
  size_bytes: number | null; // estimate (video + audio)
}

export type YouTubeDownloadQuality = "best" | "2160" | "1440" | "1080" | "720";

/** POST /publishing/timeline: a video's clips one by one, `gap_minutes` apart. */
export interface TimelineCreate {
  video_source_id: string;
  connected_account_id: string;
  gap_minutes: number;
  start_at?: string | null; // default: one gap after the account's last post, or right away
  privacy?: Privacy;
  clip_ids?: string[];
  rights_confirmed: true;
}

export interface TimelineOut {
  scheduled: number;
  first_at: ISODate | null;
  last_at: ISODate | null;
  items: QueueItemOut[];
}

export interface AutoScheduleOut {
  enabled: boolean;
  connected_account_id: string | null;
  account_name: string | null;
  gap_minutes: number;
  privacy: Privacy | null;
  since: ISODate | null;
  rights_confirmed_at: ISODate | null;
  next_slot_at: ISODate | null;
}

export interface AutoScheduleIn {
  enabled: boolean;
  connected_account_id?: string | null;
  gap_minutes: number;
  privacy?: Privacy | null;
  rights_confirmed?: boolean;
}
