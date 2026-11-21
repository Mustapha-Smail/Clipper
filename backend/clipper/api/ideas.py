"""Studio ideas: generate them for a theme, check them against YouTube, decide."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs, llm
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Idea, Job, Post, Script, Storyboard, Theme, VideoMetricSnapshot
from ..studio import ideas as ideas_mod
from ..studio import themes as themes_mod
from . import schemas as s

router = APIRouter(prefix="/ideas", tags=["studio"])
log = logging.getLogger("clipper.studio")

# Editing these makes the stored check out of date.
RESEARCH_FIELDS = {"search_query"}
JUDGED_FIELDS = {"title", "hook", "expected", "answer", "angle", "search_query"}


def legacy_video(form: str) -> str:
    """The release-line adapter for clients that still use long|short video."""
    return "short" if form in ("short", "micro_short") else "long"


def get_idea(idea_id: int, db: Session = Depends(get_db)) -> Idea:
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(404, "Idea not found")
    return idea


def idea_out(idea: Idea, script_id: int | None = None) -> s.IdeaOut:
    computed = set(s.IdeaOut.model_fields) - set(Idea.__table__.columns.keys())
    return s.IdeaOut(**{f: getattr(idea, f) for f in s.IdeaOut.model_fields if f not in computed},
                     **ideas_mod.assess(idea), script_id=script_id)


def _latest_scripts(db: Session) -> dict[int, int]:
    """The newest script of each idea that has one."""
    return dict(db.execute(select(Script.idea_id, Script.id).where(Script.idea_id.is_not(None))
                           .order_by(Script.id)).all())


def _existing_titles(db: Session) -> list[str]:
    return ideas_mod.existing_titles(db)


def _theme(db: Session, theme_id: int | None, name: str | None = None) -> Theme:
    try:
        return themes_mod.resolve(db, theme_id, name)
    except LookupError as err:
        raise HTTPException(404, str(err)) from err


@router.get("", response_model=list[s.IdeaOut])
def list_ideas(theme_id: int | None = None, db: Session = Depends(get_db)):
    """Checked ideas best first, then unchecked ones newest first. `theme_id` keeps one theme's."""
    scripts = _latest_scripts(db)
    query = select(Idea) if theme_id is None else select(Idea).where(Idea.theme_id == theme_id)
    items = [idea_out(i, scripts.get(i.id)) for i in db.scalars(query)]
    return sorted(items, key=lambda i: (i.score is None, -(i.score or 0), -i.id))


@router.post("/generate", response_model=s.JobOut, status_code=202)
def generate_ideas(body: s.IdeaGenerate, db: Session = Depends(get_db)):
    """Queue a batch of ideas for the theme (the `ideas` job); they land in its inbox when it is done.

    `mode` proven builds them on the theme's best videos, approved scripts and kept ideas you ticked, and
    says which ones each idea builds on. A batch already on its way for the theme is returned instead."""
    theme = _theme(db, body.theme_id, body.theme)
    if body.mode == "proven" and not ideas_mod.proven_examples(db, theme, body.examples):
        raise HTTPException(409, "Nothing is proven in this theme yet: keep or approve a few ideas, "
                                 "or publish a video, then try again")
    for job in db.scalars(select(Job).where(Job.kind == "ideas", Job.state.in_(jobs.ACTIVE))):
        if job.theme_id == theme.id:
            return job
    what = "ideas built on what works" if body.mode == "proven" else "new ideas"
    return jobs.enqueue(db, "ideas", f"{body.count} {what} for “{theme.name}”",
                        {"theme_id": theme.id, "count": body.count, "mode": body.mode, "hint": body.hint.strip(),
                         "examples": body.examples})


@router.post("/themes", response_model=list[s.ThemeSuggestion])
def suggest_themes(body: s.ThemeRequest | None = None, db: Session = Depends(get_db)):
    """Five channel themes when inspiration runs out, different from the ones you have. Quick tier, cheap."""
    used = list(dict.fromkeys(db.scalars(select(Theme.name).order_by(Theme.id.desc()))))
    settings_theme = settings_mod.load().studio_theme
    if settings_theme and settings_theme not in used:
        used.insert(0, settings_theme)
    try:
        themes = ideas_mod.suggest_themes(used, body.hint if body else "")
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    return [t.model_dump() for t in themes]


@router.post("", response_model=s.IdeaOut)
def add_idea(body: s.IdeaCreate, db: Session = Depends(get_db)):
    """Your own idea: Claude develops it into a title, hook, beats and search."""
    theme = _theme(db, body.theme_id)
    try:
        drafts = ideas_mod.generate(ideas_mod.with_about(theme), 1, _existing_titles(db), seed=body.text.strip())
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    if not drafts:
        raise HTTPException(502, "The AI returned no idea. Try rephrasing it.")
    idea = Idea(theme=theme.name, theme_id=theme.id, origin="you", notes=body.text.strip(), form=body.form,
                video=legacy_video(body.form), story_shape=body.story_shape)
    ideas_mod.apply_draft(idea, drafts[0])
    # The creator's explicit shape is an intent, not a hint for the idea
    # writer. Preserve it even when an older prompt override returns its own
    # (or default) shape.
    if body.story_shape is not None:
        idea.story_shape = body.story_shape
    db.add(idea)
    db.flush()
    return idea_out(idea)


def _child_idea(source: Idea, *, form: str, lineage_kind: str) -> Idea:
    """Copy the creative premise only; research and judgement belong to the child run."""
    return Idea(theme=source.theme, theme_id=source.theme_id, inspired_by=source.inspired_by,
                title=source.title, hook=source.hook, expected=source.expected, answer=source.answer,
                beats=list(source.beats or []), angle=source.angle, format=source.format,
                video=legacy_video(form), form=form, story_shape=source.story_shape,
                parent_idea_id=source.id, lineage_kind=lineage_kind, search_query=source.search_query,
                origin="you", status="new", notes=source.notes, research=None, judgement=None,
                validated_at=None)


@router.post("/{idea_id}/concept-test", response_model=s.IdeaOut)
def create_concept_test(idea: Idea = Depends(get_idea), db: Session = Depends(get_db)):
    """Create a fresh, creator-triggered micro-Short child without copying research."""
    child = _child_idea(idea, form="micro_short", lineage_kind="concept_test")
    db.add(child)
    db.flush()
    return idea_out(child)


@router.post("/{idea_id}/promote", response_model=s.IdeaOut)
def promote_concept_test(idea: Idea = Depends(get_idea), db: Session = Depends(get_db)):
    """Create a fresh long-form child from a micro test; never reuse its research or script."""
    if idea.form != "micro_short":
        raise HTTPException(409, "Only a micro-Short concept test can be promoted to a long video")
    child = _child_idea(idea, form="long", lineage_kind="promotion")
    db.add(child)
    db.flush()
    return idea_out(child)


def _lineage_node(idea: Idea, scripts: dict[int, Script], boards: dict[int, list[Storyboard]],
                  posts: dict[int, list[Post]], snapshots: dict[int, list[VideoMetricSnapshot]]) -> s.LineageIdea:
    script = scripts.get(idea.id)
    storyboards = boards.get(script.id, []) if script else []
    storyboard_ids = [board.id for board in storyboards]
    evidence: list[dict] = []
    published = False
    approved = False
    for board in storyboards:
        if board.published_at or board.published_url:
            published = True
        if board.review == "approved":
            approved = True
        if board.stats:
            evidence.append({"source": "youtube", "storyboard_id": board.id, **board.stats})
        for snapshot in snapshots.get(board.id, []):
            evidence.append({"source": snapshot.service, "source_key": snapshot.source_key,
                             "storyboard_id": board.id, "observed_at": snapshot.observed_at,
                             "observed_day": snapshot.observed_day, **{
                                 field: getattr(snapshot, field) for field in (
                                     "views", "likes", "comments", "minutes_watched", "average_view_s",
                                     "average_view_pct") if getattr(snapshot, field) is not None}})
        for post in posts.get(board.id, []):
            values = (post.metrics or {}).get("values") or {}
            if values:
                evidence.append({"source": post.service, "storyboard_id": board.id, "post_id": post.id, **values})
            if post.status == "sent":
                published = True
    state = "published" if published else "approved" if approved else "in_progress" if script else "idea"
    return s.LineageIdea(id=idea.id, title=idea.title, form=idea.form, story_shape=idea.story_shape,
                         parent_idea_id=idea.parent_idea_id, lineage_kind=idea.lineage_kind, status=idea.status,
                         publication_state=state, script_id=script.id if script else None,
                         storyboard_ids=storyboard_ids, metric_evidence=evidence)


@router.get("/{idea_id}/lineage", response_model=s.IdeaLineage)
def idea_lineage(idea: Idea = Depends(get_idea), db: Session = Depends(get_db)):
    """Return the explicit premise chain and evidence without inferring causality."""
    all_ideas = list(db.scalars(select(Idea).order_by(Idea.id)).all())
    by_id = {item.id: item for item in all_ideas}
    scripts = {script.idea_id: script for script in db.scalars(
        select(Script).where(Script.idea_id.is_not(None)).order_by(Script.id)
    )}
    script_ids = [script.id for script in scripts.values()]
    storyboard_rows = list(db.scalars(select(Storyboard).where(Storyboard.script_id.in_(script_ids))).all()) \
        if script_ids else []
    boards: dict[int, list[Storyboard]] = {}
    for board in storyboard_rows:
        boards.setdefault(board.script_id, []).append(board)
    board_ids = [board.id for board in storyboard_rows]
    post_rows = list(db.scalars(select(Post).where(Post.storyboard_id.in_(board_ids))).all()) if board_ids else []
    posts: dict[int, list[Post]] = {}
    for post in post_rows:
        posts.setdefault(post.storyboard_id, []).append(post)
    snapshot_rows = list(db.scalars(
        select(VideoMetricSnapshot).where(VideoMetricSnapshot.storyboard_id.in_(board_ids))
            .order_by(VideoMetricSnapshot.observed_at)
    ).all()) if board_ids else []
    snapshots: dict[int, list[VideoMetricSnapshot]] = {}
    for snapshot in snapshot_rows:
        snapshots.setdefault(snapshot.storyboard_id, []).append(snapshot)

    chain: list[Idea] = []
    current = idea
    seen: set[int] = set()
    while current.parent_idea_id and current.parent_idea_id in by_id and current.id not in seen:
        seen.add(current.id)
        current = by_id[current.parent_idea_id]
        chain.append(current)
    root = current
    children: dict[int, list[Idea]] = {}
    for item in all_ideas:
        if item.parent_idea_id in by_id:
            children.setdefault(item.parent_idea_id, []).append(item)
    descendants: list[Idea] = []
    queue = list(children.get(idea.id, []))
    while queue:
        item = queue.pop(0)
        descendants.append(item)
        queue.extend(children.get(item.id, []))
    def nodes(item: Idea) -> s.LineageIdea:
        return _lineage_node(item, scripts, boards, posts, snapshots)
    return s.IdeaLineage(idea=nodes(idea), root=nodes(root), ancestors=[nodes(item) for item in reversed(chain)],
                         descendants=[nodes(item) for item in descendants])


@router.post("/{idea_id}/validate", response_model=s.IdeaOut)
def validate_idea(idea: Idea = Depends(get_idea), db: Session = Depends(get_db)):
    """Check what YouTube has on the topic and have Claude judge the idea. About 10 to 20 seconds."""
    try:
        with llm.attribute(f"idea:{idea.id}"):
            ideas_mod.validate(idea)
    except RuntimeError as err:   # YouTubeError is one
        raise HTTPException(502, str(err)) from err
    db.flush()
    return idea_out(idea, _latest_scripts(db).get(idea.id))


@router.patch("/{idea_id}", response_model=s.IdeaOut)
def update_idea(body: s.IdeaPatch, idea: Idea = Depends(get_idea), db: Session = Depends(get_db)):
    changed = {f for f in body.model_fields_set
               if (f == "story_shape" or getattr(body, f) is not None) and getattr(body, f) != getattr(idea, f)}
    if "theme_id" in changed:
        theme = _theme(db, body.theme_id)
        idea.theme_id, idea.theme = theme.id, theme.name
        changed.discard("theme_id")
    # `form` owns the compatibility value when both are sent. An old video-only
    # caller still gets the exact behavior it had before this release.
    if "form" in changed:
        idea.video = legacy_video(body.form)
        changed.discard("video")
    elif "video" in changed:
        idea.form = body.video
    for field in changed:
        setattr(idea, field, getattr(body, field))
    if changed & JUDGED_FIELDS:
        idea.judgement = None
        idea.validated_at = None
    if changed & RESEARCH_FIELDS:
        idea.research = None
    db.flush()
    return idea_out(idea, _latest_scripts(db).get(idea.id))


@router.delete("/{idea_id}", status_code=204)
def delete_idea(idea: Idea = Depends(get_idea), db: Session = Depends(get_db)):
    db.delete(idea)
