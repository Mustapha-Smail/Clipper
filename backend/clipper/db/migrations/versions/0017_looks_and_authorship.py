"""storyboard looks and the authorship log

Revision ID: 7d1f4a9c3e82
Revises: 2e8b5d1f7c63
Create Date: 2026-11-12 23:55:00
"""
import sqlalchemy as sa
from alembic import op

revision = '7d1f4a9c3e82'
down_revision = '2e8b5d1f7c63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('look', sa.String(length=12), nullable=False, server_default='ink'))
    op.create_table(
        'studio_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('script_id', sa.Integer(), nullable=False),
        sa.Column('storyboard_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('by', sa.String(length=4), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('amount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['script_id'], ['scripts.id'], name='fk_studio_events_script_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_studio_events_script_id', 'studio_events', ['script_id'])


def downgrade() -> None:
    op.drop_index('ix_studio_events_script_id', table_name='studio_events')
    op.drop_table('studio_events')
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('look')
