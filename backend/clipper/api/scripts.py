"""Studio scripts: start one from an approved idea, edit it, check its facts."""

import logging
import shutil
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs, llm
from ..db import get_db
from ..db.models import Idea, Job, Script
from ..studio import compliance, hold, plain
from ..studio import script as script_mod
from ..studio import story_map as story_map_mod
from ..studio import voice as voice_mod
from . import schemas as s

router = APIRouter(tags=["studio"])
log = logging.getLogger("clipper.studio")

DEFAULT_MINUTES = {"single": 6, "list": 10}


def get_script(script_id: int, db: Session = Depends(get_db)) -> Script:
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(404, "Script not found")
    return script


def active_jobs(db: Session) -> dict[int, Job]:
    """The running or queued job of each script that has one."""
    running = db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE)).order_by(Job.id)).all()
    return {job.script_id: job for job in running if job.script_id}


def _computed(script: Script, job: Job | None) -> dict:
    words = script_mod.word_count(script.sections)
    counts = None
    if script.claims is not None:
        counts = {v: sum(c["verdict"] == v for c in script.claims)
                  for v in ("supported", "contradicted", "unclear", "joke")}
    return {
        "words": words,
        "minutes": round(words / script_mod.WORDS_PER_MINUTE, 1),
        "claim_counts": counts,
        "checked_current": script.checked_hash is not None
        and script.checked_hash == script_mod.text_hash(script.sections),
        "job": s.JobOut.model_validate(job) if job else None,
    }


def summary_out(script: Script, job: Job | None) -> s.ScriptSummary:
    fields = set(s.ScriptSummary.model_fields) - {"words", "minutes", "claim_counts", "checked_current", "job"}
    return s.ScriptSummary(**{f: getattr(script, f) for f in fields}, **_computed(script, job))


def script_out(script: Script, job: Job | None) -> s.ScriptOut:
    fields = set(s.ScriptOut.model_fields) - {"words", "minutes", "claim_counts", "checked_current", "job",
                                              "can_undo_rewrite", "plain", "hold", "story_map"}
    return s.ScriptOut(**{f: getattr(script, f) for f in fields}, **_computed(script, job),
                       can_undo_rewrite=bool(script.previous_sections),
                       plain=plain.check(script.sections or [], plain.level(), script.title),
                       hold=hold.score(script.sections or [], script.hold, script.video),
                       story_map=story_map_mod.out(script))


def _require_idle(db: Session, script: Script) -> None:
    if script.id in active_jobs(db):
        raise HTTPException(409, "The script is being worked on. Wait for the job to finish or cancel it.")


def _start(db: Session, script: Script, kind: str, stages: list[str] | None = None) -> Job:
    _require_idle(db, script)
    return jobs.enqueue(db, kind, script.title, {"script_id": script.id}, stages=stages)


@router.post("/ideas/{idea_id}/script", response_model=s.ScriptOut, status_code=202)
def start_script(idea_id: int, body: s.ScriptCreate | None = None, db: Session = Depends(get_db)):
    """Research and write a script for an idea. The idea is marked approved."""
    idea = db.get(Idea, idea_id)
    if idea is None:
        raise HTTPException(404, "Idea not found")
    idea.status = "approved"
    video = (body.video if body else None) or idea.video or "long"
    minutes = (body.target_minutes if body else None) or DEFAULT_MINUTES.get(idea.format, 6)
    seconds = (body.target_seconds if body else None) or 50
    script = Script(idea_id=idea.id, title=idea.title, theme=idea.theme, theme_id=idea.theme_id, format=idea.format,
                    target_minutes=1 if video == "short" else minutes, video=video, target_seconds=seconds,
                    sections=[])
    db.add(script)
    db.flush()
    job = _start(db, script, "script")
    log.info("script started", extra={"script_id": script.id, "idea_id": idea.id})
    return script_out(script, job)


@router.get("/scripts", response_model=list[s.ScriptSummary])
def list_scripts(db: Session = Depends(get_db)):
    running = active_jobs(db)
    return [summary_out(sc, running.get(sc.id)) for sc in db.scalars(select(Script).order_by(Script.updated_at.desc()))]


@router.get("/scripts/{script_id}", response_model=s.ScriptOut)
def read_script(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    return script_out(script, active_jobs(db).get(script.id))


@router.get("/scripts/{script_id}/text", response_class=PlainTextResponse)
def script_text(script: Script = Depends(get_script)):
    """The script as plain text, to print or read from."""
    return PlainTextResponse(script_mod.plain_text(script), headers={
        "Content-Disposition": f'attachment; filename="script-{script.id}.txt"'})


@router.patch("/scripts/{script_id}", response_model=s.ScriptOut)
def update_script(body: s.ScriptPatch, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is not None:
            setattr(script, field, value)
    db.flush()
    return script_out(script, active_jobs(db).get(script.id))


@router.put("/scripts/{script_id}/sections", response_model=s.ScriptOut)
def save_sections(body: s.ScriptSections, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Replace the script's text with your edited version. Moving back to draft if it was approved."""
    _require_idle(db, script)
    sections = [{"key": sec.key or uuid.uuid4().hex[:8], "heading": sec.heading.strip(), "text": sec.text.strip(),
                 "short": sec.short} for sec in body.sections]
    if script_mod.text_hash(sections) != script_mod.text_hash(script.sections):
        script.status = "draft"
        before, after = compliance.sections_text(script.sections), compliance.sections_text(sections)
        changed = compliance.words_changed(before, after)
        if changed:
            compliance.record(db, script.id, "script_edit", "you", f"Edited the script ({changed} words)", changed)
    script.sections = sections
    db.flush()
    return script_out(script, None)


@router.post("/scripts/{script_id}/rewrite", response_model=s.SectionRewriteOut)
def rewrite_section(body: s.SectionRewrite, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """A new version of one section, following your instruction. Not saved: you keep or discard it."""
    try:
        with llm.attribute(f"script:{script.id}"):
            text = script_mod.rewrite_section(script, body.key, body.instruction)
    except ValueError as err:
        raise HTTPException(404, str(err)) from err
    except RuntimeError as err:
        status = 409 if "story map" in str(err).lower() else 502
        raise HTTPException(status, str(err)) from err
    heading = next((x["heading"] for x in script.sections if x["key"] == body.key), body.key)
    compliance.record(db, script.id, "rewrite", "ai",
                      f"Rewrote '{heading}' on your instruction: {body.instruction.strip()[:200]}")
    return {"key": body.key, "text": text}


@router.post("/scripts/{script_id}/factcheck", response_model=s.JobOut, status_code=202)
def check_facts(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    if not script.sections:
        raise HTTPException(409, "The script has no text yet")
    return _start(db, script, "factcheck")


@router.post("/scripts/{script_id}/spoken", response_model=s.JobOut, status_code=202)
def make_spoken(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Rewrite the whole script so it sounds spoken, keeping the facts. Undo brings the old text back."""
    if not script.sections:
        raise HTTPException(409, "The script has no text yet")
    try:
        story_map_mod.prompt_value(script)
    except RuntimeError as err:
        raise HTTPException(409, str(err)) from err
    return _start(db, script, "spoken")


@router.post("/scripts/{script_id}/simpler", response_model=s.JobOut, status_code=202)
def make_simpler(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Rewrite the whole script in plainer words for the audience in Settings, keeping what is true.
    Undo brings the old text back; check the facts again afterwards."""
    if not script.sections:
        raise HTTPException(409, "The script has no text yet")
    try:
        story_map_mod.prompt_value(script)
    except RuntimeError as err:
        raise HTTPException(409, str(err)) from err
    return _start(db, script, "simpler")


@router.post("/scripts/{script_id}/hold", response_model=s.JobOut, status_code=202)
def check_hold(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Label every sentence and check how the narration holds attention. Advice only."""
    if not script.sections:
        raise HTTPException(409, "The script has no text yet")
    return _start(db, script, "hold")


@router.post("/scripts/{script_id}/story-map", response_model=s.JobOut, status_code=202)
def rebuild_story_map(body: s.StoryMapRebuild | None = None, script: Script = Depends(get_script),
                      db: Session = Depends(get_db)):
    """Build a map from the saved research. Replacing an existing map requires explicit confirmation."""
    _require_idle(db, script)
    if not script.research:
        raise HTTPException(409, "Research the script before building its story map")
    if script.story_map and not (body and body.confirm):
        raise HTTPException(409, "Rebuilding replaces the current story map and its edits. Confirm that replacement.")
    return _start(db, script, "story_map")


@router.put("/scripts/{script_id}/story-map", response_model=s.ScriptOut)
def save_story_map(body: s.StoryMapEdit, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Save a structurally valid user edit. Source numbers not in the research are refused."""
    _require_idle(db, script)
    try:
        value, _warnings = story_map_mod.user_edit(script, body.model_dump())
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    script.story_map = value
    compliance.record(db, script.id, "story_map_edit", "you", "Edited the story map")
    db.flush()
    return script_out(script, None)


@router.post("/scripts/{script_id}/undo-rewrite", response_model=s.ScriptOut)
def undo_rewrite(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Swap back to the text from before the last whole-script rewrite (and again to redo)."""
    _require_idle(db, script)
    if not script.previous_sections:
        raise HTTPException(409, "There is no earlier version to go back to")
    script.sections, script.previous_sections = script.previous_sections, script.sections
    script.status = "draft"
    compliance.record(db, script.id, "undo", "you", "Went back to the earlier version of the text")
    db.flush()
    return script_out(script, None)


@router.post("/scripts/{script_id}/redraft", response_model=s.JobOut, status_code=202)
def redraft(body: s.RedraftRequest | None = None, script: Script = Depends(get_script),
            db: Session = Depends(get_db)):
    """Write again from a current map. Legacy scripts get a map first; stale maps need explicit replacement."""
    _require_idle(db, script)
    if not script.research:
        return _start(db, script, "script")
    if not script.story_map:
        return _start(db, script, "redraft", ["story_map", "write", "factcheck", "hold"])
    current, reasons = story_map_mod.status(script)
    rebuild = bool(body and body.rebuild_story_map)
    if not current and not rebuild:
        raise HTTPException(409, "The story map is out of date: " + " ".join(reasons)
                            + " Rebuild it from research before replacing the narration.")
    if not rebuild and story_map_mod.problems(script):
        # Kept separate from staleness: a current but malformed future/legacy payload must never reach the writer.
        raise HTTPException(409, "The story map has validation problems. Rebuild it from research.")
    stages = ["story_map", "write", "factcheck", "hold"] if rebuild else None
    return _start(db, script, "redraft", stages)


@router.delete("/scripts/{script_id}", status_code=204)
def delete_script(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    _require_idle(db, script)
    script_id = script.id
    db.delete(script)
    db.flush()
    shutil.rmtree(voice_mod.script_media_dir(script_id), ignore_errors=True)
