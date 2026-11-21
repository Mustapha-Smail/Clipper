"""strategy, lineage, captions and daily metric observations

Revision ID: 9d4a7c6e5b10
Revises: f4b92d7186ce
Create Date: 2026-11-21 12:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = "9d4a7c6e5b10"
down_revision = "f4b92d7186ce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Server defaults make existing rows safe immediately. Keep `video` and
    # `format` untouched: they are older, deliberately orthogonal contracts.
    with op.batch_alter_table("ideas") as batch_op:
        batch_op.add_column(sa.Column("form", sa.String(length=12), nullable=False, server_default="long"))
        batch_op.add_column(sa.Column("story_shape", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("parent_idea_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("lineage_kind", sa.String(length=16), nullable=False, server_default="root"))
        batch_op.create_index("ix_ideas_parent_idea_id", ["parent_idea_id"])
        batch_op.create_foreign_key("fk_ideas_parent_idea_id", "ideas", ["parent_idea_id"], ["id"],
                                    ondelete="SET NULL")
    with op.batch_alter_table("scripts") as batch_op:
        batch_op.add_column(sa.Column("form", sa.String(length=12), nullable=False, server_default="long"))
        batch_op.add_column(sa.Column("story_shape", sa.String(length=16), nullable=True))
    with op.batch_alter_table("storyboards") as batch_op:
        batch_op.add_column(sa.Column("form", sa.String(length=12), nullable=False, server_default="short"))
        batch_op.add_column(sa.Column("story_shape", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("caption_mode", sa.String(length=8), nullable=False, server_default="default"))

    bind = op.get_bind()
    # Old rows already encode their distribution intent in video/format. Do not
    # infer their editorial shape from mutable prose or old map templates.
    bind.execute(sa.text("UPDATE ideas SET form = CASE WHEN video = 'short' THEN 'short' ELSE 'long' END"))
    bind.execute(sa.text("UPDATE scripts SET form = CASE WHEN video = 'short' THEN 'short' ELSE 'long' END"))
    bind.execute(sa.text("UPDATE storyboards SET form = CASE WHEN format = 'long' THEN 'long' ELSE 'short' END"))

    op.create_table(
        "video_metric_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_id", sa.Integer(), nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_day", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("minutes_watched", sa.Float(), nullable=True),
        sa.Column("average_view_s", sa.Float(), nullable=True),
        sa.Column("average_view_pct", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_id"], ["storyboards.id"],
                                name="fk_video_metric_snapshots_storyboard_id", ondelete="CASCADE"),
        sa.UniqueConstraint("source_key", "observed_day", name="ux_video_metric_snapshots_source_day"),
    )
    op.create_index("ix_video_metric_snapshots_storyboard_id", "video_metric_snapshots", ["storyboard_id"])


def downgrade() -> None:
    op.drop_index("ix_video_metric_snapshots_storyboard_id", table_name="video_metric_snapshots")
    op.drop_table("video_metric_snapshots")
    with op.batch_alter_table("storyboards") as batch_op:
        batch_op.drop_column("caption_mode")
        batch_op.drop_column("story_shape")
        batch_op.drop_column("form")
    with op.batch_alter_table("scripts") as batch_op:
        batch_op.drop_column("story_shape")
        batch_op.drop_column("form")
    with op.batch_alter_table("ideas") as batch_op:
        batch_op.drop_constraint("fk_ideas_parent_idea_id", type_="foreignkey")
        batch_op.drop_index("ix_ideas_parent_idea_id")
        batch_op.drop_column("lineage_kind")
        batch_op.drop_column("parent_idea_id")
        batch_op.drop_column("story_shape")
        batch_op.drop_column("form")
