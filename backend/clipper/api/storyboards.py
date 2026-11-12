"""Studio storyboards and the prop library: plan scenes, edit them, render, review."""

import io
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, jobs
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Job, Script, Storyboard, StudioAsset, StudioMusic, VoiceTake, utcnow
from ..studio import animate, art, beats, music_pick, music_search, sound
from ..studio import script as script_mod
from ..studio import storyboard as storyboard_mod
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


def _computed(board: Storyboard, job: Job | None) -> dict:
    return {"duration": round(board.end - board.start, 2), "scene_count": len(board.scenes),
            "has_video": bool(board.video_path), "job": s.JobOut.model_validate(job) if job else None}


SUMMARY_FIELDS = set(s.StoryboardSummary.model_fields) - {"duration", "scene_count", "has_video", "job"}


def summary_out(board: Storyboard, job: Job | None) -> s.StoryboardSummary:
    return s.StoryboardSummary(**{f: getattr(board, f) for f in SUMMARY_FIELDS}, **_computed(board, job))


def board_out(board: Storyboard, job: Job | None, db: Session) -> s.StoryboardOut:
    names = _prop_names(board)
    drawn = set(db.scalars(select(StudioAsset.name).where(StudioAsset.name.in_(names), StudioAsset.svg.is_not(None))))
    music = board.music
    credit = (music.attribution or f"Music: {music.name}") if music and music.license else ""
    score = None
    if board.style == "ink" and board.scenes and board.words:
        words = board.words
        score = beats.score(board.scenes, lambda i: words[max(0, min(i, len(words) - 1))]["s"],
                            board.end - board.start)
    return s.StoryboardOut(**{f: getattr(board, f) for f in SUMMARY_FIELDS}, **_computed(board, job),
                           words=board.words, scenes=board.scenes, missing_props=sorted(names - drawn),
                           music_credit=credit, score=score)


def _idle(db: Session, board: Storyboard) -> None:
    if board.id in _board_jobs(db):
        raise HTTPException(409, "This storyboard is being worked on. Wait for the job to finish.")


def _enqueue(db: Session, board: Storyboard, kind: str) -> Job:
    _idle(db, board)
    return jobs.enqueue(db, kind, board.title, {"script_id": board.script_id, "storyboard_id": board.id})


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
    board = Storyboard(script_id=script.id, take_id=take.id, format=body.format, style=body.style,
                       section_key=body.section_key, title=title, start=start, end=end, words=words, scenes=[])
    db.add(board)
    db.flush()
    job = _enqueue(db, board, "storyboard")
    log.info("storyboard queued", extra={"storyboard_id": board.id, "format": body.format})
    return board_out(board, job, db)


@router.get("/scripts/{script_id}/storyboards", response_model=list[s.StoryboardSummary])
def list_storyboards(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    running = _board_jobs(db)
    return [summary_out(b, running.get(b.id)) for b in reversed(script.storyboards)]


@router.get("/storyboards", response_model=list[s.StoryboardListItem])
def all_storyboards(db: Session = Depends(get_db)):
    """Every animated video, newest first, for review across scripts."""
    running = _board_jobs(db)
    boards = db.scalars(select(Storyboard).order_by(Storyboard.id.desc())).all()
    return [s.StoryboardListItem(**summary_out(b, running.get(b.id)).model_dump(), script_title=b.script.title)
            for b in boards]


@router.get("/storyboards/{storyboard_id}", response_model=s.StoryboardOut)
def read_storyboard(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    return board_out(board, _board_jobs(db).get(board.id), db)


@router.put("/storyboards/{storyboard_id}/scenes", response_model=s.StoryboardOut)
def save_scenes(body: s.StoryboardScenes, board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Replace the scenes with your edited version. New prop names join the library, to be drawn."""
    _idle(db, board)
    if board.style == "ink":
        shots, dropped = beats.clean(body.scenes, len(board.words))
        if dropped:
            raise HTTPException(400, f"{len(dropped)} line{'s' if len(dropped) > 1 else ''} could not be used: "
                                     + "; ".join(dropped[:3]))
        board.scenes = shots
    else:
        try:
            scenes = [storyboard_mod.Scene.model_validate(scene).model_dump() for scene in body.scenes]
        except ValidationError as err:
            first = err.errors()[0]
            raise HTTPException(400, f"{'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from err
        board.scenes = storyboard_mod.clean(scenes, len(board.words))
    for name in _prop_names(board):
        if db.get(StudioAsset, name) is None:
            db.add(StudioAsset(name=name, description=name.replace("_", " ")))
    db.flush()
    return board_out(board, None, db)


@router.post("/storyboards/{storyboard_id}/replan", response_model=s.JobOut, status_code=202)
def replan(board: Storyboard = Depends(get_board), db: Session = Depends(get_db)):
    """Plan the scenes again from scratch. Your edits are replaced."""
    return _enqueue(db, board, "storyboard")


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
    """Your verdict on the rendered video (nothing is ever posted for you), and the sound settings."""
    if body.review is not None:
        if not board.video_path:
            raise HTTPException(409, "Render the video before reviewing it")
        board.review = body.review
    if "music_id" in body.model_fields_set:
        if body.music_id is not None and db.get(StudioMusic, body.music_id) is None:
            raise HTTPException(404, "No such music track")
        board.music_id = body.music_id
    if body.music_db is not None:
        board.music_db = body.music_db
    if body.effects is not None:
        board.effects = body.effects
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
                searches = music_pick.searches(*args)
            except RuntimeError:
                searches = []    # the fallback genres still find something
            found = music_search.candidates(searches, {t.source_url for t in tracks if t.source_url})
        if not tracks and not found:
            return {"choice": None, "level_db": -18, "searches": searches, "ranking": [],
                    "reason": "No music to choose from: the library is empty and nothing was found online."}
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
    db.delete(board)
    db.flush()
    shutil.rmtree(folder, ignore_errors=True)


# --- the library ------------------------------------------------------------------------------

def _usage(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for style, scenes in db.execute(select(Storyboard.style, Storyboard.scenes)):
        for name in storyboard_mod.prop_names(style, scenes):
            counts[name] = counts.get(name, 0) + 1
    return counts


def asset_out(asset: StudioAsset, used: int) -> s.AssetOut:
    return s.AssetOut(name=asset.name, description=asset.description, drawn=asset.svg is not None,
                      origin=asset.origin, updated_at=asset.updated_at, used_in=used)


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
