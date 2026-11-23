"""/publishing and /schedules HTTP API: validation, lifecycle actions, calendar, access control."""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import utcnow
from app.core.security import create_access_token
from app.models.enums import Platform
from tests.test_publishing_factories import api_client, make_account, make_clip


@pytest.fixture()
def client():
    return api_client()


@pytest.fixture()
def other_headers(other_user) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(other_user.id)}"}


@pytest.fixture()
def no_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr("app.workers.jobs.enqueue", lambda name, countdown=None, **kw: calls.append(name) or "t")
    monkeypatch.setitem(sys.modules, "app.modules.rendering.service", types.SimpleNamespace(
        render_is_current=lambda clip: True, request_render=None))
    return calls


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def test_queue_create_validation_and_duplicates(db, user, auth_headers, client) -> None:
    yt, ig = make_account(db, user), make_account(db, user, Platform.INSTAGRAM)
    clip = make_clip(db, user)
    body = {"clip_id": str(clip.id), "account_ids": [str(yt.id), str(ig.id)]}
    r = client.post("/api/v1/publishing/queue", json=body, headers=auth_headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "rights_not_confirmed"
    body |= {"rights_confirmed": True, "youtube": {"title": "Custom title", "privacy": "unlisted"},
             "instagram": {"caption": "IG caption", "hashtags": ["a", "b"]}}
    r = client.post("/api/v1/publishing/queue", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    items = {i["platform"]: i for i in r.json()}
    assert items["youtube"]["payload"]["title"] == "Custom title" and items["youtube"]["payload"]["privacy"] == "unlisted"
    assert items["instagram"]["payload"]["caption"] == "IG caption" and items["youtube"]["clip_title"] == "Epic moment"
    when = datetime.fromisoformat(items["youtube"]["scheduled_at"])
    assert timedelta(minutes=4) < when - utcnow() < timedelta(minutes=6)
    r = client.post("/api/v1/publishing/queue", json=body, headers=auth_headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_queued"
    bad = {"clip_id": str(make_clip(db, user).id), "account_ids": [str(yt.id)], "rights_confirmed": True,
           "youtube": {"title": "x" * 101}}
    assert client.post("/api/v1/publishing/queue", json=bad, headers=auth_headers).status_code == 422


def test_queue_uses_schedule_slot(db, user, auth_headers, client) -> None:
    acc = make_account(db, user)
    r = client.post("/api/v1/schedules", headers=auth_headers, json={
        "connected_account_id": str(acc.id), "frequency_type": "times", "times_of_day": ["09:00", "18:00"],
        "timezone": "Europe/Berlin", "daily_limit": 2})
    assert r.status_code == 201, r.text
    schedule_id = r.json()["id"]
    preview = client.get(f"/api/v1/schedules/{schedule_id}/preview", params={"count": 3}, headers=auth_headers).json()
    assert len(preview["slots"]) == 3 and preview["slots"][0]["local"].endswith(("CET", "CEST"))
    clip = make_clip(db, user)
    r = client.post("/api/v1/publishing/queue", headers=auth_headers, json={
        "clip_id": str(clip.id), "account_ids": [str(acc.id)], "rights_confirmed": True})
    item = r.json()[0]
    assert item["schedule_id"] == schedule_id
    assert datetime.fromisoformat(item["scheduled_at"]) == datetime.fromisoformat(preview["slots"][0]["at"])
    sched = client.get(f"/api/v1/schedules/{schedule_id}", headers=auth_headers).json()
    assert sched["next_run_at"] is not None
    r = client.patch(f"/api/v1/schedules/{schedule_id}", headers=auth_headers, json={"mode": "automatic"})
    assert r.status_code == 422  # rights confirmation needed for automatic mode
    r = client.patch(f"/api/v1/schedules/{schedule_id}", headers=auth_headers,
                     json={"mode": "automatic", "rights_confirmed": True, "auto_min_score": 70})
    assert r.status_code == 200 and r.json()["mode"] == "automatic"
    assert client.post(f"/api/v1/schedules/{schedule_id}/pause", headers=auth_headers).json()["is_active"] is False
    assert client.post(f"/api/v1/schedules/{schedule_id}/resume", headers=auth_headers).json()["is_active"] is True
    bad = client.post("/api/v1/schedules", headers=auth_headers, json={
        "connected_account_id": str(acc.id), "timezone": "Not/AZone"})
    assert bad.status_code == 422


def test_item_lifecycle_actions(db, user, auth_headers, client, no_dispatch) -> None:
    acc = make_account(db, user)
    clip = make_clip(db, user)
    item = client.post("/api/v1/publishing/queue", headers=auth_headers, json={
        "clip_id": str(clip.id), "account_ids": [str(acc.id)], "rights_confirmed": True}).json()[0]
    url = f"/api/v1/publishing/queue/{item['id']}"
    later = utcnow() + timedelta(days=2)
    r = client.patch(url, headers=auth_headers, json={"scheduled_at": _iso(later),
                                                      "payload": {"description": "new desc"}})
    assert r.status_code == 200 and r.json()["payload"]["description"] == "new desc"
    assert client.patch(url, headers=auth_headers, json={"payload": {"bogus": 1}}).status_code == 422
    assert client.patch(url, headers=auth_headers,
                        json={"scheduled_at": _iso(utcnow() - timedelta(hours=1))}).status_code == 422
    assert client.post(f"{url}/cancel", headers=auth_headers).json()["status"] == "cancelled"
    assert client.post(f"{url}/retry", headers=auth_headers).json()["status"] == "scheduled"
    r = client.post(f"{url}/publish-now", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "publishing" and no_dispatch == ["publish_queue_item"]
    assert client.post(f"{url}/cancel", headers=auth_headers).status_code == 409
    listed = client.get("/api/v1/publishing/queue", params={"status": "publishing,scheduled"},
                        headers=auth_headers).json()
    assert [i["id"] for i in listed] == [item["id"]]
    hist = client.get("/api/v1/publishing/history", params={"clip_id": str(clip.id)}, headers=auth_headers).json()
    assert {"scheduled", "edit", "cancel", "retry", "publish_now"} <= {h["action"] for h in hist}


def test_calendar_next_and_pause(db, user, auth_headers, client) -> None:
    acc = make_account(db, user)
    clip = make_clip(db, user)
    when = utcnow() + timedelta(hours=5)
    client.post("/api/v1/publishing/queue", headers=auth_headers, json={
        "clip_id": str(clip.id), "account_ids": [str(acc.id)], "rights_confirmed": True,
        "scheduled_at": _iso(when)})
    cal = client.get("/api/v1/publishing/calendar", headers=auth_headers, params={
        "start": _iso(utcnow()), "end": _iso(utcnow() + timedelta(days=1))}).json()
    assert len(cal) == 1 and cal[0]["clip_title"] == "Epic moment" and cal[0]["kind"] == "queue"
    nxt = client.get("/api/v1/publishing/next", headers=auth_headers).json()
    assert datetime.fromisoformat(nxt["next_publish_at"]) == when and nxt["paused"] is False
    assert nxt["accounts"][0]["next_publish_at"] is not None
    assert client.post("/api/v1/publishing/pause", headers=auth_headers).json() == {"paused": True}
    nxt = client.get("/api/v1/publishing/next", headers=auth_headers).json()
    assert nxt["paused"] is True and nxt["next_publish_at"] is None
    assert client.post("/api/v1/publishing/resume", headers=auth_headers).json() == {"paused": False}


def test_access_control_other_user_gets_404(db, user, auth_headers, other_headers, client) -> None:
    acc = make_account(db, user)
    clip = make_clip(db, user)
    item = client.post("/api/v1/publishing/queue", headers=auth_headers, json={
        "clip_id": str(clip.id), "account_ids": [str(acc.id)], "rights_confirmed": True}).json()[0]
    schedule = client.post("/api/v1/schedules", headers=auth_headers, json={
        "connected_account_id": str(acc.id), "interval_minutes": 120}).json()
    url = f"/api/v1/publishing/queue/{item['id']}"
    assert client.patch(url, headers=other_headers, json={"payload": {"title": "x"}}).status_code == 404
    for action in ("cancel", "retry", "publish-now"):
        assert client.post(f"{url}/{action}", headers=other_headers).status_code == 404
    assert client.get("/api/v1/publishing/queue", headers=other_headers).json() == []
    assert client.get("/api/v1/publishing/history", headers=other_headers).json() == []
    r = client.post("/api/v1/publishing/queue", headers=other_headers, json={
        "clip_id": str(clip.id), "account_ids": [str(acc.id)], "rights_confirmed": True})
    assert r.status_code == 404
    for method, path in (("get", ""), ("patch", ""), ("delete", ""), ("post", "/pause"), ("get", "/preview")):
        kwargs = {"json": {"name": "x"}} if method == "patch" else {}
        r = getattr(client, method)(f"/api/v1/schedules/{schedule['id']}{path}", headers=other_headers, **kwargs)
        assert r.status_code == 404, (method, path)
    assert client.post("/api/v1/schedules", headers=other_headers, json={
        "connected_account_id": str(acc.id), "interval_minutes": 60}).status_code == 404
    assert client.get("/api/v1/accounts", headers=other_headers).json() == []
    assert client.get("/api/v1/publishing/queue").status_code == 401
    assert client.delete(f"/api/v1/schedules/{schedule['id']}", headers=auth_headers).status_code == 204


def test_naive_datetimes_are_treated_as_utc(db, user, auth_headers, client) -> None:
    acc = make_account(db, user)
    naive = (utcnow() + timedelta(hours=2)).replace(tzinfo=None, microsecond=0)
    r = client.post("/api/v1/publishing/queue", headers=auth_headers, json={
        "clip_id": str(make_clip(db, user).id), "account_ids": [str(acc.id)], "rights_confirmed": True,
        "scheduled_at": naive.isoformat()})
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()[0]["scheduled_at"]) == naive.replace(tzinfo=UTC)
    cal = client.get("/api/v1/publishing/calendar", headers=auth_headers, params={
        "start": utcnow().replace(tzinfo=None).isoformat(),
        "end": (utcnow() + timedelta(days=1)).replace(tzinfo=None).isoformat()})
    assert cal.status_code == 200 and len(cal.json()) == 1
