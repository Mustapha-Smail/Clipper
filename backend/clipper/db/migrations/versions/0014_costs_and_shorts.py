"""costs per video, Shorts-only ideas

Revision ID: 8d2f4a6c1e90
Revises: 3c9e1f7a2b10
Create Date: 2026-11-12 22:20:00
"""
import sqlalchemy as sa
from alembic import op

revision = '8d2f4a6c1e90'
down_revision = '3c9e1f7a2b10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ref', sa.String(length=40), nullable=True))
        batch_op.create_index('ix_ai_usage_ref', ['ref'])
    with op.batch_alter_table('ideas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('video', sa.String(length=8), nullable=False, server_default='long'))
    with op.batch_alter_table('scripts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('video', sa.String(length=8), nullable=False, server_default='long'))
        batch_op.add_column(sa.Column('target_seconds', sa.Integer(), nullable=False, server_default='50'))


def downgrade() -> None:
    with op.batch_alter_table('scripts', schema=None) as batch_op:
        batch_op.drop_column('target_seconds')
        batch_op.drop_column('video')
    with op.batch_alter_table('ideas', schema=None) as batch_op:
        batch_op.drop_column('video')
    with op.batch_alter_table('ai_usage', schema=None) as batch_op:
        batch_op.drop_index('ix_ai_usage_ref')
        batch_op.drop_column('ref')
