"""posts: approved Shorts scheduled on Buffer, one row per channel

Revision ID: b3e8f1a6c942
Revises: 9c4a7e2b5d18
Create Date: 2026-11-14 23:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'b3e8f1a6c942'
down_revision = '9c4a7e2b5d18'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'posts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('clip_id', sa.Integer(), nullable=True),
        sa.Column('storyboard_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('channel_id', sa.String(length=40), nullable=False),
        sa.Column('service', sa.String(length=16), nullable=False),
        sa.Column('channel_name', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('ai_label', sa.Boolean(), nullable=False),
        sa.Column('mode', sa.String(length=8), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('buffer_id', sa.String(length=40), nullable=True),
        sa.Column('media_key', sa.Text(), nullable=True),
        sa.Column('media_url', sa.Text(), nullable=True),
        sa.Column('link', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['clip_id'], ['clips.id'], name='fk_posts_clip_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['storyboard_id'], ['storyboards.id'], name='fk_posts_storyboard_id',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_posts_clip_id', 'posts', ['clip_id'])
    op.create_index('ix_posts_storyboard_id', 'posts', ['storyboard_id'])
    op.create_index('ix_posts_status', 'posts', ['status'])


def downgrade() -> None:
    op.drop_index('ix_posts_status', table_name='posts')
    op.drop_index('ix_posts_storyboard_id', table_name='posts')
    op.drop_index('ix_posts_clip_id', table_name='posts')
    op.drop_table('posts')
