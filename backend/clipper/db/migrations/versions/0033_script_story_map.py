"""source-backed story map for Studio scripts

Revision ID: b6e2c8f4a913
Revises: e7a4c19b3f52
Create Date: 2026-11-19 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "b6e2c8f4a913"
down_revision = "e7a4c19b3f52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scripts") as batch_op:
        batch_op.add_column(sa.Column("story_map", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scripts") as batch_op:
        batch_op.drop_column("story_map")
