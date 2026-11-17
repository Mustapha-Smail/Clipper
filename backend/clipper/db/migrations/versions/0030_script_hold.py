"""script hold check: the sentence roles of the hold check, with the hash of the text they label

Revision ID: c4e8a2d6f017
Revises: b7d2f4a8c915
Create Date: 2026-11-17 14:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'c4e8a2d6f017'
down_revision = 'b7d2f4a8c915'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('scripts') as batch_op:
        batch_op.add_column(sa.Column('hold', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('scripts') as batch_op:
        batch_op.drop_column('hold')
