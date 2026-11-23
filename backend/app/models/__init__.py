"""All ORM models. Importing this package registers every table on ``Base.metadata``.

Table map (requirement §14):
  users, refresh_tokens ................. Users
  connected_accounts, api_quota_usage ... Connected accounts (+ quota management)
  video_sources, upload_sessions ........ Video sources (+ resumable uploads)
  processing_jobs ....................... Processing jobs
  transcripts, video_analyses ........... Transcripts (+ audio/scene/visual analysis)
  generated_clips ....................... Generated clips
  clip_framing_settings ................. Clip framing settings
  subtitle_styles ....................... Subtitle styles (per clip, incl. editable captions)
  subtitle_presets ...................... Saved subtitle presets (+ built-in templates)
  framing_presets ....................... Saved framing presets
  custom_fonts, user_assets ............. Uploaded fonts, intro/outro/watermark assets
  publishing_schedules .................. Publishing schedules
  publishing_queue ...................... Publishing queue
  publishing_history .................... Publishing history
  analytics_snapshots ................... Analytics
  notifications ......................... Notifications
  upload_runs, upload_run_items ......... Sequential YouTube uploads (API v2)
"""

from app.models.account import ApiQuotaUsage, ConnectedAccount
from app.models.clip import (
                             ClipFramingSettings,
                             ClipSubtitleStyle,
                             CustomFont,
                             FramingPreset,
                             GeneratedClip,
                             SubtitlePreset,
                             UserAsset,
)
from app.models.publishing import (
                             AnalyticsSnapshot,
                             Notification,
                             PublishingHistory,
                             PublishingQueueItem,
                             PublishingSchedule,
)
from app.models.upload_run import UploadRun, UploadRunItem
from app.models.user import RefreshToken, User
from app.models.video import ProcessingJob, Transcript, UploadSession, VideoAnalysis, VideoSource

__all__ = [
    "AnalyticsSnapshot", "ApiQuotaUsage", "ClipFramingSettings", "ClipSubtitleStyle", "ConnectedAccount",
    "CustomFont", "FramingPreset", "GeneratedClip", "Notification", "ProcessingJob", "PublishingHistory",
    "PublishingQueueItem", "PublishingSchedule", "RefreshToken", "SubtitlePreset", "Transcript",
    "UploadRun", "UploadRunItem", "UploadSession", "User", "UserAsset", "VideoAnalysis", "VideoSource",
]
