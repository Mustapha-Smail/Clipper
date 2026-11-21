"""per-storyboard attention sound modes and render craft snapshot

Revision ID: f4b92d7186ce
Revises: b6e2c8f4a913
Create Date: 2026-11-19 17:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "f4b92d7186ce"
down_revision = "b6e2c8f4a913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("storyboards") as batch_op:
        batch_op.add_column(sa.Column("starter_mode", sa.String(length=8), nullable=False,
                                      server_default="default"))
        batch_op.add_column(sa.Column("riser_mode", sa.String(length=8), nullable=False,
                                      server_default="default"))
        batch_op.add_column(sa.Column("render_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("storyboards") as batch_op:
        batch_op.drop_column("render_meta")
        batch_op.drop_column("riser_mode")
        batch_op.drop_column("starter_mode")
