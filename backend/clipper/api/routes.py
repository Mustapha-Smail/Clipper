"""Clips, jobs, activity log, settings and system status."""

import os
import shutil
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from .. import claude_cli, config, jobs, llm, pipeline, posting
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import AiUsage, Clip, Job, JobEvent, Script, Storyboard, Video, Worker, as_utc, utcnow
from . import schemas as s
from .videos import clip_out

clips = APIRouter(prefix="/clips", tags=["clips"])
jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])
activity = APIRouter(prefix="/activity", tags=["activity"])
settings_router = APIRouter(prefix="/settings", tags=["settings"])
system = APIRouter(prefix="/system", tags=["system"])

WORKER_TIMEOUT = timedelta(seconds=20)


# --- clips ---------------------------------------------------------------------------

def get_clip(clip_id: int, db: Session = Depends(get_db)) -> Clip:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(404, "Clip not found")
    return clip


@clips.get("", response_model=list[s.ClipListItem])
def list_clips(status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
               limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db)):
    """Every rendered clip, newest first: the review queue."""
    query = (select(Clip).options(joinedload(Clip.moment), joinedload(Clip.video))
             .order_by(Clip.rendered_at.desc()).limit(limit))
    if status:
        query = query.where(Clip.status == status)
    return [
        s.ClipListItem(**clip_out(clip).model_dump(), video_title=clip.video.title,
                       title=clip.moment.title, hook=clip.moment.hook, caption=clip.moment.caption,
                       score=clip.moment.score)
        for clip in db.scalars(query)
    ]


@clips.patch("/{clip_id}", response_model=s.ClipOut)
def review_clip(body: s.ClipPatch, clip: Clip = Depends(get_clip), db: Session = Depends(get_db)):
    if body.status != "approved" and posting.has_live(db, "clip", clip.id):
        raise HTTPException(409, "A post of this clip is waiting on Buffer. Cancel it before taking back the approval.")
    clip.status = body.status
    return clip_out(clip)


@clips.delete("/{clip_id}", status_code=204)
def delete_clip(clip: Clip = Depends(get_clip), db: Session = Depends(get_db)):
    if jobs.has_active(db, clip.video_id):
        raise HTTPException(409, "A job is running for this video. Wait for it to finish.")
    pipeline.remove_clip_files(clip.path)
    db.delete(clip)


@clips.get("/{clip_id}/file")
def clip_file(download: bool = False, clip: Clip = Depends(get_clip)):
    path = Path(clip.path)
    if not path.is_file():
        raise HTTPException(404, "The clip file is missing from disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name if download else None)


# --- jobs ----------------------------------------------------------------------------

def get_job(job_id: int, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@jobs_router.get("", response_model=list[s.JobOut])
def list_jobs(active: bool = False, video_id: str | None = None,
              limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    query = select(Job).order_by(Job.id.desc()).limit(limit)
    if active:
        query = query.where(Job.state.in_(jobs.ACTIVE))
    if video_id:
        query = query.where(Job.video_id == video_id)
    return db.scalars(query).all()


@jobs_router.get("/{job_id}", response_model=s.JobOut)
def read_job(job: Job = Depends(get_job)):
    return job


@jobs_router.get("/{job_id}/events", response_model=list[s.JobEventOut])
def job_events(after: int = 0, job: Job = Depends(get_job), db: Session = Depends(get_db)):
    """The job's log. Pass the last id you have as `after` to fetch only new lines."""
    return db.scalars(select(JobEvent).where(JobEvent.job_id == job.id, JobEvent.id > after)
                      .order_by(JobEvent.id).limit(500)).all()


@jobs_router.post("/{job_id}/cancel", response_model=s.JobOut)
def cancel_job(job: Job = Depends(get_job), db: Session = Depends(get_db)):
    if job.state not in jobs.ACTIVE:
        raise HTTPException(409, "This job has already finished")
    jobs.request_cancel(db, job)
    return job


@jobs_router.delete("/{job_id}", status_code=204)
def delete_job(job: Job = Depends(get_job), db: Session = Depends(get_db)):
    """Forget a failed or cancelled job. For a clipping video that never got a clip, delete the video too."""
    if job.state not in ("failed", "cancelled"):
        raise HTTPException(409, "Only failed or cancelled jobs can be deleted")
    video = db.get(Video, job.video_id) if job.video_id else None
    if video is not None and not video.clips and not jobs.has_active(db, video.id):
        from .. import pipeline

        for other in db.scalars(select(Job).where(Job.video_id == video.id, Job.id != job.id,
                                                  Job.state.in_(("failed", "cancelled")))):
            db.delete(other)
        pipeline.delete_video(db, video)
    db.delete(job)


@jobs_router.post("/{job_id}/retry", response_model=s.JobOut, status_code=202)
def retry_job(job: Job = Depends(get_job), db: Session = Depends(get_db)):
    if job.state not in ("failed", "cancelled"):
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    if job.video_id and jobs.has_active(db, job.video_id):
        raise HTTPException(409, "A job is already running for this video")
    if job.script_id and any(j.script_id == job.script_id
                             for j in db.scalars(select(Job).where(Job.state.in_(jobs.ACTIVE)))):
        raise HTTPException(409, "A job is already running for this script")
    again = jobs.enqueue(db, job.kind, job.label, dict(job.params or {}), job.video_id)
    again.stages = list(job.stages)
    return again


# --- activity log ------------------------------------------------------------------------

@activity.get("", response_model=list[s.ActivityEvent])
def recent_activity(level: str | None = Query(None, pattern="^(info|warning|error)$"),
                    limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db)):
    """Log lines across all jobs, newest first."""
    query = select(JobEvent, Job).join(Job).order_by(JobEvent.id.desc()).limit(limit)
    if level == "warning":
        query = query.where(JobEvent.level.in_(("warning", "error")))
    elif level:
        query = query.where(JobEvent.level == level)
    return [
        s.ActivityEvent(**s.JobEventOut.model_validate(event).model_dump(), job_label=job.label,
                        job_kind=job.kind, video_id=job.video_id)
        for event, job in db.execute(query)
    ]


# --- settings --------------------------------------------------------------------------

@settings_router.get("", response_model=s.SettingsOut)
def read_settings():
    return {
        "settings": settings_mod.load().model_dump(),
        "defaults": settings_mod.Settings().model_dump(),
        "fonts": settings_mod.font_choices(),
        "default_clips_dir": str(config.default_clips_dir()),
    }


@settings_router.put("", response_model=s.SettingsOut)
def write_settings(body: dict):
    try:
        settings_mod.save(settings_mod.Settings(**body))
    except ValidationError as err:
        first = err.errors()[0]
        message = first["msg"].removeprefix("Value error, ")
        field = ".".join(str(p) for p in first["loc"]).replace("_", " ")
        raise HTTPException(400, f"{field}: {message}" if field else message) from err
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    return read_settings()


# --- AI usage ------------------------------------------------------------------------------

@system.get("/ai-usage", response_model=s.UsageReport)
def ai_usage(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    """What the AI calls of the last `days` days cost, by task and by model. Costs are estimates."""
    since = utcnow() - timedelta(days=days)
    rows = db.scalars(select(AiUsage).where(AiUsage.created_at >= since)).all()

    def group(key) -> list[dict]:
        lines: dict[str, dict] = {}
        for r in rows:
            k, label, tier = key(r)
            line = lines.setdefault(k, {"key": k, "label": label, "tier": tier, "calls": 0, "failed": 0,
                                        "input_tokens": 0, "output_tokens": 0, "searches": 0, "cost_usd": 0.0,
                                        "unpriced_calls": 0})
            line["calls"] += 1
            line["failed"] += 0 if r.ok else 1
            line["input_tokens"] += r.input_tokens + r.cache_read_tokens
            line["output_tokens"] += r.output_tokens
            line["searches"] += r.searches
            if r.cost_usd is None:
                line["unpriced_calls"] += 1 if r.ok else 0
            else:
                line["cost_usd"] = round(line["cost_usd"] + r.cost_usd, 4)
        return sorted(lines.values(), key=lambda x: -x["cost_usd"])

    settings = settings_mod.load()
    return {
        "days": days, "calls": len(rows), "failed": sum(1 for r in rows if not r.ok),
        "cost_usd": round(sum(r.cost_usd or 0 for r in rows), 4),
        "by_task": group(lambda r: (r.task, llm.TASKS.get(r.task, (r.tier, r.task))[1], r.tier)),
        "by_model": group(lambda r: (r.model, f"{r.model} ({r.provider})", r.tier)),
        "tiers": {tier: {"provider": getattr(settings, f"ai_{tier}_provider"),
                         "model": getattr(settings, f"ai_{tier}_model"),
                         "tasks": [label for t2, label in llm.TASKS.values() if t2 == tier]}
                  for tier in ("writing", "quick", "research")},
    }


@system.get("/ai-models")
def ai_models(refresh: bool = False):
    """The models each provider offers this account, known ones first with their list prices."""
    return {provider: llm.available_models(provider, refresh) for provider in llm.PROVIDERS}


# --- system ------------------------------------------------------------------------------

@system.get("/status", response_model=s.SystemStatus)
def status(db: Session = Depends(get_db)):
    settings = settings_mod.load()
    data = config.data_dir()
    data.mkdir(parents=True, exist_ok=True)

    def resolved(fn) -> str:
        try:
            return fn(settings)
        except ValueError as err:
            return f"unavailable ({err})"

    last_seen = as_utc(db.scalar(select(func.max(Worker.last_seen))))
    counts = dict(db.execute(select(Job.state, func.count()).where(Job.state.in_(jobs.ACTIVE))
                             .group_by(Job.state)).all())
    return {
        "version": config.VERSION,
        "api_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "youtube_key": bool(config.youtube_api_key()),
        "elevenlabs_key": bool(config.elevenlabs_api_key()),
        "openai_key": bool(config.openai_api_key()),
        "claude_cli": claude_cli.binary() is not None,
        "claude_cli_token": bool(config.claude_cli_token()),
        "ai_providers": sorted({getattr(settings, f"ai_{tier}_provider") for tier in ("writing", "quick", "research")}),
        "openai_base_url": config.openai_base_url(),
        "in_docker": config.IN_DOCKER,
        "database": db.get_bind().dialect.name,
        "data_dir": str(data),
        "clips_dir": str(settings_mod.clips_dir(settings)),
        "disk_free_gb": round(shutil.disk_usage(data).free / 1e9, 1),
        "whisper_engine": resolved(settings_mod.resolve_whisper_engine),
        "encoder": resolved(settings_mod.resolve_encoder),
        "tools": {tool: shutil.which(tool) is not None for tool in ("ffmpeg", "yt-dlp")},
        "worker_online": last_seen is not None and utcnow() - last_seen < WORKER_TIMEOUT,
        "worker_last_seen": last_seen,
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "features": {"clipping": settings.feature_clipping, "studio": settings.feature_studio},
        "ai": llm.health(),
        "posting": posting.readiness(),
    }


COST_NOTE = ("AI model calls only, as estimated from tokens. Kokoro and Chatterbox voices and rendering are free; "
             "ElevenLabs credits are not counted. Calls made before costs were tracked per video are not included.")


@system.get("/costs", response_model=s.CostReport)
def costs(script_id: int | None = None, storyboard_id: int | None = None, video_id: str | None = None,
          db: Session = Depends(get_db)):
    """What AI cost for one Studio video (its idea, its script, its storyboard), one script, or one clipped video."""
    if storyboard_id is not None:
        board = db.get(Storyboard, storyboard_id)
        if board is None:
            raise HTTPException(404, "Storyboard not found")
        script = board.script
        steps = [("Idea", [f"idea:{script.idea_id}"] if script.idea_id else []),
                 ("Script", [f"script:{script.id}"]), ("This video", [f"storyboard:{board.id}"])]
    elif script_id is not None:
        script = db.get(Script, script_id)
        if script is None:
            raise HTTPException(404, "Script not found")
        steps = [("Idea", [f"idea:{script.idea_id}"] if script.idea_id else []),
                 ("Script", [f"script:{script.id}"]),
                 ("Videos", [f"storyboard:{b.id}" for b in script.storyboards])]
    elif video_id is not None:
        video = db.get(Video, video_id)
        steps = [("Campaign", [f"campaign:{video.campaign_id}"])] if video and video.campaign_id else []
        steps.append(("This video", [f"video:{video_id}"]))
    else:
        raise HTTPException(400, "Say which: script_id, storyboard_id or video_id")
    return {**llm.costs(steps), "note": COST_NOTE}
