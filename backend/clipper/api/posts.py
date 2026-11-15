"""Scheduled posts: approved Shorts handed to Buffer for Instagram and TikTok."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import buffer, jobs, posting
from ..db import get_db
from ..db.models import Post, utcnow
from ..studio import compliance
from . import schemas as s

router = APIRouter(tags=["posts"])

CHANNELS_TTL = 300          # seconds: Buffer counts requests, and channels rarely change
_channels: dict = {"at": 0.0, "when": None, "list": []}


def _all_channels(fresh: bool = False) -> dict:
    if fresh or not _channels["when"] or time.monotonic() - _channels["at"] > CHANNELS_TTL:
        try:
            found = buffer.channels()
        except buffer.BufferError as err:
            raise HTTPException(502, str(err)) from err
        _channels.update(at=time.monotonic(), when=utcnow(), list=found)
    return _channels


def post_out(post: Post) -> s.PostOut:
    out = s.PostOut.model_validate(post)
    out.hosted = bool(post.media_key)
    return out


@router.get("/posting", response_model=s.PostingStatus)
def posting_status():
    """Whether the Buffer key and the public bucket are set up."""
    return posting.readiness()


@router.get("/posting/channels", response_model=s.ChannelsOut)
def channels(fresh: bool = False):
    """Your Buffer channels a Short can go to. Asks Buffer at most every five minutes."""
    cached = _all_channels(fresh)
    usable = [c for c in cached["list"] if c["service"] in posting.SERVICES]
    others = sorted({c["service"] for c in cached["list"]} - set(posting.SERVICES))
    return {"channels": usable, "others": others, "fetched_at": cached["when"]}


@router.get("/posting/draft", response_model=s.PostDraft)
def draft(kind: str = Query(pattern="^(clip|storyboard)$"), ref_id: int = Query(ge=1), db: Session = Depends(get_db)):
    """The caption proposed for a video, and whether it may be scheduled at all."""
    try:
        src = posting.source(db, kind, ref_id)
    except posting.PostingError as err:
        raise HTTPException(404, str(err)) from err
    try:
        posting.check(src)
    except posting.PostingError as err:
        return {"text": src.text, "ai_label": src.ai, "allowed": False, "reason": str(err)}
    return {"text": src.text, "ai_label": src.ai, "allowed": True}


@router.get("/posts", response_model=list[s.PostOut])
def list_posts(clip_id: int | None = None, storyboard_id: int | None = None,
               limit: int = Query(300, ge=1, le=1000), db: Session = Depends(get_db)):
    """Posts, newest first: everything, or those of one clip or one Studio video."""
    query = select(Post).order_by(Post.id.desc()).limit(limit)
    if clip_id is not None:
        query = query.where(Post.clip_id == clip_id)
    if storyboard_id is not None:
        query = query.where(Post.storyboard_id == storyboard_id)
    return [post_out(post) for post in db.scalars(query)]


@router.post("/posts", response_model=s.JobOut, status_code=202)
def schedule(body: s.PostCreate, db: Session = Depends(get_db)):
    """Queue an approved Short for Buffer, one post per chosen channel."""
    known = {c["id"]: c for c in _all_channels()["list"]}
    missing = [cid for cid in body.channel_ids if cid not in known]
    if missing:
        raise HTTPException(400, "One of those channels is no longer connected in Buffer. Reload the channels.")
    try:
        job = posting.request(db, body.kind, body.ref_id, [known[cid] for cid in dict.fromkeys(body.channel_ids)],
                              body.text, body.mode, body.due_at, body.ai_label)
    except posting.PostingError as err:
        raise HTTPException(409, str(err)) from err
    if body.kind == "storyboard":
        src = posting.source(db, body.kind, body.ref_id)
        names = ", ".join(posting.SERVICES[known[cid]["service"]] for cid in dict.fromkeys(body.channel_ids))
        compliance.record(db, src.script_id, "schedule", "you", f"Asked Buffer to post the video on {names}",
                          storyboard_id=body.ref_id)
    return job


@router.post("/posts/sync", response_model=s.JobOut, status_code=202)
def sync(db: Session = Depends(get_db)):
    """Ask Buffer now what became of the waiting posts (the worker also does it by itself)."""
    if not buffer.configured():
        raise HTTPException(409, "BUFFER_API_KEY is not set in .env")
    return jobs.enqueue(db, "post_sync", "Scheduled posts", {})


@router.delete("/posts/{post_id}", response_model=s.PostOut)
def cancel(post_id: int, db: Session = Depends(get_db)):
    """Cancel a waiting post: it is deleted on Buffer and its hosted video is taken down."""
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(404, "Post not found")
    try:
        posting.cancel(db, post)
    except posting.PostingError as err:
        raise HTTPException(409, str(err)) from err
    return post_out(post)
