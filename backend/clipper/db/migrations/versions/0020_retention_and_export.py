"""grace period for unapproved renders, and the export of approved videos

Revision ID: e2f7a1c94b36
Revises: 4c8a1e7b2d90
Create Date: 2026-11-13 15:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'e2f7a1c94b36'
down_revision = '4c8a1e7b2d90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('review_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('export', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('export')
        batch_op.drop_column('expired_at')
        batch_op.drop_column('review_at')
