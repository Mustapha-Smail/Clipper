"""campaigns remember whether Claude read them or the scout read the page layout

Revision ID: 7b3d9e5a1c20
Revises: e2f7a1c94b36
Create Date: 2026-11-13 16:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '7b3d9e5a1c20'
down_revision = 'e2f7a1c94b36'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('campaigns') as batch_op:
        batch_op.add_column(sa.Column('read_by', sa.String(length=8), nullable=False, server_default='ai'))


def downgrade() -> None:
    with op.batch_alter_table('campaigns') as batch_op:
        batch_op.drop_column('read_by')
