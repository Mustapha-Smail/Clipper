"""Scheduling approved videos on Instagram, TikTok and YouTube, through Buffer.

The gate is the same as everywhere: only a video you approved can be scheduled,
and it is checked twice, when you ask and again when the worker starts.
A post is one channel. Asking for several channels makes several posts that
share one uploaded copy of the video.

  request()   checks and queues a `post` job           (called by the API)
  upload()    stage 1: copy the approved file to the public bucket (hosting.py)
  schedule()  stage 2: create the posts on Buffer (buffer.py)
  sync()      ask Buffer what became of them; delete the hosted copy when no post needs it
  cancel()    take one post back: delete it on Buffer before it goes out
  undo()      take back every post of a video that has not gone out yet

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

SERVICES = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
TEXT_MAX = {"instagram": 2196, "tiktok": 2200, "youtube": 5000}
BUFFER_KEYS = {"clip": "BUFFER_CLIPPING_API_KEY", "storyboard": "BUFFER_STUDIO_API_KEY"}
YOUTUBE_CATEGORY = {"clip": "22", "storyboard": "27"}   # People & Blogs; Education
MODES = ("queue", "at", "draft")
LIVE = ("queued", "draft", "scheduled", "sending")   # still on its way to being posted
FROM_BUFFER = {"draft": "draft", "needs_approval": "draft", "scheduled": "scheduled", "sending": "sending",
               "sent": "sent", "error": "failed"}
LEAD = timedelta(minutes=5)          # an exact time must leave room for the upload


class PostingError(RuntimeError):
    """The post cannot be made; the message says why, in words fit to show."""


def readiness(kind: str | None = None) -> dict:
    """What is set up for one feature, or both features for the system status."""
    kinds = [kind] if kind in BUFFER_KEYS else list(BUFFER_KEYS)
    missing = [BUFFER_KEYS[k] for k in kinds if not buffer.configured(k)] + hosting.missing()
    return {"ready": not missing, "buffer_key": all(buffer.configured(k) for k in kinds),
            "hosting": hosting.configured(),
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
    youtube_text: str        # the longer description proposed for YouTube
    ai: bool                 # proposed "AI-generated" label
    approved: bool
    short: bool              # Buffer accepts only Shorts; clips must be vertical, Studio videos format=short
    metadata_ready: bool     # Studio Shorts must finish their Publish metadata first
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
        caption = clip.moment.caption.strip()
        return Source(kind="clip", ref_id=clip.id, title=clip.moment.title, path=Path(clip.path),
                      text=caption, youtube_text=caption, ai=False, approved=clip.status == "approved",
                      short=clip.format == "vertical", metadata_ready=True)
    if kind == "storyboard":
        board = db.get(Storyboard, ref_id)
        if board is None:
            raise PostingError("That video no longer exists")
        written = board.youtube or {}
        title = (written.get("title") or board.title or "").strip()
        tags = _hashtags(written.get("description") or "")
        caption = title + (f"\n\n{tags}" if tags else "")
        youtube_text = (written.get("description") or caption).strip()
        path = Path(board.video_path) if board.video_path else None
        return Source(kind="storyboard", ref_id=board.id, title=title, path=path, text=caption,
                      youtube_text=youtube_text, ai=True, approved=board.review == "approved",
                      short=board.format == "short", metadata_ready=publish_ready(written), script_id=board.script_id)
    raise PostingError(f"Unknown kind of video: {kind}")


def publish_ready(text: dict) -> bool:
    """The small dependency-free form of studio.publish.ready, avoiding a Studio import cycle."""
    title = (text.get("title") or "").strip()
    description = (text.get("description") or "").strip()
    return bool(title and description and len(title) <= 100 and len(description) <= 5000
                and not any(c in title + description for c in "<>") and not text.get("problems"))


def check(src: Source, services: list[str] | tuple[str, ...] = ()) -> None:
    """The gate: approved, a Short, complete Studio metadata, and a file on disk."""
    if not src.approved:
        raise PostingError("Only an approved video can be scheduled: watch it and approve it first")
    if not src.short:
        raise PostingError("Buffer accepts only Shorts. Publish long or horizontal videos manually.")
    if src.kind == "storyboard" and not src.metadata_ready:
        raise PostingError("Finish and save the YouTube title and description in the Publish tab first")
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
            due_at: datetime | None, ai_label: bool, title: str | None = None,
            youtube_text: str | None = None) -> Job:
    """Check everything that can be checked without the network, then queue the job."""
    state = readiness(kind)
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
    check(src, [channel["service"] for channel in channels])
    text = text.strip()
    title = (title or src.title).strip()
    youtube_text = (src.youtube_text if youtube_text is None else youtube_text).strip()
    for channel in channels:
        name = f"{SERVICES.get(channel['service'], channel['service'])} ({channel['name']})"
        if channel["service"] not in SERVICES:
            raise PostingError(f"Posting to {channel['service']} is not supported yet")
        if channel.get("disconnected") or channel.get("locked"):
            raise PostingError(f"{name} is disconnected or locked in Buffer: reconnect it there first")
        post_text = youtube_text if channel["service"] == "youtube" else text
        if len(post_text.encode("utf-16-le")) // 2 > TEXT_MAX[channel["service"]]:
            raise PostingError(f"The caption is too long for {name}: {TEXT_MAX[channel['service']]} characters at most")
        if channel["service"] == "youtube":
            if not title:
                raise PostingError("Choose a YouTube title")
            if len(title.encode("utf-16-le")) // 2 > 100:
                raise PostingError("The YouTube title is too long: 100 characters at most")
            if "<" in title or ">" in title:
                raise PostingError("The YouTube title cannot contain < or >")
            if "<" in youtube_text or ">" in youtube_text:
                raise PostingError("The YouTube description cannot contain < or >")
        waiting = db.scalar(select(Post.id).where(Post.kind == kind, _refs(kind, ref_id), Post.status.in_(LIVE),
                                                  Post.channel_id == channel["id"]).limit(1))
        if waiting is not None:
            raise PostingError(f"This video already has a post waiting on {name}: cancel it first")

    posts = [Post(kind=kind, clip_id=ref_id if kind == "clip" else None,
                  storyboard_id=ref_id if kind == "storyboard" else None, title=src.title,
                  channel_id=channel["id"], service=channel["service"], channel_name=channel["name"],
                  text=youtube_text if channel["service"] == "youtube" else text,
                  ai_label=ai_label, mode=mode, due_at=due_at, status="queued")
             for channel in channels]
    for post in posts:
        if post.service == "youtube":
            post.title = title
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
            check(src, [post.service for post in waiting])   # approved when asked is not enough: approved now
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
                       kind=post.kind, title=post.title, category_id=YOUTUBE_CATEGORY[post.kind],
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
            if post.status == "cancelled":
                # You undid it while Buffer was taking it: take it back there too, or it would go out.
                try:
                    buffer.delete_post(state["id"], post.kind)
                    rep.info(f"{name}: undone while it was being sent; deleted from Buffer")
                except buffer.BufferError as err:
                    post.status, post.error = "scheduled", f"Undone too late, Buffer kept it: {err}"[:1000]
                    rep.warn(f"{name}: you undid it while it was being sent, but Buffer would not delete it ({err}). "
                             "Undo it again, or delete it in Buffer.")
                continue
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
        ids_by_kind: dict[str, dict[str, int]] = {}
        for post in db.scalars(query):
            ids_by_kind.setdefault(post.kind, {})[post.buffer_id] = post.id
        leftovers = set(db.scalars(select(Post.media_key).where(Post.media_key.is_not(None))))
    changed = 0
    for kind, ids in ids_by_kind.items():
        states = buffer.posts(list(ids), kind)
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
        count = sum(len(ids) for ids in ids_by_kind.values())
        rep.info(f"Asked Buffer about {count} post{'s' if count != 1 else ''}: {changed} changed"
                 + (f"; removed {freed} hosted video{'s' if freed != 1 else ''} no longer needed" if freed else ""))
    return changed


def cancel(db: Session, post: Post) -> None:
    """Take a post back: delete it on Buffer, then the hosted copy if nothing else needs it.

    A post still being sent to Buffer (status queued) is marked cancelled at once; the job
    deletes it on Buffer if Buffer took it in the meantime (`schedule`).
    """
    if not needs_media(post):
        raise PostingError({"sent": f"It was already posted on {SERVICES.get(post.service, post.service)}: "
                                    "Buffer cannot take it back, delete it on the network itself",
                            "cancelled": "It is already undone"}.get(post.status, "There is nothing left to undo"))
    if post.buffer_id:
        try:
            buffer.delete_post(post.buffer_id, post.kind)
        except buffer.BufferError as err:
            raise PostingError(f"Buffer did not cancel the post: {err}") from err
    post.status, post.error = "cancelled", None
    db.flush()
    release_media(db, post.media_key)


def undo(db: Session, kind: str, ref_id: int) -> dict:
    """Take back every post of a video that has not gone out. Posts already out are listed, not touched.

    Returns {"undone": [Post], "failed": ["Instagram (name): why"], "sent": [Post]}.
    """
    posts = db.scalars(select(Post).where(Post.kind == kind, _refs(kind, ref_id)).order_by(Post.id)).all()
    out: dict = {"undone": [], "failed": [], "sent": [p for p in posts if p.status == "sent"]}
    for post in posts:
        if not needs_media(post):
            continue
        try:
            cancel(db, post)
            out["undone"].append(post)
        except PostingError as err:
            out["failed"].append(f"{SERVICES.get(post.service, post.service)} ({post.channel_name}): {err}")
    return out
