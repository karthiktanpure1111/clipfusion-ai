"""/notifications — in-app notifications of the current user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update

from app.core.database import utcnow
from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import Notification
from app.modules.common.pagination import PageParams, page_params, paginate
from app.modules.notifications.schemas import NotificationOut, NotificationPage, ReadAllOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
def list_notifications(user: CurrentUser, db: DbSession, params: PageParams = Depends(page_params),
                       unread_only: bool = False) -> NotificationPage:
    """Newest first, with the total unread count."""
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows, total = paginate(db, stmt.order_by(Notification.created_at.desc(), Notification.id), params)
    unread = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id,
                                                                 Notification.read_at.is_(None))) or 0
    return NotificationPage(items=[NotificationOut.model_validate(n) for n in rows], total=total,
                            unread_count=int(unread))


@router.post("/read-all", response_model=ReadAllOut)
def read_all(user: CurrentUser, db: DbSession) -> ReadAllOut:
    res = db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
                     .values(read_at=utcnow()))
    db.commit()
    return ReadAllOut(updated=int(res.rowcount or 0))


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> NotificationOut:
    n = get_owned(db, Notification, notification_id, user, name="Notification")
    if n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    return NotificationOut.model_validate(n)
