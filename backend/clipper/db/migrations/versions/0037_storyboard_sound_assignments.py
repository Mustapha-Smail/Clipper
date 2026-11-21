"""creator-selected timed sound-effect cues

Revision ID: 8b6f4e2d1a90
Revises: 7f1c2d9a0b34
Create Date: 2026-11-21 16:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "8b6f4e2d1a90"
down_revision = "7f1c2d9a0b34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storyboard_sounds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_id", sa.Integer(), nullable=False),
        sa.Column("sound_id", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gain", sa.Float(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["storyboard_id"], ["storyboards.id"], ondelete="CASCADE",
                                name="fk_storyboard_sounds_storyboard_id"),
        sa.ForeignKeyConstraint(["sound_id"], ["studio_sounds.id"], ondelete="RESTRICT",
                                name="fk_storyboard_sounds_sound_id"),
        sa.UniqueConstraint("storyboard_id", "at", "rule", name="ux_storyboard_sounds_cue"),
    )
    op.create_index("ix_storyboard_sounds_storyboard_id", "storyboard_sounds", ["storyboard_id"])
    op.create_index("ix_storyboard_sounds_sound_id", "storyboard_sounds", ["sound_id"])


def downgrade() -> None:
    op.drop_index("ix_storyboard_sounds_sound_id", table_name="storyboard_sounds")
    op.drop_index("ix_storyboard_sounds_storyboard_id", table_name="storyboard_sounds")
    op.drop_table("storyboard_sounds")
