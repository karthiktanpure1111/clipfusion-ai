"""Factories shared by the A2 test modules (accounts / publishing / scheduling / analytics).

Imported as ``from tests.test_publishing_factories import ...``. Contains one sanity test.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.crypto import decrypt, encrypt
from app.core.database import utcnow
from app.core.errors import register_exception_handlers
from app.core.storage import clip_key, get_storage
from app.integrations import google_oauth
from app.models import ConnectedAccount, GeneratedClip, PublishingSchedule, User, VideoSource
from app.models.enums import AccountStatus, Platform, RenderStatus, ReviewStatus

YT_SCOPES = sorted([google_oauth.SCOPE_YOUTUBE_UPLOAD, google_oauth.SCOPE_YOUTUBE_READONLY,
                    google_oauth.SCOPE_YT_ANALYTICS, "openid", "email", "profile"])


def make_account(db, user: User, platform: str = Platform.YOUTUBE, *, provider: str | None = None,
                 token: str = "access-token-1", refresh: str | None = "refresh-token-1",
                 expires_in: int | None = 3600, external_id: str | None = None,
                 scopes: list[str] | None = None, issued_hours_ago: float = 1.0) -> ConnectedAccount:
    provider = provider or ("google" if platform == Platform.YOUTUBE else "instagram_login")
    ext = external_id or (f"UC{uuid.uuid4().hex[:22]}" if platform == Platform.YOUTUBE else str(uuid.uuid4().int)[:17])
    meta = {"channel_id": ext} if platform == Platform.YOUTUBE else {"ig_user_id": ext, "username": "creator",
                                                                     "account_type": "BUSINESS"}
    meta["token_issued_at"] = (utcnow() - timedelta(hours=issued_hours_ago)).isoformat()
    acc = ConnectedAccount(user_id=user.id, platform=platform, provider=provider, external_account_id=ext,
                           display_name=f"{platform} account", username="creator",
                           access_token_enc=encrypt(token), refresh_token_enc=encrypt(refresh) if refresh else None,
                           token_expires_at=utcnow() + timedelta(seconds=expires_in) if expires_in else None,
                           scopes=scopes if scopes is not None else (
                               YT_SCOPES if platform == Platform.YOUTUBE else
                               ["instagram_business_basic", "instagram_business_content_publish"]),
                           status=AccountStatus.ACTIVE, account_metadata=meta)
    db.add(acc)
    db.commit()
    return acc


def make_source(db, user: User) -> VideoSource:
    src = VideoSource(user_id=user.id, source_type="upload", status="ready", title="Long stream")
    db.add(src)
    db.commit()
    return src


def make_clip(db, user: User, *, source: VideoSource | None = None, score: float = 50.0,
              output_file: Path | None = None, review: str = ReviewStatus.PENDING,
              start: float | None = None) -> GeneratedClip:
    source = source or make_source(db, user)
    start = start if start is not None else float(uuid.uuid4().int % 10_000)
    clip = GeneratedClip(id=uuid.uuid4(), user_id=user.id, video_source_id=source.id, start_time=start,
                         end_time=start + 30, duration=30.0, highlight_score=score, title="Epic moment",
                         review_status=review,
                         social_metadata={"youtube": {"title": "Epic moment #shorts", "description": "Watch this",
                                                      "tags": ["gaming", "clip"], "hashtags": ["shorts"],
                                                      "privacy": "public", "made_for_kids": False},
                                          "instagram": {"caption": "Epic moment", "hashtags": ["gaming", "reels"],
                                                        "share_to_feed": True}})
    if output_file is not None:
        key = clip_key(user.id, clip.id, "render-test.mp4")
        get_storage().put_file(output_file, key, "video/mp4")
        clip.output_key = key
        clip.render_status = RenderStatus.RENDERED
    db.add(clip)
    db.commit()
    return clip


def make_schedule(db, user: User, account: ConnectedAccount, **kw) -> PublishingSchedule:
    values = {"name": "Test", "mode": "review", "frequency_type": "interval", "interval_minutes": 60,
              "times_of_day": [], "days_of_week": [0, 1, 2, 3, 4, 5, 6], "timezone": "UTC", "daily_limit": 10,
              "auto_min_score": 0.0, "default_privacy": "public", "rights_confirmed_at": utcnow(), "is_active": True}
    values.update(kw)
    s = PublishingSchedule(user_id=user.id, connected_account_id=account.id, platform=account.platform, **values)
    db.add(s)
    db.commit()
    return s


def write_bytes_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    return path


def api_client() -> TestClient:
    """Minimal app with the A2 routers under /api/v1 (app.main is written by another engineer)."""
    from app.modules.accounts.router import router as accounts_router
    from app.modules.analytics.router import router as analytics_router
    from app.modules.publishing.router import router as publishing_router
    from app.modules.scheduling.router import router as schedules_router

    app = FastAPI()
    register_exception_handlers(app)
    for r in (accounts_router, publishing_router, schedules_router, analytics_router):
        app.include_router(r, prefix="/api/v1")
    return TestClient(app, follow_redirects=False)


def test_factories_roundtrip(db, user) -> None:
    acc = make_account(db, user)
    assert decrypt(acc.access_token_enc) == "access-token-1"
    assert acc.access_token_enc != "access-token-1"
    clip = make_clip(db, user)
    assert clip.user_id == user.id
