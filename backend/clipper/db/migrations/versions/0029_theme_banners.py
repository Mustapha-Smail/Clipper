"""theme banners

Revision ID: b7d2f4a8c915
Revises: a3c9e1f7b204
Create Date: 2026-11-17 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'b7d2f4a8c915'
down_revision = 'a3c9e1f7b204'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('themes') as batch_op:
        batch_op.add_column(sa.Column('banner', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('themes') as batch_op:
        batch_op.drop_column('banner')
