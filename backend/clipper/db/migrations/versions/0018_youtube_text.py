"""youtube title and description per storyboard

Revision ID: 9b3e6c2a5f14
Revises: 7d1f4a9c3e82
Create Date: 2026-11-13 09:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '9b3e6c2a5f14'
down_revision = '7d1f4a9c3e82'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('youtube', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('youtube')
