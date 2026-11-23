"""/schedules -- per-account publishing schedules (interval / specific times, timezone, limits)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import PublishingSchedule
from app.modules.scheduling import service
from app.modules.scheduling.schemas import PreviewOut, ScheduleCreate, ScheduleOut, ScheduleUpdate, SlotOut
from app.modules.scheduling.slots import describe_local

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _owned(db: DbSession, user: CurrentUser, schedule_id: uuid.UUID) -> PublishingSchedule:
    return get_owned(db, PublishingSchedule, schedule_id, user, name="Schedule")


@router.get("", response_model=list[ScheduleOut])
def list_schedules(user: CurrentUser, db: DbSession) -> list[PublishingSchedule]:
    """All schedules of the current user."""
    return service.list_schedules(db, user)


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScheduleCreate, user: CurrentUser, db: DbSession) -> PublishingSchedule:
    """Create a schedule for one connected account."""
    s = service.create_schedule(db, user, body.model_dump(exclude_none=True))
    db.commit()
    return s


@router.get("/{schedule_id}", response_model=ScheduleOut)
def get_schedule(schedule_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PublishingSchedule:
    return _owned(db, user, schedule_id)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: uuid.UUID, body: ScheduleUpdate, user: CurrentUser,
                    db: DbSession) -> PublishingSchedule:
    """Partial update; fields explicitly set to null clear optional values (window, start_at)."""
    s = _owned(db, user, schedule_id)
    values = body.model_dump(exclude_unset=True)
    service.update_schedule(db, s, values)
    db.commit()
    return s


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Response:
    """Delete the schedule and cancel the publications it placed that have not started."""
    service.delete_schedule(db, _owned(db, user, schedule_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{schedule_id}/pause", response_model=ScheduleOut)
def pause_schedule(schedule_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PublishingSchedule:
    s = service.set_schedule_active(db, _owned(db, user, schedule_id), False)
    db.commit()
    return s


@router.post("/{schedule_id}/resume", response_model=ScheduleOut)
def resume_schedule(schedule_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PublishingSchedule:
    s = service.set_schedule_active(db, _owned(db, user, schedule_id), True)
    db.commit()
    return s


@router.get("/{schedule_id}/preview", response_model=PreviewOut)
def preview(schedule_id: uuid.UUID, user: CurrentUser, db: DbSession,
            count: int = Query(10, ge=1, le=100)) -> PreviewOut:
    """Next free slots (UTC + local wall time strings) honouring the account's queue."""
    s = _owned(db, user, schedule_id)
    slots = service.preview_slots(db, s, count)
    return PreviewOut(schedule_id=s.id, timezone=s.timezone,
                      slots=[SlotOut(at=t, local=describe_local(t, s.timezone)) for t in slots])
