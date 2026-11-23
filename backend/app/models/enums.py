"""String enums stored as VARCHAR columns (kept in Python for easy migrations)."""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    UPLOAD = "upload"
    YOUTUBE = "youtube"  # metadata/analytics via official API; processing needs an attached original file
    CLOUD = "cloud"


class SourceStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"   # created, original file not yet attached
    METADATA_ONLY = "metadata_only"     # YouTube link: metadata fetched, awaiting original file
    IMPORTING = "importing"             # cloud import running
    UPLOADED = "uploaded"               # original file stored + validated
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"                     # analysis finished, clips generated
    FAILED = "failed"


class JobType(StrEnum):
    IMPORT = "import"            # cloud import (download from authorised source)
    ANALYSIS = "analysis"        # full analysis pipeline -> clips
    CLIP_VISION = "clip_vision"  # face detection/tracking for one clip (+ proxy, default framing)
    TRANSCRIBE_CLIP = "transcribe_clip"  # re-transcribe a clip range (subtitle regeneration)
    METADATA = "metadata"        # (re)generate titles/captions/hashtags
    RENDER = "render"            # final 9:16 export
    PUBLISH = "publish"          # upload/publish one queue item
    ANALYTICS = "analytics"      # fetch platform statistics


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(StrEnum):
    """Legacy: approve/reject was removed in API v2 (column kept, no longer used)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RenderStatus(StrEnum):
    NOT_RENDERED = "not_rendered"
    QUEUED = "queued"
    RENDERING = "rendering"
    RENDERED = "rendered"
    FAILED = "failed"
    STALE = "stale"  # framing/subtitles/trim changed after the last render


class ClipStatus(StrEnum):
    """Derived clip lifecycle status exposed as ``ClipOut.status`` (API v2)."""

    PENDING = "pending"          # not generated and nothing running (render failed / edited since)
    PROCESSING = "processing"    # tracking / framing / render running, or waiting for a framing review
    GENERATED = "generated"      # rendered MP4 ready
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    UPLOAD_FAILED = "upload_failed"
    REMOVED = "removed"          # Remove Clip: media deleted, small record kept
    CLEANED = "cleaned"          # uploaded and its media deleted after verification


class UploadStatus(StrEnum):
    NONE = "none"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


class CleanupStatus(StrEnum):
    NONE = "none"
    CLEANED = "cleaned"
    FAILED = "failed"


class UploadRunMode(StrEnum):
    SINGLE = "single"
    NEXT = "next"
    REMAINING = "remaining"


class UploadRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UploadRunItemStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Platform(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class FramingMode(StrEnum):
    SMART = "smart"                        # Mode 1: AI smart crop (face tracking)
    MANUAL = "manual"                      # Mode 2: manual crop rectangle
    FACECAM_GAMEPLAY = "facecam_gameplay"  # Mode 3: stacked facecam + gameplay
    PIP = "pip"                            # Mode 4: picture-in-picture
    FULL_FRAME = "full_frame"              # Mode 5: full frame / no crop, blurred background


class PublishMode(StrEnum):
    REVIEW = "review"        # clips need approval before entering the queue
    AUTOMATIC = "automatic"  # clips that match the schedule rules are queued automatically


class FrequencyType(StrEnum):
    INTERVAL = "interval"  # every N hours (1, 2, 3, 6 or custom)
    TIMES = "times"        # specific times of day


class QueueStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    SCHEDULED = "scheduled"
    RENDERING = "rendering"           # waiting for the clip render to finish
    PUBLISHING = "publishing"         # upload in progress
    PROCESSING_REMOTE = "processing_remote"  # uploaded; platform still processing
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotificationType(StrEnum):
    PROCESSING_COMPLETE = "processing_complete"
    PROCESSING_FAILED = "processing_failed"
    RENDER_COMPLETE = "render_complete"
    RENDER_FAILED = "render_failed"
    PUBLISH_SUCCESS = "publish_success"
    PUBLISH_FAILED = "publish_failed"
    ACCOUNT_ISSUE = "account_issue"
    CLIPS_AWAITING_APPROVAL = "clips_awaiting_approval"  # legacy (approve/reject removed)
    CLIPS_READY = "clips_ready"                          # clips generated, ready to upload
    FRAMING_REVIEW = "framing_review"                    # a clip needs a framing review before rendering
    UPLOAD_RUN = "upload_run"                            # sequential upload finished / stopped
    STORAGE = "storage"                                  # media cleanup / original deleted
    SYSTEM = "system"


class AssetKind(StrEnum):
    INTRO = "intro"
    OUTRO = "outro"
    WATERMARK = "watermark"
    BACKGROUND = "background"
