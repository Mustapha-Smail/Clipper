"""Studio storyboards and the prop library: plan scenes, edit them, render, review."""

import io
import logging
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import config, jobs, llm, posting
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import (
    Job,
    Script,
    Storyboard,
    StoryboardSound,
    StudioAsset,
    StudioMusic,
    StudioPhoto,
    StudioPlace,
    StudioSound,
    VoiceTake,
    utcnow,
)
from ..studio import (
    animate,
    art,
    beats,
    cartoon,
    compliance,
    continuity,
    data_graphics,
    film,
    music_pick,
    music_search,
    publish,
    retention,
    sfx,
    sound,
    youtube,
)
from ..studio import director as director_mod
from ..studio import export as export_mod
from ..studio import places as places_mod
from ..studio import script as script_mod
from ..studio import story_map as story_map_mod
from ..studio import storyboard as storyboard_mod
from ..studio import thumbnail as thumbnail_mod
from ..studio import voice as voice_mod
from . import schemas as s
from .scripts import get_script

router = APIRouter(tags=["studio"])
log = logging.getLogger("clipper.studio")


def get_board(storyboard_id: int, db: Session = Depends(get_db)) -> Storyboard:
    board = db.get(Storyboard, storyboard_id)
    if board is None:
        raise HTTPException(404, "Storyboard not found")
    return board


def _board_jobs(db: Session) -> dict[int, Job]:
    running = db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE)).order_by(Job.id)).all()
    return {job.storyboard_id: job for job in running if job.storyboard_id}


def _prop_names(board: Storyboard) -> set[str]:
    return storyboard_mod.prop_names(board.style, board.scenes)


def _keep_days() -> int:
    return settings_mod.load().unapproved_keep_days


def _computed(board: Storyboard, job: Job | None, keep_days: int) -> dict:
    return {"duration": round(board.end - board.start, 2), "scene_count": len(board.scenes),
            "has_video": bool(board.video_path), "job": s.JobOut.model_validate(job) if job else None,
            "publish_ready": publish.ready(board.youtube),
            "expires_at": retention.deadline(board, keep_days)}


SUMMARY_FIELDS = set(s.StoryboardSummary.model_fields) - {
    "duration", "scene_count", "has_video", "publish_ready", "job", "expires_at"}


def summary_out(board: Storyboard, job: Job | None, keep_days: int) -> s.StoryboardSummary:
    return s.StoryboardSummary(**{f: getattr(board, f) for f in SUMMARY_FIELDS}, **_computed(board, job, keep_days))


def export_out(board: Storyboard) -> s.ExportInfo | None:
    return s.ExportInfo(**board.export, stale=export_mod.is_stale(board)) if board.export else None


def board_out(board: Storyboard, job: Job | None, db: Session) -> s.StoryboardOut:
    names = _prop_names(board)
    drawn = set(db.scalars(select(StudioAsset.name).where(StudioAsset.name.in_(names), StudioAsset.svg.is_not(None))))
    music = board.music
    credit = (music.attribution or f"Music: {music.name}") if music and music.license else ""
    queries = film.photo_searches(board.style, board.scenes)
    found = {p.query: p for p in db.scalars(select(StudioPhoto).where(StudioPhoto.query.in_(queries)))} \
        if queries else {}
    used = [found[q] for q in queries if q in found]
    credits = publish.credits(db, board)
    score = None
    if board.style == "ink" and board.scenes and board.words:
        words = board.words
        score = beats.score(board.scenes, lambda i: words[max(0, min(i, len(words) - 1))]["s"],
                            board.end - board.start)
    map_value = board.script.story_map if board.script.story_map and story_map_mod.status(board.script)[0] else None
    cards = data_graphics.stored(map_value)
    continuity_lines = continuity.check(board.style, board.scenes, board.words, board.script.sections,
                                        board.script.hold, map_value)
    settings = settings_mod.load()
    return s.StoryboardOut(**{f: getattr(board, f) for f in SUMMARY_FIELDS}, **_computed(board, job, _keep_days()),
                           export=export_out(board), words=board.words, scenes=board.scenes,
                           missing_props=sorted(names - drawn),
                           music_credit=credit, score=score, photos=used,
                           photo_searches_missing=[q for q in queries if q not in found], credits=credits,
                           youtube=board.youtube, director=director_mod.public(board.director),
                           director_undoable=director_mod.undoable(board),
                           thumbnails=thumbnail_mod.public(board.thumbnails), render_meta=board.render_meta,
                           sound_starter_default=settings.sound_starter,
                           sound_riser_default=settings.sound_riser, continuity=continuity_lines,
                           data_cards=list(cards.values()))


LOOKS = {"ink": ("ink", "paper", "blueprint", "cartoon", "clean_doodle", "rough_paint", "glow"),
         "film": ("wall", "studio"), "flat": ("ink",)}


def _look_for(style: str, look: str | None) -> str:
    """The look a storyboard of this style gets: the one asked for when the style has it, else its default."""
    allowed = LOOKS.get(style, ("ink",))
    return look if look in allowed else allowed[0]


def _idle(db: Session, board: Storyboard) -> None:
    if board.id in _board_jobs(db):
        raise HTTPException(409, "This storyboard is being worked on. Wait for the job to finish.")


def _enqueue(db: Session, board: Storyboard, kind: str) -> Job:
    _idle(db, board)
    stages = None
    if kind == "storyboard" and board.style in director_mod.KITS and settings_mod.load().director_pass:
        stages = [*jobs.STAGES["storyboard"], "direct"]       # Settings: the director looks after every plan
    return jobs.enqueue(db, kind, board.title, {"script_id": board.script_id, "storyboard_id": board.id},
                        stages=stages)


# --- storyboards ----------------------------------------------------------------------------

@router.post("/scripts/{script_id}/storyboards", response_model=s.StoryboardOut, status_code=202)
def create_storyboard(body: s.StoryboardCreate, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Plan scenes for a section (a Short) or the whole script, timed to a voice take."""
    take = db.get(VoiceTake, body.take_id) if body.take_id else next((t for t in script.takes if t.chosen), None)
    if take is None or take.script_id != script.id:
        raise HTTPException(409, "Choose a voice take for the video first (Voice, then 'Use for the video')")
    if not take.alignment:
        raise HTTPException(409, "That take has not been timed yet")
    if body.format == "short" and not body.section_key:
        raise HTTPException(400, "A Short tells one section: pick which")
    section = next((x for x in script.sections if x["key"] == body.section_key), None)
    if body.section_key and section is None:
        raise HTTPException(404, "Section not found")
    try:
        words, start, end = storyboard_mod.words_for(take, body.section_key)
    except (ValueError, FileNotFoundError) as err:
        raise HTTPException(409, f"{err}. Time the take against the current script first.") from err
    if body.format == "long" and body.section_key:
        raise HTTPException(400, "A long video tells the whole script: leave the section out")
    if body.format == "long" and take.script_hash != script_mod.text_hash(script.sections):
        raise HTTPException(409, "The script changed since this take was made. Time it against the current "
                                 "script, or make a new take, so the video matches the words.")
    title = f"{script.title} · {section['heading']}" if section else script.title
    look = _look_for(body.style, body.look)
    board = Storyboard(script_id=script.id, take_id=take.id, format=body.format, form=script.form,
                       story_shape=script.story_shape, caption_mode=body.caption_mode, style=body.style, look=look,
                       section_key=body.section_key, title=title, start=start, end=end, words=words, scenes=[])
    db.add(board)
    db.flush()
    compliance.record(db, script.id, "start", "you", f"Started a {'Short' if body.format == 'short' else 'long video'}"
                      + (f" in the {board.look} look" if body.style in ("ink", "film") else ""),
                      storyboard_id=board.id)
    job = _enqueue(db, board, "storyboard")
    log.info("storyboard queued", extra={"storyboard_id": board.id, "format": body.format})
    return board_out(board, job, db)


@router.get("/scripts/{script_id}/storyboards", response_model=list[s.StoryboardSummary])
def list_storyboards(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    running = _board_jobs(db)
    keep = _keep_days()
    return [summary_out(b, running.get(b.id), keep) for b in reversed(script.storyboards)]


@router.get("/storyboards", response_model=list[s.StoryboardListItem])
def all_storyboards(theme_id: int | None = None, db: Session = Depends(get_db)):
    """Every animated video, newest first, for review; `theme_id` keeps one theme's."""
    running = _board_jobs(db)
    query = select(Storyboard).order_by(Storyboard.id.desc())
    if theme_id is not None:
        query = query.join(Script, Storyboard.script_id == Script.id).where(Script.theme_id == theme_id)
    boards = db.scalars(query).all()
    keep = _keep_days()
    return [s.StoryboardListItem(**summary_out(b, running.get(b.id), keep).model_dump(), script_title=b.script.title)
            for b in boards]


@router.get("/storyboards/{storyboard_id}", response_model=s.StoryboardOut)
def read_storyboard(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    return board_out(board, _board_jobs(db).get(board.id), db)


@router.put("/storyboards/{storyboard_id}/scenes", response_model=s.StoryboardOut)
def save_scenes(body: s.StoryboardScenes, board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Replace the scenes with your edited version. New prop names join the library, to be drawn."""
    _idle(db, board)
    card_ids = set(data_graphics.stored(board.script.story_map))
    if board.style == "ink":
        shots, dropped = beats.clean(body.scenes, len(board.words), card_ids)
        if dropped:
            raise HTTPException(400, f"{len(dropped)} line{'s' if len(dropped) > 1 else ''} could not be used: "
                                     + "; ".join(dropped[:3]))
        changed = compliance.lines_changed(board.scenes or [], shots)
        if changed:
            compliance.record(db, board.script_id, "beats_edit", "you",
                              f"Edited the scenes ({changed} line{'s' if changed != 1 else ''})", changed, board.id)
        board.scenes = shots
        for name in beats.place_names(shots):
            if name not in cartoon.PLACES and db.get(StudioPlace, name) is None:
                db.add(StudioPlace(name=name, description=name.replace("_", " ")))
    elif board.style == "film":
        shots, dropped = film.clean(body.scenes, len(board.words), card_ids)
        if dropped:
            raise HTTPException(400, f"{len(dropped)} line{'s' if len(dropped) > 1 else ''} could not be used: "
                                     + "; ".join(dropped[:3]))
        changed = compliance.lines_changed(board.scenes or [], shots)
        if changed:
            compliance.record(db, board.script_id, "beats_edit", "you",
                              f"Edited the scenes ({changed} line{'s' if changed != 1 else ''})", changed, board.id)
        board.scenes = shots
    else:
        try:
            scenes = []
            for raw in body.scenes:
                item = storyboard_mod.Scene.model_validate(raw).model_dump(exclude_none=True)
                # Keep missing composition/lane fields absent for old flat plans;
                # the cleaner emits canonical split contracts only when requested.
                if "composition" not in raw:
                    item.pop("composition", None)
                scenes.append(item)
        except ValidationError as err:
            first = err.errors()[0]
            raise HTTPException(400, f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from err
        board.scenes = storyboard_mod.clean(scenes, len(board.words), card_ids)
    for name in _prop_names(board):
        if db.get(StudioAsset, name) is None:
            db.add(StudioAsset(name=name, description=name.replace("_", " ")))
    db.flush()
    return board_out(board, None, db)


@router.post("/storyboards/{storyboard_id}/replan", response_model=s.JobOut, status_code=202)
def replan(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Plan the scenes again from scratch. Your edits are replaced."""
    return _enqueue(db, board, "storyboard")


@router.post("/storyboards/{storyboard_id}/direct", response_model=s.JobOut, status_code=202)
def direct(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """The AI director looks at stills of every shot and rewrites the weak ones. Undo puts the plan back."""
    if board.style not in director_mod.KITS:
        raise HTTPException(409, "The director works on ink and film storyboards only")
    if not board.scenes:
        raise HTTPException(409, "The storyboard has no shots yet")
    return _enqueue(db, board, "direct")


@router.post("/storyboards/{storyboard_id}/director/undo", response_model=s.StoryboardOut)
def undo_director(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Put back the plan from before the director's last pass. Refused once the plan was edited since."""
    _idle(db, board)
    if not director_mod.undo(board):
        raise HTTPException(409, "Nothing to undo: the director changed nothing, or the shots were edited since")
    compliance.record(db, board.script_id, "beats_edit", "you", "Undid the AI director's changes",
                      storyboard_id=board.id)
    db.flush()
    return board_out(board, None, db)


@router.post("/storyboards/{storyboard_id}/render", response_model=s.JobOut, status_code=202)
def render(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    if not board.scenes:
        raise HTTPException(409, "The storyboard has no scenes yet")
    return _enqueue(db, board, "animate")


@router.get("/storyboards/{storyboard_id}/still")
def still(t: float = Query(0.0, ge=0), width: int = Query(540, ge=120, le=1080),
          board: Storyboard = Depends(get_board)):
    """One frame, for previews in the editor."""
    png = animate.still(board.id, t, settings_mod.load(), width)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/storyboards/{storyboard_id}/video")
def video(board: Storyboard = Depends(get_board)):
    if not board.video_path:
        raise HTTPException(404, "Not rendered yet")
    return FileResponse(board.video_path, media_type="video/mp4",
                        filename=f"{board.format}-{board.script_id}-{board.id}.mp4")


@router.patch("/storyboards/{storyboard_id}", response_model=s.StoryboardOut)
def update_storyboard(body: s.StoryboardPatch, board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Your verdict on the rendered video (approving posts nothing by itself), and the sound settings."""
    if body.review is not None:
        if not board.video_path:
            raise HTTPException(409, "Render the video before reviewing it")
        if body.review != "approved" and board.published_at:
            raise HTTPException(409, "This long video is marked as published. Move it back to Approved first.")
        if body.review != "approved" and posting.has_live(db, "storyboard", board.id):
            raise HTTPException(409, "A post of this video is waiting on Buffer. "
                                     "Undo it (Animations, Published) before taking back the approval.")
        if body.review != board.review:
            compliance.record(db, board.script_id, "review", "you",
                              {"approved": "Watched and approved the video",
                               "rejected": "Watched and rejected the video",
                               "pending": "Took back the review"}[body.review], storyboard_id=board.id)
            board.review_at = utcnow()      # an unapproved render gets a full grace period from here
        board.review = body.review
    if body.published is not None:
        if board.format != "long":
            raise HTTPException(409, "Only long videos are published manually; schedule Shorts from the Approved tab")
        if body.published:
            if not board.video_path or board.review != "approved":
                raise HTTPException(409, "Render and approve the long video before marking it published")
            if not publish.ready(board.youtube):
                raise HTTPException(409, "Finish and save the YouTube title and description first")
            if not board.published_at:
                board.published_at = utcnow()
                compliance.record(db, board.script_id, "publish", "you", "Marked the long video as published manually",
                                  storyboard_id=board.id)
        elif board.published_at:
            board.published_at = None
            compliance.record(db, board.script_id, "publish", "you", "Moved the long video back to Approved",
                              storyboard_id=board.id)
    if body.published_url is not None:
        url = body.published_url.strip()
        if url and not youtube.video_id(url):
            raise HTTPException(400, "Paste the video's YouTube link (watch, youtu.be or shorts)")
        if url != (board.published_url or ""):
            board.published_url, board.stats = url or None, None
            if url and config.youtube_api_key():
                try:
                    found = youtube.stats([youtube.video_id(url)])
                except youtube.YouTubeError as err:
                    log.info("statistics not read", extra={"error": str(err)[:200]})
                else:
                    if found:
                        board.stats = {**next(iter(found.values())), "at": utcnow().isoformat(),
                                       "source": "youtube"}
    if body.look is not None and body.look != board.look:
        if body.look not in LOOKS.get(board.style, ()) or board.style == "flat":
            raise HTTPException(400, f"The {board.style} style has no {body.look} look")
        _idle(db, board)
        compliance.record(db, board.script_id, "look", "you", f"Chose the {body.look} look", storyboard_id=board.id)
        board.look = body.look
    if "music_id" in body.model_fields_set:
        if body.music_id is not None and db.get(StudioMusic, body.music_id) is None:
            raise HTTPException(404, "No such music track")
        if body.music_id != board.music_id:
            track = db.get(StudioMusic, body.music_id) if body.music_id else None
            by = "ai" if body.music_suggested else "you"
            what = f"'{track.name}'" if track else "no music"
            compliance.record(db, board.script_id, "music", by,
                              f"{'Took the suggested' if by == 'ai' else 'Chose'} music: {what}",
                              storyboard_id=board.id)
        board.music_id = body.music_id
    if body.music_db is not None:
        board.music_db = body.music_db
    if body.effects is not None:
        board.effects = body.effects
    for field, label in (("starter_mode", "opening attention sound"), ("riser_mode", "twist/payoff risers")):
        value = getattr(body, field)
        if value is not None and value != getattr(board, field):
            _idle(db, board)
            inherited = getattr(settings_mod.load(), "sound_starter" if field == "starter_mode" else "sound_riser")
            summary = f"Set {label} to {value}"
            if value == "default":
                summary += f" (currently {'on' if inherited else 'off'} in Settings)"
            compliance.record(db, board.script_id, "sound", "you", summary, storyboard_id=board.id)
            setattr(board, field, value)
    if body.caption_mode is not None and body.caption_mode != board.caption_mode:
        _idle(db, board)
        board.caption_mode = body.caption_mode
        compliance.record(db, board.script_id, "captions", "you",
                          f"Set burned captions to {body.caption_mode}", storyboard_id=board.id)
    db.flush()
    return board_out(board, _board_jobs(db).get(board.id), db)


def _measure(db: Session, tracks: list[StudioMusic]) -> None:
    """Measure tracks that were added before measuring existed."""
    for track in tracks:
        if track.features is None and Path(track.path).is_file():
            track.features = sound.describe_track(Path(track.path))
    db.flush()


@router.post("/storyboards/{storyboard_id}/suggest-music", response_model=s.MusicSuggestion)
def suggest_music(body: s.SuggestMusic | None = None, board: Storyboard = Depends(get_board),
                  db: Session = Depends(get_db)):
    """Which track suits this video, from your library and (by default) online, or none at all.

    Two small calls on the quick tier: search words, then the ranking. Online
    candidates are measured by the app itself from a stretch of their audio.
    """
    online = body.online if body else True
    tracks = db.scalars(select(StudioMusic).order_by(StudioMusic.name)).all()
    _measure(db, tracks)
    narration = " ".join(w["w"] for w in board.words[:90])
    chapters = [sec["heading"] for sec in board.script.sections] if board.format == "long" else []
    args = (board.title, board.format, board.end - board.start, narration, chapters)
    searches, found = [], []
    try:
        if online:
            try:
                with llm.attribute(f"storyboard:{board.id}"):
                    searches = music_pick.searches(*args)
            except RuntimeError:
                searches = []    # the fallback genres still find something
            found = music_search.candidates(searches, {t.source_url for t in tracks if t.source_url})
        if not tracks and not found:
            return {"choice": None, "level_db": -18, "searches": searches, "ranking": [],
                    "reason": "No music to choose from: the library is empty and nothing was found online."}
        with llm.attribute(f"storyboard:{board.id}"):
            pick = music_pick.suggest(*args, tracks, found)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err

    details = {f"lib-{t.id}": {"source": "library", "track_id": t.id, "openverse_id": None, "title": t.name,
                               "creator": t.creator, "license": t.license, "duration": t.duration,
                               "words": (t.features or {}).get("words", []), "landing_url": t.source_url}
               for t in tracks}
    details.update({f"web-{i['id'][:8]}": {"source": "online", "track_id": None, "openverse_id": i["id"],
                                          "title": i["title"], "creator": i["creator"], "license": i["license"],
                                          "duration": i["duration"],
                                          "words": (i.get("features") or {}).get("words", []),
                                          "landing_url": i["landing_url"]}
                    for i in found})
    ranking = [{"ref": r.ref, "fit": max(0, min(10, r.fit)), "reason": r.reason, **details[r.ref]}
               for r in pick.ranking if r.ref in details]
    return {"choice": pick.choice if pick.choice in details else None, "reason": pick.reason,
            "level_db": max(-34, min(-10, pick.level_db)), "searches": searches, "ranking": ranking[:8]}


@router.get("/storyboards/{storyboard_id}/sound-preview")
def sound_preview(music_id: int | None = None, openverse_id: str | None = None,
                  music_db: float = Query(-18, ge=-40, le=-6), effects: bool = True,
                  start: float = Query(0, ge=0), seconds: float = Query(25, ge=5, le=60),
                  board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """The voice, effects and a music track mixed for a stretch of the video, without rendering any frames."""
    take = board.take
    if take is None or not take.path or not Path(take.path).is_file():
        raise HTTPException(409, "The voice take of this storyboard is missing")
    track = db.get(StudioMusic, music_id) if music_id else None
    music = track.path if track else None
    if openverse_id and not music:   # an online track, heard before adding it: streamed from its source
        try:
            music = music_search.track(openverse_id)["audio_url"]
        except (ValueError, RuntimeError) as err:
            raise HTTPException(400, str(err)) from err
    try:
        audio = animate.preview_mix(board, take.path, music, music_db, effects, start, seconds)
    except RuntimeError as err:
        raise HTTPException(500, str(err)) from err
    return Response(audio, media_type="audio/mp4", headers={"Cache-Control": "no-store"})


@router.get("/storyboards/{storyboard_id}/subtitles.srt", response_class=Response)
def subtitles(board: Storyboard = Depends(get_board)):
    """Subtitles for YouTube, from the script's own words with the take's timing."""
    name = f"{board.format}-{board.script_id}-{board.id}.srt"
    return Response(sound.srt(board.words), media_type="application/x-subrip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/storyboards/{storyboard_id}/chapters", response_model=s.Chapters)
def chapters(board: Storyboard = Depends(get_board)):
    """Chapter timestamps to paste in the YouTube description."""
    return sound.chapters(board.words, {sec["key"]: sec["heading"] for sec in board.script.sections})


@router.delete("/storyboards/{storyboard_id}", status_code=204)
def delete_storyboard(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    _idle(db, board)
    folder = animate.video_path({"script_id": board.script_id, "id": board.id, "format": board.format}).parent
    thumbs = thumbnail_mod.folder(board)
    db.delete(board)
    db.flush()
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(thumbs, ignore_errors=True)


# --- archive photos ---------------------------------------------------------------------------

@router.get("/photos/{photo_id}/image")
def photo_image(photo_id: int, size: int = Query(320, ge=64, le=1200), db: Session = Depends(get_db)):
    photo = db.get(StudioPhoto, photo_id)
    if photo is None or not Path(photo.path).is_file():
        raise HTTPException(404, "Picture not found")

    image = Image.open(photo.path)
    image.thumbnail((size, size))
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=85)
    return Response(out.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.post("/photos/{photo_id}/next", response_model=s.PhotoOut)
def next_photo(photo_id: int, storyboard_id: int | None = None, db: Session = Depends(get_db)):
    """Use the next search result instead, in every video that asks for this search. Render again to see it."""
    from ..studio import photos as photos_mod

    photo = db.get(StudioPhoto, photo_id)
    if photo is None:
        raise HTTPException(404, "Picture not found")
    query, rank = photo.query, photo.rank
    board = db.get(Storyboard, storyboard_id) if storyboard_id else None
    if board is not None:
        compliance.record(db, board.script_id, "photo", "you", f"Asked for another picture for '{query}'",
                          storyboard_id=board.id)
    db.commit()       # release the row: find() writes it in its own session
    try:
        found = photos_mod.find(query, rank + 1) or photos_mod.find(query, 0)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    if found is None:
        raise HTTPException(404, "No other usable picture for this search: change its words in the beats")
    return found


# --- before you publish ----------------------------------------------------------------------

@router.get("/storyboards/{storyboard_id}/publish-check", response_model=s.PublishCheck)
def publish_check(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Advice before uploading: your input on record, likeness to recent videos, kids and AI labels. Advises only."""
    return compliance.before_publish(db, board)


@router.get("/storyboards/{storyboard_id}/authorship.txt", response_class=PlainTextResponse)
def authorship_log(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Who did what, as text to keep with the video."""
    return PlainTextResponse(compliance.authorship_text(db, board), headers={
        "Content-Disposition": f'attachment; filename="authorship-{board.id}.txt"'})


@router.post("/storyboards/{storyboard_id}/youtube", response_model=s.YoutubeText)
def write_youtube_text(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Draft the title, description and tags for an approved video. Replaces your edits. About a cent."""
    if not board.video_path or board.review != "approved":
        raise HTTPException(409, "Render the video and approve it first")
    try:
        with llm.attribute(f"storyboard:{board.id}"):
            board.youtube = publish.draft(db, board)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    compliance.record(db, board.script_id, "description", "ai", "Drafted the YouTube title and description",
                      storyboard_id=board.id)
    db.flush()
    return board.youtube


@router.put("/storyboards/{storyboard_id}/youtube", response_model=s.YoutubeText)
def save_youtube_text(body: s.YoutubeEdit, board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Your edited title, description and tags."""
    before = board.youtube or {}
    board.youtube = publish.edit(before, body.title, body.description, body.tags)
    changed = compliance.words_changed(f"{before.get('title', '')} {before.get('description', '')}",
                                       f"{board.youtube['title']} {board.youtube['description']}")
    if changed:
        compliance.record(db, board.script_id, "description_edit", "you",
                          f"Edited the YouTube title and description ({changed} words)", changed, board.id)
    db.flush()
    return board.youtube


@router.post("/storyboards/{storyboard_id}/export", response_model=s.ExportInfo)
def export_video(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Copy the approved video, its subtitles, YouTube text and authorship log to the output folder.

    A copy for you to upload or sync. Nothing is posted.
    """
    try:
        export_mod.run(db, board, settings_mod.load())
    except export_mod.ExportError as err:
        raise HTTPException(409, str(err)) from err
    compliance.record(db, board.script_id, "export", "you", "Exported the approved video to the output folder",
                      storyboard_id=board.id)
    db.flush()
    return export_out(board)


# --- thumbnails (long videos, YouTube's A/B test) ---------------------------------------------

@router.post("/storyboards/{storyboard_id}/thumbnails", response_model=s.JobOut, status_code=202)
def make_thumbnails(body: s.ThumbnailsMake | None = None, board: Storyboard = Depends(get_board),
                    db: Session = Depends(get_db)):
    """Plan and draw three thumbnails (or plan one again). A job: a few cents of AI, a few seconds of drawing."""
    body = body or s.ThumbnailsMake()
    if board.format != "long":
        raise HTTPException(409, "Thumbnails are for long videos: a Short's cover is chosen in the YouTube app")
    if not board.scenes or not board.words:
        raise HTTPException(409, "Plan the storyboard first")
    if body.only and not board.thumbnails:
        raise HTTPException(409, "Make the three thumbnails first")
    _idle(db, board)
    params = {"script_id": board.script_id, "storyboard_id": board.id}
    if body.only:
        params |= {"only": body.only, "note": body.note.strip()}
    return jobs.enqueue(db, "thumbnails", board.title, params)


@router.put("/storyboards/{storyboard_id}/thumbnails/{key}", response_model=s.StoryboardOut)
def edit_thumbnail(key: str, body: s.ThumbnailEdit, board: Storyboard = Depends(get_board),
                   db: Session = Depends(get_db)):
    """Your change to one thumbnail: drawn again at once, checked again, no AI."""
    if key not in thumbnail_mod.KEYS or not board.thumbnails:
        raise HTTPException(404, "No such thumbnail")
    _idle(db, board)
    board_id, script_id = board.id, board.script_id
    db.commit()           # release the row: the drawing writes it in its own session
    try:
        thumbnail_mod.edit(board_id, key, body.model_dump(exclude_none=True), settings_mod.load())
    except KeyError as err:
        raise HTTPException(404, "No such thumbnail") from err
    compliance.record(db, script_id, "thumbnail_edit", "you", f"Edited thumbnail {key}", storyboard_id=board_id)
    db.flush()
    db.expire_all()
    board = db.get(Storyboard, board_id)
    return board_out(board, _board_jobs(db).get(board_id), db)


@router.patch("/storyboards/{storyboard_id}/thumbnails/result", response_model=s.StoryboardOut)
@router.post("/storyboards/{storyboard_id}/thumbnails/result", response_model=s.StoryboardOut)
def thumbnail_result(body: s.ThumbnailResult, board: Storyboard = Depends(get_board),
                     db: Session = Depends(get_db)):
    """Save the creator's selected thumbnail and/or manually observed test winner."""
    if not board.thumbnails:
        raise HTTPException(409, "Make the three thumbnails first")
    _idle(db, board)
    board_id, script_id = board.id, board.script_id
    db.commit()
    try:
        thumbnail_mod.record_result(board_id, **body.model_dump(exclude_unset=True))
    except KeyError as err:
        raise HTTPException(400, f"Unknown thumbnail: {err.args[0]}") from err
    compliance.record(db, script_id, "thumbnail_result", "you", "Recorded thumbnail selection/test result",
                      storyboard_id=board_id)
    db.flush()
    db.expire_all()
    return board_out(db.get(Storyboard, board_id), _board_jobs(db).get(board_id), db)


@router.get("/storyboards/{storyboard_id}/thumbnails/{key}.jpg")
def thumbnail_image(key: str, board: Storyboard = Depends(get_board)):
    path = thumbnail_mod.path_of(board, key)
    if path is None or not path.is_file():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg", filename=f"thumbnail-{key}.jpg",
                        content_disposition_type="inline", headers={"Cache-Control": "max-age=86400"})


# --- the library ------------------------------------------------------------------------------

def _usage(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for style, scenes in db.execute(select(Storyboard.style, Storyboard.scenes)):
        for name in storyboard_mod.prop_names(style, scenes):
            counts[name] = counts.get(name, 0) + 1
    return counts


def asset_out(asset: StudioAsset, used: int) -> s.AssetOut:
    return s.AssetOut(name=asset.name, description=asset.description, drawn=asset.svg is not None,
                      origin=asset.origin, created_at=asset.created_at, updated_at=asset.updated_at,
                      used_in=used)


@router.get("/assets", response_model=list[s.AssetOut])
def list_assets(db: Session = Depends(get_db)):
    used = _usage(db)
    return [asset_out(a, used.get(a.name, 0)) for a in db.scalars(select(StudioAsset).order_by(StudioAsset.name))]


@router.get("/assets/cast.png")
def cast_image(variant: str = "narrator", pose: str = "stand", expression: str = "neutral",
               height: int = Query(260, ge=60, le=900)):
    """A cast member, for the editor's pickers."""
    svg = art.character_svg(variant, pose, expression)
    out = io.BytesIO()
    art.rasterize(svg, height).save(out, format="PNG")
    return Response(out.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@router.get("/assets/{name}/image")
def asset_image(name: str, size: int = Query(256, ge=32, le=1024), db: Session = Depends(get_db)):
    asset = db.get(StudioAsset, name)
    if asset is None:
        raise HTTPException(404, "No such prop")
    out = io.BytesIO()
    art.rasterize(asset.svg or art.placeholder_svg(name), size).save(out, format="PNG")
    return Response(out.getvalue(), media_type="image/png", headers={"Cache-Control": "no-cache"})


# --- places (the cartoon look's backgrounds) ------------------------------------------------------

def _place_usage(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for style, scenes in db.execute(select(Storyboard.style, Storyboard.scenes)):
        if style == "ink":
            for name in beats.place_names(scenes or []):
                counts[name] = counts.get(name, 0) + 1
    return counts


def _place_out(name: str, row: StudioPlace | None, used: dict[str, int]) -> s.PlaceOut:
    """A place for the library. A code place without a row is drawn by code; with one, by Claude."""
    if row is None:
        return s.PlaceOut(name=name, description=cartoon.BUILT_IN[name], built_in=True, wide=True, tall=True,
                          origin="code", used_in=used.get(name, 0), by_code=True)
    return s.PlaceOut(name=row.name, description=row.description, built_in=name in cartoon.PLACES,
                      wide=row.svg is not None, tall=row.svg_tall is not None, origin=row.origin,
                      updated_at=row.updated_at, used_in=used.get(name, 0))


@router.get("/places", response_model=list[s.PlaceOut])
def list_places(db: Session = Depends(get_db)):
    """The places of the cartoon look: the six that come with the app, then those Claude drew."""
    used = _place_usage(db)
    rows = {p.name: p for p in db.scalars(select(StudioPlace).order_by(StudioPlace.name))}
    out = [_place_out(n, rows.get(n), used) for n in cartoon.BUILT_IN]
    return out + [_place_out(n, p, used) for n, p in rows.items() if n not in cartoon.PLACES]


@router.get("/places/{name}/image")
def place_image(name: str, shape: str = Query("wide", pattern="^(wide|tall)$"), size: int = Query(480, ge=96, le=1920),
                db: Session = Depends(get_db)):
    """What the camera sees of the place when it rests in the middle, as PNG."""
    tall = shape == "tall"
    row = db.get(StudioPlace, name)
    if row is None and name not in cartoon.PLACES:
        raise HTTPException(404, "No such place")
    svg = places_mod.stored(row, tall) if row else None
    if row is not None and svg is None:
        raise HTTPException(404, "Not drawn yet: it is drawn when a cartoon video first needs it")
    return Response(cartoon.preview(name, tall, size, svg), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/places/{name}/redraw", response_model=s.PlaceOut)
def redraw_place(name: str, body: s.PlaceRedraw, db: Session = Depends(get_db)):
    """Have Claude draw the place (again), wide or tall, optionally with your instruction. About 30 seconds.
    A place drawn by code becomes one Claude draws, like any other, until you bring the code drawing back."""
    place = db.get(StudioPlace, name)
    if place is None:
        if name not in cartoon.PLACES:
            raise HTTPException(404, "No such place")
        place = StudioPlace(name=name, description=cartoon.BUILT_IN[name])
    if body.description is not None:
        place.description = body.description.strip()
    tall = body.shape == "tall"
    try:
        with llm.attribute(None):
            svg = places_mod.draw(name, place.description, tall, body.instruction.strip())
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    if tall:
        place.svg_tall = svg
    else:
        place.svg = svg
    place.origin, place.updated_at = "claude", utcnow()
    db.add(place)
    db.flush()
    return _place_out(name, place, _place_usage(db))


@router.delete("/places/{name}", status_code=204)
def delete_place(name: str, db: Session = Depends(get_db)):
    """Forget a drawn place. Videos that name it show the plain colour background until it is drawn again.
    A place drawn by code loses its code drawing instead: Claude draws it when a video needs it, like any
    other. Deleting it again forgets Claude's drawings and brings the code drawing back."""
    place = db.get(StudioPlace, name)
    if place is not None:
        db.delete(place)
    elif name in cartoon.PLACES:
        db.add(StudioPlace(name=name, description=cartoon.BUILT_IN[name]))
    else:
        raise HTTPException(404, "No such place")


@router.post("/assets/{name}/redraw", response_model=s.AssetOut)
def redraw(name: str, body: s.AssetRedraw, db: Session = Depends(get_db)):
    """Have Claude draw the prop (again), optionally with your instruction. About 20 seconds."""
    asset = db.get(StudioAsset, name)
    if asset is None:
        raise HTTPException(404, "No such prop")
    if body.description is not None:
        asset.description = body.description.strip()
    try:
        asset.svg = storyboard_mod.draw_prop(name, asset.description, body.instruction.strip())
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    asset.origin, asset.updated_at = "ai", utcnow()
    db.flush()
    return asset_out(asset, _usage(db).get(name, 0))


@router.delete("/assets/{name}", status_code=204)
def delete_asset(name: str, db: Session = Depends(get_db)):
    """Remove a prop. Storyboards that use it show a placeholder until it is drawn again."""
    asset = db.get(StudioAsset, name)
    if asset is None:
        raise HTTPException(404, "No such prop")
    db.delete(asset)


# --- music ------------------------------------------------------------------------------------

MUSIC_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


@router.get("/music", response_model=list[s.MusicOut])
def list_music(db: Session = Depends(get_db)):
    return db.scalars(select(StudioMusic).order_by(StudioMusic.name)).all()


@router.post("/music", response_model=s.MusicOut, status_code=201)
async def upload_music(file: UploadFile, db: Session = Depends(get_db)):
    """A background track. Use only music you have the rights to, such as the YouTube Audio Library."""
    suffix = Path(file.filename or "track.mp3").suffix.lower()
    if suffix not in MUSIC_SUFFIXES:
        raise HTTPException(400, "Unsupported file type. Use mp3, wav, m4a, flac or ogg.")
    track = StudioMusic(name=Path(file.filename or "track").stem[:200], path="")
    db.add(track)
    db.flush()
    folder = config.studio_dir() / "music"
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / f"{track.id}.original{suffix}"
    with original.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
    target = folder / f"{track.id}.m4a"
    try:
        sound.normalise_music(original, target)    # same loudness for every track
        track.duration = voice_mod.duration_of(target)
        track.features = sound.describe_track(target)
    except Exception as err:  # noqa: BLE001 - anything ffmpeg cannot read is not music
        original.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read that file as audio") from err
    original.unlink(missing_ok=True)
    track.path = str(target)
    db.flush()
    return track


@router.get("/music/search", response_model=s.MusicSearch)
def search_music(q: str = Query(min_length=2, max_length=200), length: str = "", page: int = Query(1, ge=1, le=50)):
    """Openverse music under licences safe for monetised videos (CC0, public domain, CC BY)."""
    try:
        return music_search.search(q, length, page)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err


@router.post("/music/import", response_model=s.MusicOut, status_code=201)
def import_music(body: s.MusicImport, db: Session = Depends(get_db)):
    """Download a track found through the search, with its credit line."""
    try:
        item = music_search.track(body.openverse_id)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    existing = db.scalar(select(StudioMusic).where(StudioMusic.source_url == item["landing_url"]))
    if existing is not None and item["landing_url"]:
        return existing
    track = StudioMusic(name=item["title"][:200], path="", creator=item["creator"], license=item["license"],
                        source_url=item["landing_url"], attribution=music_search.credit(item),
                        genres=item["genres"][:6])
    db.add(track)
    db.flush()
    folder = config.studio_dir() / "music"
    folder.mkdir(parents=True, exist_ok=True)
    original, target = folder / f"{track.id}.download", folder / f"{track.id}.m4a"
    try:
        music_search.download(item["audio_url"], original)
        sound.normalise_music(original, target)
        track.duration = voice_mod.duration_of(target)
        track.features = sound.describe_track(target)
    except Exception as err:  # noqa: BLE001 - a bad download is the source's fault, not a crash
        target.unlink(missing_ok=True)
        db.delete(track)
        raise HTTPException(502, str(err) if isinstance(err, RuntimeError) else "That track could not be read") \
            from err
    finally:
        original.unlink(missing_ok=True)
    track.path = str(target)
    db.flush()
    return track


@router.get("/music/{music_id}/audio")
def music_audio(music_id: int, db: Session = Depends(get_db)):
    track = db.get(StudioMusic, music_id)
    if track is None or not Path(track.path).is_file():
        raise HTTPException(404, "No such music track")
    return FileResponse(track.path)


@router.delete("/music/{music_id}", status_code=204)
def delete_music(music_id: int, db: Session = Depends(get_db)):
    """Storyboards that used it render without music."""
    track = db.get(StudioMusic, music_id)
    if track is None:
        raise HTTPException(404, "No such music track")
    Path(track.path).unlink(missing_ok=True)
    db.delete(track)


# --- licensed sound effects ---------------------------------------------------------------

def _sound_view(row: StudioSound) -> dict:
    value = {name: getattr(row, name) for name in (
        "id", "kind", "name", "duration", "gain", "enabled", "provider", "provider_id",
        "upstream_id", "creator", "creator_url", "landing_url", "license", "license_version", "license_url",
        "attribution", "checksum", "acquired_at", "approved_at", "rejected_at", "rejection_note")}
    return {**value, "cached": bool(row.path and Path(row.path).is_file())}


@router.get("/sounds", response_model=list[s.SoundOut])
def list_sounds(db: Session = Depends(get_db)):
    return [_sound_view(row) for row in db.scalars(select(StudioSound).order_by(StudioSound.kind, StudioSound.id))]


@router.get("/sounds/{sound_id}/audio")
def sound_audio(sound_id: int, db: Session = Depends(get_db)):
    """Play the locally cached copy without exposing its filesystem path or a remote hotlink."""
    row = db.get(StudioSound, sound_id)
    path = Path(row.path) if row is not None and row.path else None
    if path is None or not path.is_file():
        raise HTTPException(404, "The cached sound file is missing")
    return FileResponse(path, media_type="audio/wav", filename=f"sound-{row.id}.wav",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/sounds/search", response_model=s.SoundSearch)
def search_sounds(q: str = Query(min_length=2, max_length=200),
                  provider: Literal["openverse", "wikimedia"] = "openverse",
                  page: int = Query(1, ge=1, le=50)):
    try:
        if provider == "openverse":
            return sfx.search_openverse(q, page)
        if provider == "wikimedia":
            return sfx.search_commons(q)
    except (RuntimeError, ValueError) as err:
        raise HTTPException(502, str(err)) from err


@router.post("/sounds/import", response_model=s.SoundOut, status_code=201)
def import_sound(body: s.SoundImport, db: Session = Depends(get_db)):
    try:
        # Metadata is always fetched again through the provider adapter. A
        # browser cannot self-assert a licence or substitute an arbitrary URL.
        item = sfx.candidate(body.provider, body.provider_id)
        row = sfx.import_sound(db, item, body.kind)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    except (RuntimeError, OSError) as err:
        raise HTTPException(502, str(err)) from err
    return _sound_view(row)


@router.post("/sounds/{sound_id}/approve", response_model=s.SoundOut)
def approve_sound(sound_id: int, db: Session = Depends(get_db)):
    row = db.get(StudioSound, sound_id)
    if row is None:
        raise HTTPException(404, "No such sound")
    try:
        sfx.approve(row)
    except ValueError as err:
        raise HTTPException(409, str(err)) from err
    return _sound_view(row)


@router.post("/sounds/{sound_id}/reject", response_model=s.SoundOut)
def reject_sound(sound_id: int, body: s.SoundApproval | None = None, db: Session = Depends(get_db)):
    row = db.get(StudioSound, sound_id)
    if row is None:
        raise HTTPException(404, "No such sound")
    sfx.reject(row, (body or s.SoundApproval()).note)
    return _sound_view(row)


def _storyboard_sound_view(row: StoryboardSound) -> dict:
    return {"id": row.id, "storyboard_id": row.storyboard_id, "sound_id": row.sound_id, "rule": row.rule,
            "at": row.at, "gain": row.gain, "created_at": row.created_at, "sound": _sound_view(row.sound)}


@router.get("/storyboards/{storyboard_id}/sounds", response_model=list[s.StoryboardSoundOut])
def list_storyboard_sounds(board: Storyboard = Depends(get_board)):
    return [_storyboard_sound_view(row) for row in board.sound_assignments]


@router.post("/storyboards/{storyboard_id}/sounds", response_model=s.StoryboardSoundOut, status_code=201)
def assign_storyboard_sound(body: s.StoryboardSoundAssign, board: Storyboard = Depends(get_board),
                            db: Session = Depends(get_db)):
    _idle(db, board)
    row = db.get(StudioSound, body.sound_id)
    if row is None:
        raise HTTPException(404, "No such sound")
    if not sfx.usable(row):
        raise HTTPException(409, "Approve a cached local sound before assigning it")
    duration = max(0.0, board.end - board.start)
    if duration and body.at >= duration:
        raise HTTPException(422, "The sound cue must be before the end of the storyboard")
    cue = StoryboardSound(storyboard_id=board.id, sound_id=row.id, rule=body.rule.strip(), at=body.at, gain=body.gain)
    db.add(cue)
    try:
        db.flush()
    except IntegrityError as err:
        db.rollback()
        raise HTTPException(409, "A sound cue already exists at that time and rule") from err
    return _storyboard_sound_view(cue)


@router.delete("/storyboards/{storyboard_id}/sounds/{assignment_id}", status_code=204)
def delete_storyboard_sound(assignment_id: int, board: Storyboard = Depends(get_board),
                            db: Session = Depends(get_db)):
    _idle(db, board)
    cue = db.scalar(select(StoryboardSound).where(StoryboardSound.id == assignment_id,
                                                  StoryboardSound.storyboard_id == board.id))
    if cue is None:
        raise HTTPException(404, "No such sound cue")
    db.delete(cue)
