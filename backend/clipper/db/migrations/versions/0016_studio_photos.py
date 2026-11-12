"""archive photos

Revision ID: 2e8b5d1f7c63
Revises: 5a7c3e9d2b41
Create Date: 2026-11-12 23:30:00
"""
import sqlalchemy as sa
from alembic import op

revision = '2e8b5d1f7c63'
down_revision = '5a7c3e9d2b41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'studio_photos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('query', sa.String(length=200), nullable=False),
        sa.Column('rank', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('source', sa.String(length=40), nullable=False, server_default='Wikimedia Commons'),
        sa.Column('key', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('title', sa.Text(), nullable=False, server_default=''),
        sa.Column('creator', sa.Text(), nullable=False, server_default=''),
        sa.Column('license', sa.String(length=60), nullable=False, server_default=''),
        sa.Column('license_url', sa.Text(), nullable=False, server_default=''),
        sa.Column('landing_url', sa.Text(), nullable=False, server_default=''),
        sa.Column('credit', sa.Text(), nullable=False, server_default=''),
        sa.Column('width', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('height', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('path', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('query'),
    )


def downgrade() -> None:
    op.drop_table('studio_photos')
