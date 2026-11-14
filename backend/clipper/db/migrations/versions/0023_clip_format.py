"""clips can be vertical or horizontal: the video's choice and the shape each clip was rendered in

Revision ID: 9c4a7e2b5d18
Revises: 6e2d9b4f1a37
Create Date: 2026-11-14 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '9c4a7e2b5d18'
down_revision = '6e2d9b4f1a37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('videos') as batch_op:
        batch_op.add_column(sa.Column('clip_format', sa.String(length=12), nullable=True))
    with op.batch_alter_table('clips') as batch_op:
        batch_op.add_column(sa.Column('format', sa.String(length=12), nullable=False, server_default='vertical'))


def downgrade() -> None:
    with op.batch_alter_table('clips') as batch_op:
        batch_op.drop_column('format')
    with op.batch_alter_table('videos') as batch_op:
        batch_op.drop_column('clip_format')
