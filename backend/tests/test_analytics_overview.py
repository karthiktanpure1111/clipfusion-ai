"""Analytics: real internal counts, latest platform snapshots, labelled AI estimates, collector."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import respx
from sqlalchemy import select

from app.core.database import utcnow
from app.core.security import create_access_token
from app.integrations.youtube import API_BASE
from app.models import AnalyticsSnapshot, ProcessingJob
from app.models.enums import JobType, Platform, QueueStatus, ReviewStatus
from app.modules.analytics.collector import collect_platform_stats
from app.modules.publishing import history
from app.modules.scheduling import service
from tests.test_publishing_factories import api_client, make_account, make_clip, make_source


def _published(db, clip, acc, external_id: str):
    item = service.enqueue_clip(db, clip, acc, rights_confirmed=True)
    item.status = QueueStatus.PUBLISHED
    item.external_id = external_id
    item.published_at = utcnow()
    history.record(db, item, "publish", QueueStatus.PUBLISHED, "ok")
    db.commit()
    return item


def _snap(db, item, views: int, likes: int, *, age_minutes: int = 0) -> None:
    db.add(AnalyticsSnapshot(user_id=item.user_id, clip_id=item.clip_id, queue_item_id=item.id,
                             connected_account_id=item.connected_account_id, platform=item.platform,
                             external_id=item.external_id, views=views, likes=likes,
                             fetched_at=utcnow() - timedelta(minutes=age_minutes)))
    db.commit()


def test_overview_counts_and_sources(db, user, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr("app.workers.jobs.enqueue", lambda name, countdown=None, **kw: "t")
    yt, ig = make_account(db, user), make_account(db, user, Platform.INSTAGRAM)
    source = make_source(db, user)
    c1 = make_clip(db, user, source=source, score=95, review=ReviewStatus.APPROVED)
    c2 = make_clip(db, user, source=source, score=55, review=ReviewStatus.APPROVED)
    c3 = make_clip(db, user, source=source, score=15, review=ReviewStatus.REJECTED)
    make_clip(db, user, source=source, score=42)
    for c in (c1, c2, c3):
        c.reviewed_at = utcnow()
    db.commit()
    p1 = _published(db, c1, yt, "vid00000001")
    p2 = _published(db, c2, ig, "1790000000002")
    _snap(db, p1, views=100, likes=5, age_minutes=60)
    _snap(db, p1, views=250, likes=9)  # latest wins
    _snap(db, p2, views=80, likes=12)
    failed = service.enqueue_clip(db, c2, yt, rights_confirmed=True)
    failed.status = QueueStatus.FAILED
    history.record(db, failed, "failed", QueueStatus.FAILED, "boom")
    db.commit()

    client = api_client()
    data = client.get("/api/v1/analytics/overview", params={"days": 7}, headers=auth_headers).json()
    totals = data["internal"]["totals"]
    assert totals["videos"] == 1 and totals["clips_generated"] == 4 and totals["clips_awaiting_approval"] == 1
    assert totals["published"] == 2 and totals["failed"] == 1 and totals["scheduled"] == 0
    assert totals["approval_rate"] == round(2 / 3, 4)
    today = data["internal"]["series"][-1]
    assert len(data["internal"]["series"]) == 7
    assert today == {"date": today["date"], "clips_generated": 4, "published_youtube": 1, "published_instagram": 1,
                     "publish_failures": 1}
    assert data["platform"]["youtube"]["totals"]["views"] == 250 and data["platform"]["youtube"]["totals"]["likes"] == 9
    assert data["platform"]["instagram"]["totals"]["views"] == 80
    assert data["platform"]["youtube"]["totals"]["shares"] is None
    assert "YouTube Data API" in data["platform"]["youtube"]["source"]
    assert [t["external_id"] for t in data["top_published_clips"]] == ["vid00000001", "1790000000002"]
    ai = data["ai_highlight_scores"]
    assert ai["label"] == "AI highlight score (estimate)" and ai["is_estimate"] is True
    counts = {b["range"]: b["count"] for b in ai["buckets"]}
    assert counts["90-100"] == 1 and counts["50-60"] == 1 and counts["10-20"] == 1 and counts["40-50"] == 1

    clip_data = client.get(f"/api/v1/analytics/clips/{c1.id}", headers=auth_headers).json()
    pub = clip_data["publications"][0]
    assert pub["latest"]["views"] == 250 and len(pub["history"]) == 2
    assert clip_data["ai_highlight_score"]["label"] == "AI highlight score (estimate)"
    other = {"Authorization": f"Bearer {create_access_token(make_other(db).id)}"}
    assert client.get(f"/api/v1/analytics/clips/{c1.id}", headers=other).status_code == 404
    other_overview = client.get("/api/v1/analytics/overview", headers=other).json()
    assert other_overview["internal"]["totals"]["clips_generated"] == 0

    r = client.post("/api/v1/analytics/refresh", headers=auth_headers)
    assert r.status_code == 202
    job = db.get(ProcessingJob, uuid.UUID(r.json()["job_id"]))
    assert job.job_type == JobType.ANALYTICS and job.user_id == user.id


def make_other(db):
    from app.models import User

    u = User(email=f"o-{utcnow().timestamp()}@example.com", full_name="O", timezone="UTC", settings={})
    db.add(u)
    db.commit()
    return u


@respx.mock
def test_collector_snapshots_real_youtube_statistics(db, user) -> None:
    acc = make_account(db, user, token="stats-token")
    item = _published(db, make_clip(db, user), acc, "vidSTATS001")
    route = respx.get(f"{API_BASE}/videos").mock(return_value=httpx.Response(200, json={"items": [
        {"id": "vidSTATS001", "statistics": {"viewCount": "321", "likeCount": "12", "commentCount": "3"}}]}))
    result = collect_platform_stats(db, user_id=user.id)
    assert result["snapshots"] == 1 and result["errors"] == []
    assert route.calls[0].request.url.params["part"] == "statistics"
    snap = db.scalar(select(AnalyticsSnapshot).where(AnalyticsSnapshot.queue_item_id == item.id))
    assert (snap.views, snap.likes, snap.comments) == (321, 12, 3) and snap.platform == Platform.YOUTUBE
