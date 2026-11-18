"""video analytics: YouTube Analytics summary and retention curve per published video

Revision ID: e7a4c19b3f52
Revises: d9f3b5a1c826
Create Date: 2026-11-18 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'e7a4c19b3f52'
down_revision = 'd9f3b5a1c826'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'video_analytics',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('storyboard_id', sa.Integer(), nullable=False),
        sa.Column('youtube_id', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.Column('duration_s', sa.Float(), nullable=True),
        sa.Column('views', sa.Integer(), nullable=True),
        sa.Column('minutes_watched', sa.Float(), nullable=True),
        sa.Column('average_view_s', sa.Float(), nullable=True),
        sa.Column('average_view_pct', sa.Float(), nullable=True),
        sa.Column('retention', sa.JSON(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['storyboard_id'], ['storyboards.id'], name='fk_video_analytics_storyboard_id',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_video_analytics_storyboard_id', 'video_analytics', ['storyboard_id'])
    op.create_index('ux_video_analytics_board_video', 'video_analytics', ['storyboard_id', 'youtube_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ux_video_analytics_board_video', table_name='video_analytics')
    op.drop_index('ix_video_analytics_storyboard_id', table_name='video_analytics')
    op.drop_table('video_analytics')
