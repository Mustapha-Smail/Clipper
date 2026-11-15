"""the AI director's last pass per storyboard

Revision ID: e5c1a7f3b820
Revises: d7a4b9c2e631
Create Date: 2026-11-15 22:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'e5c1a7f3b820'
down_revision = 'd7a4b9c2e631'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('director', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('director')
