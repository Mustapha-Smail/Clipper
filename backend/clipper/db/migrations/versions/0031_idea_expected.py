"""idea expected guess: what most viewers would guess before watching; the answer overturns it

Revision ID: d9f3b5a1c826
Revises: c4e8a2d6f017
Create Date: 2026-11-17 15:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'd9f3b5a1c826'
down_revision = 'c4e8a2d6f017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('ideas') as batch_op:
        batch_op.add_column(sa.Column('expected', sa.Text(), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('ideas') as batch_op:
        batch_op.drop_column('expected')
