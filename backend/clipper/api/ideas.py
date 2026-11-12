"""Studio ideas: generate them for a theme, check them against YouTube, decide."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Idea, Script
from ..studio import ideas as ideas_mod
from . import schemas as s

router = APIRouter(prefix="/ideas", tags=["studio"])
log = logging.getLogger("clipper.studio")

# Editing these makes the stored check out of date.
RESEARCH_FIELDS = {"search_query"}
JUDGED_FIELDS = {"title", "hook", "answer", "angle", "search_query"}


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
    return list(db.scalars(select(Idea.title).order_by(Idea.id)))


@router.get("", response_model=list[s.IdeaOut])
def list_ideas(db: Session = Depends(get_db)):
    """Checked ideas best first, then unchecked ones newest first."""
    scripts = _latest_scripts(db)
    items = [idea_out(i, scripts.get(i.id)) for i in db.scalars(select(Idea))]
    return sorted(items, key=lambda i: (i.score is None, -(i.score or 0), -i.id))


@router.post("/generate", response_model=list[s.IdeaOut])
def generate_ideas(body: s.IdeaGenerate, db: Session = Depends(get_db)):
    """Ask Claude for new ideas on the theme. Takes up to a minute or two."""
    settings = settings_mod.load()
    theme = (body.theme or settings.studio_theme).strip()
    started = time.monotonic()
    try:
        drafts = ideas_mod.generate(theme, body.count, _existing_titles(db))
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    created = []
    for draft in drafts:
        idea = Idea(theme=theme, origin="claude", status="inbox")   # waits under Generate until kept
        ideas_mod.apply_draft(idea, draft)
        db.add(idea)
        created.append(idea)
    db.flush()
    log.info("ideas generated", extra={"count": len(created), "duration_s": round(time.monotonic() - started, 1)})
    return [idea_out(i) for i in created]


@router.post("/themes", response_model=list[s.ThemeSuggestion])
def suggest_themes(body: s.ThemeRequest | None = None, db: Session = Depends(get_db)):
    """Five channel themes when inspiration runs out, different from the ones you have. Quick tier, cheap."""
    used = list(dict.fromkeys(db.scalars(select(Idea.theme).order_by(Idea.id.desc()))))
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
    settings = settings_mod.load()
    try:
        drafts = ideas_mod.generate(settings.studio_theme, 1, _existing_titles(db),
                                    seed=body.text.strip())
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    if not drafts:
        raise HTTPException(502, "Claude returned no idea. Try rephrasing it.")
    idea = Idea(theme=settings.studio_theme, origin="you", notes=body.text.strip())
    ideas_mod.apply_draft(idea, drafts[0])
    db.add(idea)
    db.flush()
    return idea_out(idea)


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
               if getattr(body, f) is not None and getattr(body, f) != getattr(idea, f)}
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
