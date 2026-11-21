"""licensed sound-effect library and provenance

Revision ID: 7f1c2d9a0b34
Revises: 9d4a7c6e5b10
Create Date: 2026-11-21 15:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "7f1c2d9a0b34"
down_revision = "9d4a7c6e5b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "studio_sounds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("path", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration", sa.Float(), nullable=False, server_default="0"),
        sa.Column("gain", sa.Float(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("provider_id", sa.String(length=100), nullable=False),
        sa.Column("upstream_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("creator", sa.Text(), nullable=False, server_default=""),
        sa.Column("creator_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("landing_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("license", sa.String(length=16), nullable=False),
        sa.Column("license_version", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("license_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("attribution", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_id", name="ux_studio_sounds_provider_id"),
    )
    op.create_index("ix_studio_sounds_kind", "studio_sounds", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_studio_sounds_kind", table_name="studio_sounds")
    op.drop_table("studio_sounds")
