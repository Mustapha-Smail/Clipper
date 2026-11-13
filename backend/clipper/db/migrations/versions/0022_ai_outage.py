"""mark AI calls that failed because the service was unavailable

Revision ID: 6e2d9b4f1a37
Revises: 7b3d9e5a1c20
Create Date: 2026-11-13 17:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '6e2d9b4f1a37'
down_revision = '7b3d9e5a1c20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('ai_usage') as batch_op:
        batch_op.add_column(sa.Column('outage', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('ai_usage') as batch_op:
        batch_op.drop_column('outage')
