"""Studio voice: AI voices and your recordings for a script, each timed against it."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, jobs
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Job, Script, VoiceTake
from ..studio import chatterbox as chatterbox_mod
from ..studio import compliance
from ..studio import script as script_mod
from ..studio import voice as voice_mod
from . import schemas as s
from .scripts import get_script

router = APIRouter(tags=["studio"])
log = logging.getLogger("clipper.studio")

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".mov", ".aiff", ".aif"}


def get_take(take_id: int, db: Session = Depends(get_db)) -> VoiceTake:
    take = db.get(VoiceTake, take_id)
    if take is None:
        raise HTTPException(404, "Voice take not found")
    return take


def _take_jobs(db: Session) -> dict[int, Job]:
    running = db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE)).order_by(Job.id)).all()
    return {job.take_id: job for job in running if job.take_id}


def _failures(db: Session) -> dict[int, str]:
    """The error of the latest failed voice job of each take."""
    failed = db.scalars(
        select(Job)
        .where(Job.state == "failed", Job.kind.in_(("voice_ai", "voice_rec", "align")))
        .order_by(Job.id.desc())
        .limit(200)
    ).all()
    out: dict[int, str] = {}
    for job in failed:
        if job.take_id and job.take_id not in out:
            out[job.take_id] = (job.error or "Failed").strip().splitlines()[0][:400]
    return out


def take_out(take: VoiceTake, script: Script, job: Job | None, failure: str | None = None) -> s.TakeOut:
    alignment = take.alignment
    return s.TakeOut(
        id=take.id,
        script_id=take.script_id,
        source=take.source,
        voice=take.voice,
        speed=take.speed,
        pauses=take.pauses,
        duration=take.duration,
        chosen=take.chosen,
        created_at=take.created_at,
        alignment=alignment,
        ready=bool(take.path and alignment and job is None),
        current=take.script_hash == script_mod.text_hash(script.sections),
        job=s.JobOut.model_validate(job) if job else None,
        failure=failure if job is None and not alignment else None,
    )


def _busy(db: Session, script: Script) -> None:
    active = db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE))).all()
    if any(j.script_id == script.id for j in active):
        raise HTTPException(409, "The script is being worked on. Wait for that job to finish.")


def _new_take(db: Session, script: Script, source: str, voice: str, speed: float | None) -> VoiceTake:
    if not any(sec["text"].strip() for sec in script.sections):
        raise HTTPException(409, "The script has no text yet")
    take = VoiceTake(
        script_id=script.id, source=source, voice=voice, speed=speed, script_hash=script_mod.text_hash(script.sections)
    )
    db.add(take)
    db.flush()
    return take


@router.get("/voices", response_model=s.VoiceOptions)
def voice_options():
    """The AI voices you can use. ElevenLabs voices come from your account when a key is set."""
    settings = settings_mod.load()
    eleven, error = [], None
    available = bool(config.elevenlabs_api_key())
    if available:
        try:
            eleven = voice_mod.elevenlabs_voices()
        except RuntimeError as err:
            error = str(err)
    return {
        "kokoro": [{"id": k, "label": v} for k, v in voice_mod.KOKORO_VOICES.items()],
        "kokoro_downloaded": voice_mod.kokoro_ready(),
        "chatterbox": voice_mod.chatterbox_voices(),
        "chatterbox_ready": chatterbox_mod.ready(),
        "elevenlabs": eleven,
        "elevenlabs_available": available,
        "elevenlabs_error": error,
        "defaults": {
            "engine": settings.voice_engine,
            "kokoro_voice": settings.kokoro_voice,
            "chatterbox_voice": settings.chatterbox_voice,
            "elevenlabs_voice": settings.elevenlabs_voice,
            "speed": settings.voice_speed,
            "pauses": settings.voice_pauses,
        },
    }


@router.post("/voices/sample")
def voice_sample(body: s.VoiceSample):
    """A few seconds of text in a Kokoro voice, to compare voices. Free, and cached.

    ElevenLabs voices are compared with their own preview clips instead, which
    cost no credits."""
    try:
        path = voice_mod.sample_kokoro(body.voice, body.speed, body.text, body.pauses)
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})


@router.get("/scripts/{script_id}/takes", response_model=list[s.TakeOut])
def list_takes(script: Script = Depends(get_script), db: Session = Depends(get_db)):
    running = _take_jobs(db)
    failures = _failures(db)
    return [take_out(t, script, running.get(t.id), failures.get(t.id)) for t in reversed(script.takes)]


@router.post("/scripts/{script_id}/takes/ai", response_model=s.TakeOut, status_code=202)
def create_ai_take(body: s.AiTakeCreate, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Have an AI voice read the script as it is now, then time it."""
    _busy(db, script)
    if script.form == "micro_short":
        raise HTTPException(409, "A micro-Short is silent and already has a designed cue track; no TTS is needed")
    if body.engine == "kokoro" and body.voice not in voice_mod.KOKORO_VOICES:
        raise HTTPException(400, f"Unknown Kokoro voice: {body.voice}")
    if body.engine == "chatterbox" and body.voice not in {v["id"] for v in voice_mod.chatterbox_voices()}:
        raise HTTPException(400, f"Unknown voice for Chatterbox: {body.voice}")
    take = _new_take(db, script, body.engine, body.voice, body.speed)
    take.pauses = body.pauses if body.engine in ("kokoro", "chatterbox") else None
    job = jobs.enqueue(db, "voice_ai", script.title, {"script_id": script.id, "take_id": take.id})
    log.info("ai voice queued", extra={"take_id": take.id, "engine": body.engine, "voice": body.voice})
    return take_out(take, script, job)


@router.post("/scripts/{script_id}/takes/recording", response_model=s.TakeOut, status_code=202)
async def upload_recording(file: UploadFile, script: Script = Depends(get_script), db: Session = Depends(get_db)):
    """Your own recording of the script: any audio file, or a video with sound."""
    _busy(db, script)
    if script.form == "micro_short":
        raise HTTPException(409, "A micro-Short is silent and uses its designed cue track, not a recording")
    suffix = Path(file.filename or "recording.webm").suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        raise HTTPException(400, "Unsupported file type. Use wav, mp3, m4a, flac, ogg, webm or a video file.")
    take = _new_take(db, script, "recording", Path(file.filename or "recording").name[:120], None)
    folder = voice_mod.take_dir(script.id, take.id)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"original{suffix}").open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
    job = jobs.enqueue(db, "voice_rec", script.title, {"script_id": script.id, "take_id": take.id})
    return take_out(take, script, job)


@router.get("/takes/{take_id}/audio")
def take_audio(take: VoiceTake = Depends(get_take)):
    if not take.path or not Path(take.path).is_file():
        raise HTTPException(404, "This take has no audio yet")
    return FileResponse(take.path, media_type="audio/wav", filename=f"voice-{take.script_id}-{take.id}.wav")


@router.get("/takes/{take_id}/timeline")
def take_timeline(take: VoiceTake = Depends(get_take)):
    """Every spoken word or designed silent cue token with its render time."""
    path = voice_mod.timeline_path(take)
    if not path.is_file():
        raise HTTPException(404, "This take has not been timed yet")
    return JSONResponse(content=json.loads(path.read_text()))


@router.post("/takes/{take_id}/align", response_model=s.TakeOut, status_code=202)
def realign(take: VoiceTake = Depends(get_take), db: Session = Depends(get_db)):
    """Time the take again, against the script as it is now. Use it after editing the script to match."""
    _busy(db, take.script)
    take.script_hash = script_mod.text_hash(take.script.sections)
    stages = ["cue"] if take.source == "silent" else None
    job = jobs.enqueue(db, "align", take.script.title, {"script_id": take.script_id, "take_id": take.id}, stages=stages)
    return take_out(take, take.script, job)


@router.post("/takes/{take_id}/choose", response_model=s.TakeOut)
def choose_take(take: VoiceTake = Depends(get_take), db: Session = Depends(get_db)):
    """Make this the take the video will use."""
    if not take.chosen:
        what = (
            "your recording"
            if take.source == "recording"
            else "the designed silent cue track"
            if take.source == "silent"
            else f"the {take.voice} AI voice"
        )
        compliance.record(db, take.script_id, "take", "you", f"Chose {what} (take {take.id}) for the video")
    for other in take.script.takes:
        other.chosen = other.id == take.id
    db.flush()
    return take_out(take, take.script, _take_jobs(db).get(take.id))


@router.delete("/takes/{take_id}", status_code=204)
def delete_take(take: VoiceTake = Depends(get_take), db: Session = Depends(get_db)):
    if take.id in _take_jobs(db):
        raise HTTPException(409, "This take is still being made. Cancel its job first.")
    voice_mod.remove_take_files(take.script_id, take.id)
    db.delete(take)
