"""Studio ideas: generate them for a theme, check them against YouTube, decide."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Idea, Script, Theme, utcnow
from ..studio import ideas as ideas_mod
from ..studio import learning
from ..studio import themes as themes_mod
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


def _with_about(theme: Theme) -> str:
    """The theme as the idea prompts get it: its name, and what it is about when you said."""
    return theme.name + (f"\n{theme.description}" if theme.description else "")


def _existing_titles(db: Session) -> list[str]:
    return list(db.scalars(select(Idea.title).order_by(Idea.id)))


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


@router.post("/generate", response_model=list[s.IdeaOut])
def generate_ideas(body: s.IdeaGenerate, db: Session = Depends(get_db)):
    """Ask the AI for new ideas on the theme. Takes up to a minute or two.

    `mode` proven builds them on the theme's best videos, approved scripts and kept ideas, and says
    which ones each idea builds on."""
    theme = _theme(db, body.theme_id, body.theme)
    started = time.monotonic()
    examples: list[dict] = []
    try:
        with llm.attribute(f"theme:{theme.id}"):
            if body.mode == "proven":
                examples = learning.chosen(learning.proven(db, theme), body.examples)
                if not examples:
                    raise HTTPException(409, "Nothing is proven in this theme yet: keep or approve a few ideas, "
                                             "or publish a video, then try again")
                drafts = ideas_mod.generate_proven(
                    theme.name, theme.description, body.count, _existing_titles(db),
                    learning.examples_text(examples), learning.lessons_text(theme),
                    learning.rejected_titles(db, theme.id), body.hint)
            else:
                drafts = ideas_mod.generate(_with_about(theme), body.count, _existing_titles(db))
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    by_number = {e["n"]: e for e in examples}
    created = []
    for draft in drafts:
        idea = Idea(theme=theme.name, theme_id=theme.id, origin="claude", status="inbox")   # waits until kept
        ideas_mod.apply_draft(idea, draft)
        if examples:
            idea.inspired_by = [{"kind": by_number[n]["kind"], "id": by_number[n]["id"],
                                 "title": by_number[n]["title"], "evidence": by_number[n]["evidence"]}
                                for n in dict.fromkeys(getattr(draft, "inspired_by", [])) if n in by_number][:3]
            if getattr(draft, "why_now", ""):
                idea.notes = f"Builds on: {draft.why_now}"
        db.add(idea)
        created.append(idea)
    if created:
        theme.updated_at = utcnow()
    db.flush()
    log.info("ideas generated", extra={"count": len(created), "duration_s": round(time.monotonic() - started, 1)})
    return [idea_out(i) for i in created]


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
        drafts = ideas_mod.generate(_with_about(theme), 1, _existing_titles(db), seed=body.text.strip())
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    if not drafts:
        raise HTTPException(502, "The AI returned no idea. Try rephrasing it.")
    idea = Idea(theme=theme.name, theme_id=theme.id, origin="you", notes=body.text.strip())
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
    if "theme_id" in changed:
        theme = _theme(db, body.theme_id)
        idea.theme_id, idea.theme = theme.id, theme.name
        changed.discard("theme_id")
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
