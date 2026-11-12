"""storyboard style

Revision ID: 3c9e1f7a2b10
Revises: 57b6b23db558
Create Date: 2026-11-12 20:40:00
"""
import sqlalchemy as sa
from alembic import op

revision = '3c9e1f7a2b10'
down_revision = '57b6b23db558'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Storyboards made before the ink style are flat ones.
    with op.batch_alter_table('storyboards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('style', sa.String(length=8), nullable=False, server_default='flat'))


def downgrade() -> None:
    with op.batch_alter_table('storyboards', schema=None) as batch_op:
        batch_op.drop_column('style')
