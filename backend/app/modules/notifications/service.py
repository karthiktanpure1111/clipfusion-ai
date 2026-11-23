"""In-app notifications. Call :func:`notify` from services/workers (commit is the caller's job)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import Notification
from app.models.enums import NotificationType


def notify(db: Session, user_id: uuid.UUID, type_: NotificationType | str, title: str, body: str = "",
           link: str | None = None, data: dict[str, Any] | None = None) -> Notification:
    n = Notification(user_id=user_id, type=str(type_), title=title[:200], body=body, link=link, data=data or {})
    db.add(n)
    db.flush()
    return n
