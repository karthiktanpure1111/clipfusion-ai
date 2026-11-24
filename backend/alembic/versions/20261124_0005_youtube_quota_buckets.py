"""YouTube quota buckets: uploads move to their own bucket.

YouTube now charges ``videos.insert`` 1 unit in a dedicated "video uploads" bucket (100/day by default) instead of
~1,600 units of the shared 10,000-unit bucket. Earlier ClipFusion versions recorded 1,600 units per upload in the
``youtube`` rows of ``api_quota_usage``; this moves each recorded upload (one per "upload session started" history
entry) out of that bucket and into ``youtube_uploads`` as 1 unit, so today's numbers are right after upgrading.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24 12:00:00
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_UPLOAD_COST = 1600
PACIFIC = ZoneInfo("America/Los_Angeles")


def _when(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _scope(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return str(value)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT connected_account_id, created_at FROM publishing_history "
        "WHERE platform = 'youtube' AND action = 'upload' AND message = 'YouTube resumable upload session started' "
        "AND connected_account_id IS NOT NULL")).all()
    uploads: Counter[tuple[str, date]] = Counter()
    for account_id, created in rows:
        when = _when(created)
        if when is not None:
            uploads[(_scope(account_id), when.astimezone(PACIFIC).date())] += 1
    usage = sa.table("api_quota_usage", sa.column("id", sa.Uuid), sa.column("provider", sa.String),
                     sa.column("scope_key", sa.String), sa.column("day", sa.Date),
                     sa.column("units_used", sa.Integer), sa.column("updated_at", sa.DateTime(timezone=True)))
    now = datetime.now(UTC)
    for (scope, day), n in uploads.items():
        old = bind.execute(sa.select(usage.c.units_used).where(
            usage.c.provider == "youtube", usage.c.scope_key == scope, usage.c.day == day)).scalar()
        if old is None:
            continue  # nothing was recorded for that day (or it was already converted)
        bind.execute(usage.update().where(usage.c.provider == "youtube", usage.c.scope_key == scope,
                                          usage.c.day == day)
                     .values(units_used=max(int(old) - LEGACY_UPLOAD_COST * n, 0), updated_at=now))
        moved = bind.execute(sa.select(usage.c.units_used).where(
            usage.c.provider == "youtube_uploads", usage.c.scope_key == scope, usage.c.day == day)).scalar()
        if moved is None:
            bind.execute(usage.insert().values(id=uuid.uuid4(), provider="youtube_uploads", scope_key=scope, day=day,
                                               units_used=n, updated_at=now))


def downgrade() -> None:
    pass  # the old single-bucket numbers cannot be told apart again; nothing to undo
