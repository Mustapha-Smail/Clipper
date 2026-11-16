"""Themes: the home of a channel's work, from the first idea to the published video.

Every idea and script belongs to a theme. A script's stage is read from what
exists (its status, a chosen take, its storyboards and their posts), never
stored, so it cannot drift from the work itself.

  ideas      ideas waiting for your decision (inbox) or kept for later
  script     a script being researched, written or edited
  voice      an approved script without a chosen voice take
  animation  a take is chosen; storyboards are planned, rendered or waiting for review
  ready      a video is approved and not yet out
  published  a video went out (sent by Buffer, or published by hand)
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import posting
from .. import settings as settings_mod
from ..db.models import Idea, Job, Post, Script, Storyboard, Theme, VoiceTake
from . import script as script_mod

STAGES = ("ideas", "script", "voice", "animation", "ready", "published")
STAGE_LABELS = {"ideas": "Ideas", "script": "Script", "voice": "Voice", "animation": "Animation",
                "ready": "Ready", "published": "Published"}
COLORS = ("amber", "coral", "violet", "teal", "sky", "lime", "rose", "indigo")


def by_name(db: Session, name: str) -> Theme | None:
    return db.scalar(select(Theme).where(func.lower(Theme.name) == name.strip().lower()).order_by(Theme.id))


def create(db: Session, name: str, description: str = "") -> Theme:
    count = db.scalar(select(func.count()).select_from(Theme)) or 0
    theme = Theme(name=name.strip(), description=description.strip(), color=COLORS[count % len(COLORS)],
                  channels=[])
    db.add(theme)
    db.flush()
    return theme


def resolve(db: Session, theme_id: int | None = None, name: str | None = None) -> Theme:
    """The theme asked for by id or by name (created when new); else the Studio theme in Settings."""
    if theme_id is not None:
        theme = db.get(Theme, theme_id)
        if theme is None:
            raise LookupError("Theme not found")
        return theme
    name = (name or "").strip() or settings_mod.load().studio_theme
    return by_name(db, name) or create(db, name)


def rename(db: Session, theme: Theme, name: str) -> None:
    """A new name, carried to the theme text its ideas and scripts send to the AI."""
    theme.name = name.strip()
    for model in (Idea, Script):
        for row in db.scalars(select(model).where(model.theme_id == theme.id)):
            row.theme = theme.name


def merge(db: Session, source: Theme, target: Theme) -> int:
    """Move everything of `source` into `target` and delete `source`. Returns how many rows moved."""
    moved = 0
    for model in (Idea, Script):
        for row in db.scalars(select(model).where(model.theme_id == source.id)):
            row.theme_id, row.theme = target.id, target.name
            moved += 1
    target.channels = list(dict.fromkeys([*(target.channels or []), *(source.channels or [])]))
    db.delete(source)
    db.flush()
    return moved


def _script_stage(script: Script, boards: list[Storyboard], live: dict[int, str], taken: bool) -> str:
    if any(b.published_at or live.get(b.id) == "sent" for b in boards):
        return "published"
    if any(b.review == "approved" for b in boards):
        return "ready"
    if taken or boards:
        return "animation"
    if script.status == "approved":
        return "voice"
    return "script"


def board(db: Session, theme: Theme) -> dict:
    """The theme's work by stage: what is where, and what to do next."""
    ideas = list(db.scalars(select(Idea).where(Idea.theme_id == theme.id)))
    scripts = list(db.scalars(select(Script).where(Script.theme_id == theme.id).order_by(Script.updated_at.desc())))
    script_ids = [s.id for s in scripts]
    boards: dict[int, list[Storyboard]] = {}
    taken: set[int] = set()
    chosen_takes: dict[int, VoiceTake] = {}
    live: dict[int, str] = {}
    if script_ids:
        for b in db.scalars(select(Storyboard).where(Storyboard.script_id.in_(script_ids)).order_by(Storyboard.id)):
            boards.setdefault(b.script_id, []).append(b)
        chosen_takes = {t.script_id: t for t in db.scalars(select(VoiceTake).where(
            VoiceTake.script_id.in_(script_ids), VoiceTake.chosen.is_(True)))}
        taken = set(chosen_takes)
        board_ids = [b.id for group in boards.values() for b in group]
        if board_ids:
            rank = {"sent": 3, **dict.fromkeys(posting.LIVE, 2)}   # sent wins over waiting, over cancelled
            for post in db.scalars(select(Post).where(Post.storyboard_id.in_(board_ids))):
                current = live.get(post.storyboard_id)
                if current is None or rank.get(post.status, 0) > rank.get(current, 0):
                    live[post.storyboard_id] = post.status
    with_script = {s.idea_id for s in scripts if s.idea_id}

    items: list[dict] = []
    for script in scripts:
        mine = boards.get(script.id, [])
        stage = _script_stage(script, mine, live, script.id in taken)
        items.append({
            "kind": "script", "id": script.id, "title": script.title, "stage": stage, "video": script.video,
            "status": script.status, "updated_at": script.updated_at, "idea_id": script.idea_id,
            "words": script_mod.word_count(script.sections or []), "voiced": script.id in taken,
            "take": ({"source": t.source, "voice": t.voice, "duration": t.duration}
                     if (t := chosen_takes.get(script.id)) else None),
            "storyboards": [{"id": b.id, "title": b.title, "format": b.format, "review": b.review,
                             "has_video": bool(b.video_path), "published": bool(b.published_at)
                             or live.get(b.id) == "sent", "post": live.get(b.id)} for b in mine],
        })
    waiting = [i for i in ideas if i.status in ("new", "approved") and i.id not in with_script]
    inbox = sum(1 for i in ideas if i.status == "inbox")
    counts = {stage: sum(1 for i in items if i["stage"] == stage) for stage in STAGES}
    counts["ideas"] = len(waiting)
    return {"counts": counts, "inbox": inbox, "items": items,
            "ideas": sorted(waiting, key=lambda i: (i.status != "approved", -(i.id)))}


def next_step(counts: dict, inbox: int) -> dict:
    """The one thing most worth doing in this theme, for the overview."""
    if counts["ready"]:
        return {"stage": "ready", "text": f"{counts['ready']} video{'s' if counts['ready'] != 1 else ''} "
                                          "approved: schedule or publish"}
    if counts["animation"]:
        return {"stage": "animation", "text": "Finish the animation and review the render"}
    if counts["voice"]:
        return {"stage": "voice", "text": "Record or generate the voice"}
    if counts["script"]:
        return {"stage": "script", "text": "Edit and approve the script"}
    if inbox:
        return {"stage": "ideas", "text": f"Sort {inbox} new idea{'s' if inbox != 1 else ''}"}
    if counts["ideas"]:
        return {"stage": "ideas", "text": "Pick an idea and write its script"}
    return {"stage": "ideas", "text": "Generate ideas"}


# --- deleting a theme with everything in it ---------------------------------------------

def contents(db: Session, theme: Theme) -> dict:
    """What deleting the theme would remove, for the confirmation."""
    script_ids = list(db.scalars(select(Script.id).where(Script.theme_id == theme.id)))
    boards = list(db.scalars(select(Storyboard.id).where(Storyboard.script_id.in_(script_ids)))) if script_ids else []
    takes = db.scalar(select(func.count()).select_from(VoiceTake).where(VoiceTake.script_id.in_(script_ids))) \
        if script_ids else 0
    return {"ideas": db.scalar(select(func.count()).select_from(Idea).where(Idea.theme_id == theme.id)) or 0,
            "scripts": len(script_ids), "takes": takes or 0, "videos": len(boards),
            "script_ids": script_ids, "storyboard_ids": boards}


def blockers(db: Session, theme: Theme) -> list[str]:
    """Why the theme cannot be deleted right now: work in progress, or posts still on their way."""
    from .. import jobs

    found = contents(db, theme)
    problems = []
    if found["storyboard_ids"]:
        live = db.scalar(select(func.count()).select_from(Post).where(
            Post.storyboard_id.in_(found["storyboard_ids"]), Post.status.in_(posting.LIVE)))
        if live:
            problems.append(f"{live} post{'s' if live != 1 else ''} still waiting on Buffer: undo "
                            f"{'them' if live != 1 else 'it'} first, so nothing goes out after the theme is gone")
    running = [j for j in db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE)))
               if j.script_id in found["script_ids"] or j.storyboard_id in found["storyboard_ids"]
               or (j.params or {}).get("theme_id") == theme.id]
    if running:
        problems.append("A job of this theme is queued or running: wait for it or cancel it")
    return problems


def delete_everything(db: Session, theme: Theme) -> dict:
    """Delete the theme, its ideas, scripts, voice takes, videos, posts and their files on disk.

    Copies exported to the output folder are yours and stay. Returns what was removed."""
    import shutil

    from .. import config
    from .voice import script_media_dir

    found = contents(db, theme)
    freed = 0
    if found["storyboard_ids"]:
        posts = list(db.scalars(select(Post).where(Post.storyboard_id.in_(found["storyboard_ids"]))))
        for key in {p.media_key for p in posts if p.media_key}:
            freed += posting.release_media(db, key)       # the online copy goes first, while its rows say so
        for post in posts:
            db.delete(post)
    for script in db.scalars(select(Script).where(Script.theme_id == theme.id)):
        db.delete(script)                    # takes, storyboards and the authorship log go with it
    for idea in db.scalars(select(Idea).where(Idea.theme_id == theme.id)):
        db.delete(idea)
    db.delete(theme)
    db.flush()
    for script_id in found["script_ids"]:
        shutil.rmtree(script_media_dir(script_id), ignore_errors=True)
    shutil.rmtree(config.studio_dir() / "themes" / f"theme-{theme.id}", ignore_errors=True)
    return {k: found[k] for k in ("ideas", "scripts", "takes", "videos")} | {"hosted_removed": freed}
