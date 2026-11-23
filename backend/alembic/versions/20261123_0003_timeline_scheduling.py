"""Timeline scheduling: ``publishing_queue.origin`` marks publications spaced out from the video page or by
auto-schedule ("timeline"), so missed slots can be shifted and timeline order kept.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('publishing_queue', schema=None) as batch_op:
        batch_op.add_column(sa.Column('origin', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('publishing_queue', schema=None) as batch_op:
        batch_op.drop_column('origin')
