"""three thumbnails per long video, for YouTube's A/B test

Revision ID: f2b8d4e6a913
Revises: e5c1a7f3b820
Create Date: 2026-11-15 23:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'f2b8d4e6a913'
down_revision = 'e5c1a7f3b820'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('thumbnails', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('thumbnails')
