"""Per-video framing: ``video_sources.framing_template`` holds the framing saved on one clip so it is
applied to every clip of the same video (existing and new).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONType

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('video_sources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('framing_template', JSONType, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('video_sources', schema=None) as batch_op:
        batch_op.drop_column('framing_template')
