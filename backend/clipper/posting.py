"""Scheduling approved Shorts on Instagram and TikTok, through Buffer.

The gate is the same as everywhere: only a video you approved can be scheduled,
and it is checked twice, when you ask and again when the worker starts.
A post is one channel. Asking for several channels makes several posts that
share one uploaded copy of the video.

  request()   checks and queues a `post` job           (called by the API)
  upload()    stage 1: copy the approved file to the public bucket (hosting.py)
  schedule()  stage 2: create the posts on Buffer (buffer.py)
  sync()      ask Buffer what became of them; delete the hosted copy when no post needs it
  cancel()    delete the post on Buffer

The hosted copy is the file you approved: rendering the clip again later does
not change what was scheduled. Rejecting a video that has a post waiting is
refused until the post is cancelled (`has_live`, used by the review routes).

Buffer counts requests (1,000 per channel per 30 days on the Free plan), so the
worker never polls: it asks only for posts whose time has come, plus one full
pass a day for what was changed inside Buffer.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import buffer, hosting, jobs
from .db import session_scope
from .db.models import Clip, Job, Post, Storyboard, as_utc, utcnow
from .jobs import Reporter

SERVICES = {"instagram": "Instagram", "tiktok": "TikTok"}
TEXT_MAX = {"instagram": 2196, "tiktok": 2200}       # Buffer's limits for a caption with a video
MODES = ("queue", "at", "draft")
LIVE = ("queued", "draft", "scheduled", "sending")   # still on its way to being posted
FROM_BUFFER = {"draft": "draft", "needs_approval": "draft", "scheduled": "scheduled", "sending": "sending",
               "sent": "sent", "error": "failed"}
LEAD = timedelta(minutes=5)          # an exact time must leave room for the upload


class PostingError(RuntimeError):
    """The post cannot be made; the message says why, in words fit to show."""


def readiness() -> dict:
    """What is set up, for the UI to explain what is missing."""
    missing = ([] if buffer.configured() else ["BUFFER_API_KEY"]) + hosting.missing()
    return {"ready": not missing, "buffer_key": buffer.configured(), "hosting": hosting.configured(),
            "missing": missing, "services": list(SERVICES)}


def needs_media(post: Post) -> bool:
    """Buffer may still fetch the video: the post is on its way, or failed there and can be retried there."""
    return post.status in LIVE or (post.status == "failed" and bool(post.buffer_id))


# --- what can be posted --------------------------------------------------------------

@dataclass
class Source:
    kind: str                # clip | storyboard
    ref_id: int
    title: str
    path: Path | None
    text: str                # the caption proposed for the post
    ai: bool                 # proposed "AI-generated" label
    approved: bool
    short: bool              # vertical: what Instagram Reels and TikTok show full screen
    script_id: int | None = None


def _hashtags(text: str, most: int = 5) -> str:
    seen: list[str] = []
    for tag in re.findall(r"#\w+", text or ""):
        if tag.lower() not in (t.lower() for t in seen):
            seen.append(tag)
    return " ".join(seen[:most])


def source(db: Session, kind: str, ref_id: int) -> Source:
    if kind == "clip":
        clip = db.get(Clip, ref_id)
        if clip is None:
            raise PostingError("That clip no longer exists")
        return Source("clip", clip.id, clip.moment.title, Path(clip.path), clip.moment.caption.strip(), False,
                      clip.status == "approved", clip.format == "vertical")
    if kind == "storyboard":
        board = db.get(Storyboard, ref_id)
        if board is None:
            raise PostingError("That video no longer exists")
        written = board.youtube or {}
        title = (written.get("title") or board.title or "").strip()
        tags = _hashtags(written.get("description") or "")
        path = Path(board.video_path) if board.video_path else None
        return Source("storyboard", board.id, board.title or title, path,
                      title + (f"\n\n{tags}" if tags else ""), True,
                      board.review == "approved", board.format == "short", board.script_id)
    raise PostingError(f"Unknown kind of video: {kind}")


def check(src: Source) -> None:
    """The gate. Raises unless this is an approved Short whose file is on disk."""
    if not src.approved:
        raise PostingError("Only an approved video can be scheduled: watch it and approve it first")
    if not src.short:
        raise PostingError("Instagram and TikTok are for vertical Shorts: this video is horizontal")
    if src.path is None or not src.path.is_file():
        raise PostingError("The video file is missing from disk: render it again")


def _refs(kind: str, ref_id: int):
    return (Post.clip_id == ref_id) if kind == "clip" else (Post.storyboard_id == ref_id)


def has_live(db: Session, kind: str, ref_id: int) -> bool:
    """A post of this video is waiting to go out (so it must stay approved)."""
    waiting = select(Post.id).where(Post.kind == kind, _refs(kind, ref_id), Post.status.in_(LIVE)).limit(1)
    return db.scalar(waiting) is not None


# --- asking ---------------------------------------------------------------------------

def request(db: Session, kind: str, ref_id: int, channels: list[dict], text: str, mode: str,
            due_at: datetime | None, ai_label: bool) -> Job:
    """Check everything that can be checked without the network, then queue the job."""
    state = readiness()
    if not state["ready"]:
        raise PostingError("Scheduling is not set up yet: " + ", ".join(state["missing"]) + " missing in .env")
    src = source(db, kind, ref_id)
    check(src)
    if mode not in MODES:
        raise PostingError(f"Unknown way of scheduling: {mode}")
    if mode == "at":
        if due_at is None:
            raise PostingError("Choose a date and time")
        due_at = as_utc(due_at)
        if due_at < utcnow() + LEAD:
            raise PostingError("Choose a time at least 5 minutes from now: the video has to be uploaded first")
        if due_at > utcnow() + timedelta(days=365):
            raise PostingError("Choose a time within a year")
    else:
        due_at = None
    if not channels:
        raise PostingError("Choose at least one channel")
    text = text.strip()
    for channel in channels:
        name = f"{SERVICES.get(channel['service'], channel['service'])} ({channel['name']})"
        if channel["service"] not in SERVICES:
            raise PostingError(f"Posting to {channel['service']} is not supported yet")
        if channel.get("disconnected") or channel.get("locked"):
            raise PostingError(f"{name} is disconnected or locked in Buffer: reconnect it there first")
        if len(text.encode("utf-16-le")) // 2 > TEXT_MAX[channel["service"]]:
            raise PostingError(f"The caption is too long for {name}: {TEXT_MAX[channel['service']]} characters at most")
        waiting = db.scalar(select(Post.id).where(Post.kind == kind, _refs(kind, ref_id), Post.status.in_(LIVE),
                                                  Post.channel_id == channel["id"]).limit(1))
        if waiting is not None:
            raise PostingError(f"This video already has a post waiting on {name}: cancel it first")

    posts = [Post(kind=kind, clip_id=ref_id if kind == "clip" else None,
                  storyboard_id=ref_id if kind == "storyboard" else None, title=src.title,
                  channel_id=channel["id"], service=channel["service"], channel_name=channel["name"],
                  text=text, ai_label=ai_label, mode=mode, due_at=due_at, status="queued")
             for channel in channels]
    db.add_all(posts)
    db.flush()
    params = {"post_ids": [p.id for p in posts], f"{kind}_id": ref_id}
    return jobs.enqueue(db, "post", src.title, params)


# --- the job --------------------------------------------------------------------------

def _fail(post_ids: list[int], message: str) -> None:
    with session_scope() as db:
        for post in db.scalars(select(Post).where(Post.id.in_(post_ids), Post.status == "queued")):
            post.status, post.error = "failed", message[:1000]


def upload(post_ids: list[int], rep: Reporter) -> None:
    """Put the approved file online, once for all the posts of this job."""
    with session_scope() as db:
        waiting = db.scalars(select(Post).where(Post.id.in_(post_ids), Post.status == "queued")).all()
        if not waiting:
            rep.info("Every post of this job was cancelled: nothing to upload")
            return
        first = waiting[0]
        kind, ref_id = first.kind, first.clip_id if first.kind == "clip" else first.storyboard_id
        problem = None
        try:
            if ref_id is None:
                raise PostingError("The video was deleted before it could be scheduled")
            src = source(db, kind, ref_id)
            check(src)                       # approved when asked is not enough: approved now
            path, title = src.path, src.title
        except PostingError as err:
            problem = str(err)
    if problem:
        _fail(post_ids, problem)
        raise PostingError(problem)

    key = hosting.new_key(title)
    rep.info(f"Uploading {path.stat().st_size / 1e6:.0f} MB to the public bucket")
    try:
        url = hosting.put(path, key, progress=rep.progress)
        hosting.check_public(url)
    except hosting.HostingError as err:
        _fail(post_ids, str(err))
        try:
            hosting.delete(key)
        except hosting.HostingError:
            pass
        raise
    with session_scope() as db:
        for post in db.scalars(select(Post).where(Post.id.in_(post_ids))):
            post.media_key, post.media_url = key, url
        db.flush()
        release_media(db, key)               # all cancelled during the upload: take it down again
    rep.info("The video is online, under an address nobody can guess")


def _when(post: Post) -> str:
    if post.status == "draft":
        return "saved as a draft in Buffer: schedule it there"
    if post.due_at is None:
        return "added to the queue"
    return f"scheduled for {as_utc(post.due_at):%d %b %H:%M} UTC"


def _apply(post: Post, state: dict) -> None:
    """Copy what Buffer says about a post onto our row."""
    post.status = FROM_BUFFER.get(state["status"], post.status)
    post.checked_at = utcnow()
    for field, key in (("due_at", "due_at"), ("sent_at", "sent_at")):
        if state.get(key):
            setattr(post, field, datetime.fromisoformat(state[key].replace("Z", "+00:00")))
    post.link = state.get("link") or post.link
    post.error = state.get("error") if post.status == "failed" else None


def schedule(post_ids: list[int], rep: Reporter) -> str:
    """Create each post on Buffer. One channel failing does not stop the others."""
    done, failed = [], []
    for position, post_id in enumerate(post_ids):
        rep.progress(position / len(post_ids))
        with session_scope() as db:
            post = db.get(Post, post_id)
            if post is None or post.status != "queued" or not post.media_url:
                continue                     # cancelled since, or the upload did not happen
            ask = dict(channel_id=post.channel_id, service=post.service, text=post.text, video_url=post.media_url,
                       mode=post.mode, ai_generated=post.ai_label,
                       due_at=as_utc(post.due_at).strftime("%Y-%m-%dT%H:%M:%S.000Z") if post.due_at else None)
            name = f"{SERVICES.get(post.service, post.service)} ({post.channel_name})"
        try:
            state = buffer.create_post(**ask)
        except buffer.BufferError as err:
            failed.append(f"{name}: {err}")
            rep.warn(f"{name}: {err}")
            with session_scope() as db:
                post = db.get(Post, post_id)
                post.status, post.error = "failed", str(err)[:1000]
            continue
        with session_scope() as db:
            post = db.get(Post, post_id)
            post.buffer_id = state["id"]
            _apply(post, state)
            done.append(f"{name}: {_when(post)}")
            rep.info(done[-1][0].upper() + done[-1][1:])
    with session_scope() as db:
        for key in set(db.scalars(select(Post.media_key).where(Post.id.in_(post_ids), Post.media_key.is_not(None)))):
            release_media(db, key)
    if failed and not done:
        raise PostingError("Buffer took none of the posts. " + " ".join(failed))
    if not done:
        return "Done: nothing was left to schedule"
    return "Done, " + ("; ".join(done) if not failed else f"{len(done)} scheduled, {len(failed)} refused: see the log")


# --- afterwards -------------------------------------------------------------------------

def release_media(db: Session, key: str | None) -> bool:
    """Delete the hosted copy once no post needs it. Returns whether it was deleted."""
    if not key:
        return False
    sharing = db.scalars(select(Post).where(Post.media_key == key)).all()
    if any(needs_media(post) for post in sharing):
        return False
    try:
        hosting.delete(key)
    except hosting.HostingError:
        return False                         # still recorded on the rows: the next sync tries again
    for post in sharing:
        post.media_key = None
    return True


def sync(rep: Reporter | None = None, due_only: bool = False) -> int:
    """Ask Buffer about the posts it holds for us and record what changed. Returns how many did.

    `due_only`: only posts whose time has come (the cheap, frequent pass).
    """
    with session_scope() as db:
        query = select(Post).where(Post.buffer_id.is_not(None), Post.status.in_(("draft", "scheduled", "sending")))
        if due_only:
            query = query.where(Post.status != "draft", Post.due_at.is_not(None), Post.due_at <= utcnow())
        ids = {post.buffer_id: post.id for post in db.scalars(query)}
        leftovers = set(db.scalars(select(Post.media_key).where(Post.media_key.is_not(None))))
    changed = 0
    if ids:
        states = buffer.posts(list(ids))
        with session_scope() as db:
            for buffer_id, state in states.items():
                post = db.get(Post, ids[buffer_id])
                before = (post.status, post.due_at)
                if state is None:
                    post.status, post.error, post.checked_at = "removed", "Deleted in Buffer", utcnow()
                else:
                    _apply(post, state)
                if (post.status, post.due_at) != before:
                    changed += 1
                    if rep:
                        said = {"sent": "was posted", "failed": f"could not be posted: {post.error}",
                                "removed": "was deleted in Buffer"}.get(post.status, _when(post))
                        (rep.warn if post.status == "failed" else rep.info)(
                            f"'{post.title}' on {SERVICES.get(post.service, post.service)} {said}")
    with session_scope() as db:
        freed = sum(release_media(db, key) for key in leftovers)
    if rep:
        rep.info(f"Asked Buffer about {len(ids)} post{'s' if len(ids) != 1 else ''}: {changed} changed"
                 + (f"; removed {freed} hosted video{'s' if freed != 1 else ''} no longer needed" if freed else ""))
    return changed


def cancel(db: Session, post: Post) -> None:
    """Take a post back: delete it on Buffer, then the hosted copy if nothing else needs it."""
    if not needs_media(post):
        raise PostingError({"sent": "It was already posted: delete it on the network itself",
                            "cancelled": "It is already cancelled"}.get(post.status, "There is nothing left to cancel"))
    if post.buffer_id:
        try:
            buffer.delete_post(post.buffer_id)
        except buffer.BufferError as err:
            raise PostingError(f"Buffer did not cancel the post: {err}") from err
    post.status, post.error = "cancelled", None
    db.flush()
    release_media(db, post.media_key)
