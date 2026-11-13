"""places for the cartoon look

Revision ID: 4c8a1e7b2d90
Revises: 9b3e6c2a5f14
Create Date: 2026-11-13 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = '4c8a1e7b2d90'
down_revision = '9b3e6c2a5f14'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'studio_places',
        sa.Column('name', sa.String(length=48), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('svg', sa.Text(), nullable=True),
        sa.Column('svg_tall', sa.Text(), nullable=True),
        sa.Column('origin', sa.String(length=16), nullable=False, server_default='claude'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    op.drop_table('studio_places')
