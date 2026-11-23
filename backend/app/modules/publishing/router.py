"""/publishing -- queue, history, calendar, next publish time, user-level pause/resume."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.models import PublishingHistory
from app.modules.publishing import service
from app.modules.publishing.schemas import (
    AutoScheduleIn,
    AutoScheduleOut,
    CalendarEntry,
    CancelledOut,
    HistoryOut,
    NextOut,
    PausedOut,
    QueueCreate,
    QueueItemOut,
    QueueUpdate,
    TimelineCancel,
    TimelineCreate,
    TimelineNextOut,
    TimelineOut,
)
from app.modules.scheduling import timeline

router = APIRouter(prefix="/publishing", tags=["publishing"])


@router.get("/queue", response_model=list[QueueItemOut])
def list_queue(user: CurrentUser, db: DbSession, status_: str | None = Query(None, alias="status"),
               platform: str | None = None, account: uuid.UUID | None = None,
               from_: datetime | None = Query(None, alias="from"), to: datetime | None = None,
               limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Queue items; ``status`` accepts a comma-separated list."""
    items = service.list_queue(db, user, status=status_, platform=platform, account_id=account, start=from_,
                               end=to, limit=limit)
    return service.serialize_items(db, items)


@router.post("/queue", response_model=list[QueueItemOut], status_code=status.HTTP_201_CREATED)
def create_queue(body: QueueCreate, user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    """Queue a clip for one or more accounts (requires ``rights_confirmed``)."""
    return service.serialize_items(db, service.create_queue_items(db, user, body))


@router.patch("/queue/{item_id}", response_model=QueueItemOut)
def update_queue_item(item_id: uuid.UUID, body: QueueUpdate, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    item = service.update_item(db, user, item_id, scheduled_at=body.scheduled_at, payload=body.payload)
    return service.serialize_items(db, [item])[0]


@router.post("/queue/{item_id}/cancel", response_model=QueueItemOut)
def cancel(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.serialize_items(db, [service.cancel_item(db, user, item_id)])[0]


@router.post("/queue/{item_id}/retry", response_model=QueueItemOut)
def retry(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.serialize_items(db, [service.retry_item(db, user, item_id)])[0]


@router.post("/queue/{item_id}/publish-now", response_model=QueueItemOut)
def publish_now(item_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return service.serialize_items(db, [service.publish_now(db, user, item_id)])[0]


@router.get("/history", response_model=list[HistoryOut])
def history(user: CurrentUser, db: DbSession, clip_id: uuid.UUID | None = None, platform: str | None = None,
            limit: int = Query(100, ge=1, le=500)) -> list[PublishingHistory]:
    return service.list_history(db, user, clip_id=clip_id, platform=platform, limit=limit)


@router.get("/calendar", response_model=list[CalendarEntry])
def calendar(start: datetime, end: datetime, user: CurrentUser, db: DbSession) -> list[dict[str, Any]]:
    """Scheduled + published + failed publications between ``start`` and ``end``."""
    return service.calendar(db, user, start, end)


@router.get("/next", response_model=NextOut)
def next_publish(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Next publication time overall and per account (+ next free schedule slot)."""
    return service.next_times(db, user)


@router.post("/pause", response_model=PausedOut)
def pause(user: CurrentUser, db: DbSession) -> dict[str, bool]:
    """Pause all automatic publishing for the user."""
    return service.set_paused(db, user, True)


@router.post("/resume", response_model=PausedOut)
def resume(user: CurrentUser, db: DbSession) -> dict[str, bool]:
    """Resume publishing; overdue scheduled items move to the next free slots."""
    return service.set_paused(db, user, False)


# ------------------------------------------------------------------ timeline
@router.post("/timeline", response_model=TimelineOut, status_code=status.HTTP_201_CREATED,
             responses={409: {"description": "nothing_to_schedule | youtube_auth_expired | permission_missing"}})
def schedule_timeline(body: TimelineCreate, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Schedule a video's generated clips one by one in timeline order, ``gap_minutes`` apart."""
    items = timeline.schedule_source(db, user, video_source_id=body.video_source_id,
                                     connected_account_id=body.connected_account_id, gap_minutes=body.gap_minutes,
                                     start_at=body.start_at, privacy=body.privacy, clip_ids=body.clip_ids,
                                     rights_confirmed=body.rights_confirmed)
    return timeline.timeline_out(db, items)


@router.get("/timeline/next", response_model=TimelineNextOut)
def timeline_next(account_id: uuid.UUID, user: CurrentUser, db: DbSession,
                  gap_minutes: int = Query(60, ge=15, le=10080), count: int = Query(1, ge=1, le=200)) -> dict[str, Any]:
    """The upload times ``count`` posts of the account would get now (the first right away when the channel is
    free, then one per gap, never within a gap of another scheduled post)."""
    from app.core.deps import get_owned
    from app.models import ConnectedAccount

    account = get_owned(db, ConnectedAccount, account_id, user, name="Account")
    slots = timeline.plan_slots(db, account.id, timedelta(minutes=gap_minutes), count)
    return {"next_at": slots[0], "slots": slots}


@router.post("/timeline/cancel", response_model=CancelledOut)
def cancel_timeline(body: TimelineCancel, user: CurrentUser, db: DbSession) -> dict[str, int]:
    """Cancel every scheduled (not yet uploading) publication of a video's clips."""
    return {"cancelled": timeline.cancel_source(db, user, body.video_source_id)}


@router.get("/auto-schedule", response_model=AutoScheduleOut)
def get_auto_schedule(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return timeline.auto_out(db, user)


@router.put("/auto-schedule", response_model=AutoScheduleOut)
def put_auto_schedule(body: AutoScheduleIn, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Auto-schedule: every clip that finishes rendering goes on the account's timeline, ``gap_minutes`` after
    the previous post. Switching it on needs ``rights_confirmed`` once."""
    return timeline.set_auto(db, user, enabled=body.enabled, connected_account_id=body.connected_account_id,
                             gap_minutes=body.gap_minutes, privacy=body.privacy,
                             rights_confirmed=body.rights_confirmed)
