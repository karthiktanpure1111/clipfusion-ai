"""What each platform integration needs (credentials, scopes, review, eligibility).

Served by ``GET /accounts/requirements`` so the UI can explain -- accurately -- why a
platform cannot be connected yet and what the operator/user must do.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.integrations import google_oauth, meta_oauth

_DOC = "docs/INTEGRATIONS.md"


def _youtube() -> dict[str, Any]:
    s = get_settings()
    scopes = [
        {"scope": google_oauth.SCOPE_YOUTUBE_UPLOAD, "purpose": "Upload Shorts to your channel",
         "classification": "sensitive", "required": True},
        {"scope": google_oauth.SCOPE_YOUTUBE_READONLY, "purpose": "Read channel identity, video metadata "
         "and publishing status", "classification": "sensitive", "required": True},
        {"scope": google_oauth.SCOPE_YT_ANALYTICS, "purpose": "Real audience retention and statistics for "
         "your own videos", "classification": "sensitive", "required": True},
        {"scope": "openid email profile", "purpose": "Identify the Google account", "classification":
         "non-sensitive", "required": True},
        {"scope": google_oauth.SCOPE_DRIVE_READONLY, "purpose": "Optional: import original videos from Google "
         "Drive", "classification": "restricted", "required": False},
    ]
    return {
        "platform": "youtube",
        "provider": "google",
        "configured": s.google_configured,
        "missing_env": google_oauth.missing_env(),
        "api_key_configured": bool(s.youtube_api_key),
        "redirect_uris": [s.oauth_redirect_uri("google")],
        "scopes": scopes,
        "eligibility": [
            "A Google account with a YouTube channel (create one at youtube.com before connecting).",
            "Shorts classification is decided by YouTube: vertical or square video up to 3 minutes.",
            "Channels may need phone verification for some features (e.g. custom thumbnails, long uploads).",
        ],
        "app_review": [
            "Google Cloud project with YouTube Data API v3, YouTube Analytics API (and Google Drive API for "
            "imports) enabled.",
            "OAuth consent screen: while the app is unverified only listed test users (max 100) can connect, "
            "and their refresh tokens expire after 7 days in 'Testing' publishing status.",
            "Sensitive scopes require Google OAuth app verification; drive.readonly is restricted and also "
            "requires a security assessment.",
            "Videos uploaded through an unverified API project created after 28 July 2020 are locked to "
            "private until the project passes the YouTube API Services audit.",
            f"Default quota per Google Cloud project: {s.youtube_daily_upload_limit} video uploads/day "
            f"(videos.insert, its own bucket, 1 unit each), {s.youtube_daily_search_limit} searches/day "
            f"(search.list, its own bucket) and {s.youtube_daily_quota:,} units/day for all other calls. "
            "Quotas reset at midnight Pacific Time. Request more through the YouTube API Services audit and "
            "quota extension form.",
        ],
        "notes": [
            "ClipFusion never asks for your Google password; you sign in on Google's own page.",
            "You can revoke access at any time at https://myaccount.google.com/permissions.",
        ],
        "quota": {"daily_units": s.youtube_daily_quota, "uploads_per_day": s.youtube_daily_upload_limit,
                  "searches_per_day": s.youtube_daily_search_limit, "upload_cost": 1},
        "docs_links": [
            {"title": "Setup guide", "url": _DOC},
            {"title": "YouTube Data API: videos.insert", "url": "https://developers.google.com/youtube/v3/docs/videos/insert"},
            {"title": "YouTube Data API quota costs", "url": "https://developers.google.com/youtube/data/determine_quota_cost"},
            {"title": "Resumable uploads", "url": "https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol"},
            {"title": "Quota and compliance audits", "url": "https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits"},
            {"title": "OAuth app verification", "url": "https://support.google.com/cloud/answer/13463073"},
        ],
    }


def _instagram() -> dict[str, Any]:
    s = get_settings()
    flow = s.instagram_auth_flow
    provider_path = "facebook" if flow == meta_oauth.FLOW_FACEBOOK else "instagram"
    if flow == meta_oauth.FLOW_UNIFIED:
        perms = [{"scope": "instagram_basic", "purpose": "Read the professional account profile"},
                 {"scope": "instagram_content_publish", "purpose": "Publish Reels"}]
        eligibility = ["An Instagram professional account (Business or Creator).",
                       "A Facebook Page is NOT required with Unified Login: sign in with Instagram or Facebook, "
                       "pick your business portfolio and tick the Instagram account to share."]
        notes = ["The permissions come from the login configuration (META app > Use cases > Instagram > API "
                 "setup with Unified login); keep instagram_content_publish in it."]
    elif flow == meta_oauth.FLOW_INSTAGRAM:
        perms = [{"scope": "instagram_business_basic", "purpose": "Read the professional account profile"},
                 {"scope": "instagram_business_content_publish", "purpose": "Publish Reels"}]
        eligibility = ["An Instagram professional account (Business or Creator). Personal accounts cannot "
                       "publish through the API; switch in Instagram > Settings > Account type and tools.",
                       "A Facebook Page is NOT required with Instagram Login."]
        notes = ["Long-lived tokens last 60 days and are refreshed automatically once they are 24 h old.",
                 "Reach/views insights need the extra instagram_business_manage_insights permission, which is "
                 "not requested by default; without it analytics show like and comment counts only."]
    else:
        perms = [{"scope": p, "purpose": purpose} for p, purpose in (
            ("instagram_basic", "Read the linked Instagram account"),
            ("instagram_content_publish", "Publish Reels"),
            ("pages_show_list", "Find the Facebook Page linked to Instagram"),
            ("pages_read_engagement", "Read Page/Instagram linkage"),
            ("business_management", "Access Pages owned through Business Manager"))]
        eligibility = ["An Instagram professional account (Business or Creator) linked to a Facebook Page.",
                       "During Facebook login select the Page that is linked to the Instagram account."]
        notes = ["Facebook long-lived user tokens last about 60 days and cannot be refreshed without you; "
                 "ClipFusion will ask you to reconnect before they expire."]
    return {
        "platform": "instagram",
        "provider": flow,
        "configured": s.instagram_configured,
        "missing_env": meta_oauth.missing_env(flow),
        "redirect_uris": [s.oauth_redirect_uri(provider_path)],
        "scopes": perms,
        "eligibility": eligibility,
        "app_review": [
            "A Meta app (developers.facebook.com) with the Instagram product added.",
            "Until App Review grants Advanced Access, only users with a role on the app (admins, developers, "
            "testers) can connect.",
            "Business Verification of the app owner may be required for Advanced Access.",
            f"Instagram allows a limited number of API-published posts per 24 h per account (checked live via "
            f"content_publishing_limit; ClipFusion also caps at {s.instagram_daily_publish_limit}/day).",
        ],
        "notes": notes + [
            "Reels must be MP4/MOV, H.264 or HEVC video with AAC audio, ideally 9:16 1080x1920; duration and "
            "size limits are enforced by Instagram.",
            "ClipFusion never asks for your Instagram or Facebook password.",
        ],
        "publish_limit": s.instagram_daily_publish_limit,
        "docs_links": [
            {"title": "Setup guide", "url": _DOC},
            {"title": "Instagram API with Instagram Login",
             "url": "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login"},
            {"title": "Content publishing",
             "url": "https://developers.facebook.com/docs/instagram-platform/content-publishing"},
            {"title": "App Review", "url": "https://developers.facebook.com/docs/app-review"},
        ],
    }


def platform_requirements() -> dict[str, Any]:
    """Requirements for every supported platform keyed by platform name."""
    return {"youtube": _youtube(), "instagram": _instagram()}
