"""manual publication state for long Studio videos

Revision ID: d7a4b9c2e631
Revises: b3e8f1a6c942
Create Date: 2026-11-15 20:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'd7a4b9c2e631'
down_revision = 'b3e8f1a6c942'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('published_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('published_at')
