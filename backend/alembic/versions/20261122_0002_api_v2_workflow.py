"""API v2 workflow: clip lifecycle (remove/upload/cleanup), source attribution/original deletion,
per-stage pipeline timings and sequential upload runs.

Existing data is kept: new columns are nullable or have server defaults, ``review_status`` stays
(unused), and clips that were already published to YouTube are marked ``uploaded`` with their
video id/URL so they are never uploaded twice.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.database import JSONType, UTCDateTime

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_default() -> sa.TextClause:
    bind = op.get_bind()
    return sa.text("'{}'::jsonb") if bind.dialect.name == "postgresql" else sa.text("'{}'")


def upgrade() -> None:
    with op.batch_alter_table('generated_clips', schema=None) as batch_op:
        batch_op.add_column(sa.Column('media_bytes', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('removed_at', UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('upload_status', sa.String(length=20), server_default='none', nullable=False))
        batch_op.add_column(sa.Column('youtube_video_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('youtube_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('uploaded_at', UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('upload_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('cleanup_status', sa.String(length=20), server_default='none', nullable=False))
        batch_op.add_column(sa.Column('cleaned_at', UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('cleanup_checked_at', UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column('cleanup_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('freed_bytes', sa.BigInteger(), server_default='0', nullable=False))
        batch_op.create_index('ix_generated_clips_source_start', ['video_source_id', 'start_time'], unique=False)
        batch_op.create_index('ix_generated_clips_user_upload', ['user_id', 'upload_status'], unique=False)

    with op.batch_alter_table('video_sources', schema=None) as batch_op:
        batch_op.add_column(sa.Column('processing_mode', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('attribution', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('original_deleted_at', UTCDateTime(), nullable=True))

    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stages', JSONType, server_default=_json_default(), nullable=False))

    op.create_table(
        'upload_runs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('video_source_id', sa.Uuid(), nullable=False),
        sa.Column('connected_account_id', sa.Uuid(), nullable=False),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('privacy', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.Column('current_clip_id', sa.Uuid(), nullable=True),
        sa.Column('failed_clip_id', sa.Uuid(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('rights_confirmed_at', UTCDateTime(), nullable=True),
        sa.Column('finished_at', UTCDateTime(), nullable=True),
        sa.Column('created_at', UTCDateTime(), nullable=False),
        sa.Column('updated_at', UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['connected_account_id'], ['connected_accounts.id'],
                                name=op.f('fk_upload_runs_connected_account_id_connected_accounts'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_upload_runs_user_id_users'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['video_source_id'], ['video_sources.id'],
                                name=op.f('fk_upload_runs_video_source_id_video_sources'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_upload_runs')),
    )
    with op.batch_alter_table('upload_runs', schema=None) as batch_op:
        batch_op.create_index('ix_upload_runs_user_created', ['user_id', 'created_at'], unique=False)
        batch_op.create_index('ix_upload_runs_source_created', ['video_source_id', 'created_at'], unique=False)
        batch_op.create_index('ix_upload_runs_status', ['status'], unique=False)
        batch_op.create_index('uq_upload_runs_account_running', ['connected_account_id'], unique=True,
                              postgresql_where=sa.text("status = 'running'"),
                              sqlite_where=sa.text("status = 'running'"))

    op.create_table(
        'upload_run_items',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('clip_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('queue_item_id', sa.Uuid(), nullable=True),
        sa.Column('progress', sa.Float(), nullable=True),
        sa.Column('youtube_video_id', sa.String(length=32), nullable=True),
        sa.Column('youtube_url', sa.String(length=500), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('started_at', UTCDateTime(), nullable=True),
        sa.Column('finished_at', UTCDateTime(), nullable=True),
        sa.Column('created_at', UTCDateTime(), nullable=False),
        sa.Column('updated_at', UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['clip_id'], ['generated_clips.id'],
                                name=op.f('fk_upload_run_items_clip_id_generated_clips'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['queue_item_id'], ['publishing_queue.id'],
                                name=op.f('fk_upload_run_items_queue_item_id_publishing_queue'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['run_id'], ['upload_runs.id'], name=op.f('fk_upload_run_items_run_id_upload_runs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_upload_run_items')),
        sa.UniqueConstraint('run_id', 'clip_id', name='uq_upload_run_items_clip'),
        sa.UniqueConstraint('run_id', 'position', name='uq_upload_run_items_position'),
    )
    with op.batch_alter_table('upload_run_items', schema=None) as batch_op:
        batch_op.create_index('ix_upload_run_items_clip', ['clip_id'], unique=False)
        batch_op.create_index('ix_upload_run_items_queue_item', ['queue_item_id'], unique=False)

    _backfill()


def _backfill() -> None:
    """Clips already published to YouTube -> upload_status 'uploaded' (+ video id/URL); media size
    of rendered clips from output_size (refreshed by GET /storage later)."""
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT clip_id, external_id, external_url, published_at, updated_at FROM publishing_queue "
        "WHERE platform = 'youtube' AND external_id IS NOT NULL "
        "AND status IN ('published', 'processing_remote') ORDER BY updated_at")).fetchall()
    for clip_id, external_id, external_url, published_at, updated_at in rows:
        bind.execute(sa.text(
            "UPDATE generated_clips SET upload_status = 'uploaded', youtube_video_id = :vid, "
            "youtube_url = :url, uploaded_at = :at WHERE id = :id"),
            {"vid": str(external_id)[:32], "url": external_url or f"https://www.youtube.com/watch?v={external_id}",
             "at": published_at or updated_at, "id": clip_id})
    bind.execute(sa.text("UPDATE generated_clips SET media_bytes = output_size "
                         "WHERE media_bytes IS NULL AND output_size IS NOT NULL"))


def downgrade() -> None:
    with op.batch_alter_table('upload_run_items', schema=None) as batch_op:
        batch_op.drop_index('ix_upload_run_items_queue_item')
        batch_op.drop_index('ix_upload_run_items_clip')
    op.drop_table('upload_run_items')
    with op.batch_alter_table('upload_runs', schema=None) as batch_op:
        batch_op.drop_index('uq_upload_runs_account_running')
        batch_op.drop_index('ix_upload_runs_status')
        batch_op.drop_index('ix_upload_runs_source_created')
        batch_op.drop_index('ix_upload_runs_user_created')
    op.drop_table('upload_runs')
    with op.batch_alter_table('processing_jobs', schema=None) as batch_op:
        batch_op.drop_column('stages')
    with op.batch_alter_table('video_sources', schema=None) as batch_op:
        batch_op.drop_column('original_deleted_at')
        batch_op.drop_column('attribution')
        batch_op.drop_column('processing_mode')
    with op.batch_alter_table('generated_clips', schema=None) as batch_op:
        batch_op.drop_index('ix_generated_clips_user_upload')
        batch_op.drop_index('ix_generated_clips_source_start')
        for col in ('freed_bytes', 'cleanup_error', 'cleanup_checked_at', 'cleaned_at', 'cleanup_status',
                    'upload_error', 'uploaded_at', 'youtube_url', 'youtube_video_id', 'upload_status',
                    'removed_at', 'media_bytes'):
            batch_op.drop_column(col)
