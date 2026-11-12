"""tag past AI calls with the video they were for

The worker runs one job at a time, so a call logged while a job was running
belonged to that job. Calls made from the API (idea checks, rewrites, music
suggestions) cannot be traced and stay untagged.

Revision ID: 5a7c3e9d2b41
Revises: 8d2f4a6c1e90
Create Date: 2026-11-12 22:40:00
"""
import sqlalchemy as sa
from alembic import op

revision = '5a7c3e9d2b41'
down_revision = '8d2f4a6c1e90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    jobs = sa.table("jobs", sa.column("id"), sa.column("params", sa.JSON), sa.column("video_id"),
                    sa.column("started_at", sa.DateTime(timezone=True)),
                    sa.column("finished_at", sa.DateTime(timezone=True)))
    usage = sa.table("ai_usage", sa.column("ref"), sa.column("created_at", sa.DateTime(timezone=True)))
    rows = bind.execute(sa.select(jobs.c.params, jobs.c.video_id, jobs.c.started_at, jobs.c.finished_at)
                        .where(jobs.c.started_at.is_not(None), jobs.c.finished_at.is_not(None))).all()
    for params, video_id, started, finished in rows:
        params = params or {}
        ref = (f"storyboard:{params['storyboard_id']}" if params.get("storyboard_id")
               else f"script:{params['script_id']}" if params.get("script_id")
               else f"video:{video_id}" if video_id else None)
        if ref:
            bind.execute(usage.update().where(usage.c.ref.is_(None), usage.c.created_at >= started,
                                              usage.c.created_at <= finished).values(ref=ref))


def downgrade() -> None:
    pass
