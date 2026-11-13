"""The grace period of rendered videos that are not approved.

A render is the heavy part of a video (tens to hundreds of megabytes); its plan
is a few kilobytes in the database. A render that is still not approved a set
number of days after it was made, or after its verdict last changed, is deleted
to save space: the video file and the effects track next to it. The storyboard,
its scenes, the voice take, the props and the YouTube text all stay, so the
video can be rendered again at no AI cost. Approved videos are never touched,
and neither are their exported copies.

The worker runs `sweep` when it is idle, so it never meets a render in
progress. Settings, "Delete unapproved videos after" sets the days; 0 keeps
every render.
"""

import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from .. import jobs
from ..db import session_scope
from ..db.models import Job, Storyboard, as_utc, utcnow
from ..settings import Settings
from . import animate, compliance

log = logging.getLogger("clipper.studio")


def deadline(board: Storyboard, keep_days: int) -> datetime | None:
    """When this board's render is deleted if it is still not approved; None = never."""
    if keep_days <= 0 or not board.video_path or board.review == "approved" or board.rendered_at is None:
        return None
    rendered = as_utc(board.rendered_at)
    return max(rendered, as_utc(board.review_at) or rendered) + timedelta(days=keep_days)


def render_dir(board: Storyboard) -> Path:
    """The folder that holds only this storyboard's render (video, effects track)."""
    return animate.video_path({"script_id": board.script_id, "id": board.id, "format": board.format}).parent


def _size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0


def _newest(folder: Path) -> datetime | None:
    times = [p.stat().st_mtime for p in folder.rglob("*") if p.is_file()]
    return datetime.fromtimestamp(max(times), tz=UTC) if times else None


def sweep(settings: Settings, now: datetime | None = None) -> list[int]:
    """Delete the renders whose grace period is over. Returns the storyboards it cleared."""
    keep = settings.unapproved_keep_days
    if keep <= 0:
        return []
    now = now or utcnow()
    with session_scope() as db:
        busy = {job.storyboard_id for job in db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE)))}
        boards = [b for b in db.scalars(select(Storyboard).where(Storyboard.review != "approved"))
                  if b.id not in busy]
        due = [b.id for b in boards if (when := deadline(b, keep)) is not None and when <= now]
        # A render left behind when its plan was redone: no longer shown anywhere, same grace period.
        stale = [render_dir(b) for b in boards if not b.video_path]
    cleared: list[int] = []
    freed = 0
    for board_id in due:
        with session_scope() as db:
            board = db.get(Storyboard, board_id)
            when = deadline(board, keep) if board is not None else None
            if when is None or when > now:          # approved or rendered again in the meantime
                continue
            folder, video = render_dir(board), Path(board.video_path)
            board.video_path, board.review, board.review_at, board.expired_at = None, "pending", None, now
            compliance.record(db, board.script_id, "expired", "app",
                              f"Deleted the render: not approved within {keep} days", storyboard_id=board.id)
        # Files go after the database agrees, so a row never points at a file that is gone.
        size = _size(folder) + (video.stat().st_size if video.is_file() and folder not in video.parents else 0)
        shutil.rmtree(folder, ignore_errors=True)
        video.unlink(missing_ok=True)
        freed += size
        cleared.append(board_id)
        log.info("unapproved render deleted",
                 extra={"storyboard_id": board_id, "keep_days": keep, "freed_mb": round(size / 1e6, 1)})
    for folder in stale:
        newest = _newest(folder) if folder.is_dir() else None
        if newest is not None and newest + timedelta(days=keep) <= now:
            freed += _size(folder)
            shutil.rmtree(folder, ignore_errors=True)
            log.info("render of a replaced plan deleted", extra={"folder": str(folder)})
    if freed:
        log.info("grace period sweep done", extra={"cleared": len(cleared), "freed_mb": round(freed / 1e6, 1)})
    return cleared
