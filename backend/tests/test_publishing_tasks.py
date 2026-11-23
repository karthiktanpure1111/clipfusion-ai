"""Task registration and the periodic wrappers in app.workers.tasks_publishing."""

from __future__ import annotations

from datetime import timedelta

import httpx
import respx

from app.core.config import get_settings
from app.core.database import utcnow
from app.integrations import google_oauth
from app.models.enums import AccountStatus, JobType
from app.workers import registry, tasks_publishing
from tests.test_publishing_factories import make_account


def test_tasks_and_periodics_registered() -> None:
    tasks = registry._TASKS
    assert tasks["publish_queue_item"].queue == "publish"
    assert tasks["dispatch_due_publications"].fn is tasks_publishing.dispatch_due_publications
    assert registry._JOB_TASKS[JobType.PUBLISH] == "publish_queue_item"
    assert registry._JOB_TASKS[JobType.ANALYTICS] == "refresh_platform_analytics"
    expected = {"dispatch_due_publications": 60, "poll_remote_publications": 120,
                "refresh_account_tokens": 1800, "refresh_platform_analytics": 21600}
    assert {k: registry._PERIODIC[k].every_seconds for k in expected} == expected


def test_dispatch_wrapper_runs(monkeypatch) -> None:
    monkeypatch.setattr("app.workers.jobs.enqueue", lambda *a, **k: "t")
    assert tasks_publishing.dispatch_due_publications() >= 0
    assert tasks_publishing.poll_remote_publications() >= 0


@respx.mock
def test_refresh_account_tokens_marks_revoked(db, user, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", "gid")
    monkeypatch.setattr(get_settings(), "google_client_secret", "gsecret")
    monkeypatch.setattr("app.integrations.http._sleep", lambda s: None)
    acc = make_account(db, user, expires_in=600)
    acc.token_expires_at = utcnow() + timedelta(minutes=10)
    db.commit()
    respx.post(google_oauth.TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    tasks_publishing.refresh_account_tokens()
    db.refresh(acc)
    assert acc.status == AccountStatus.REVOKED
