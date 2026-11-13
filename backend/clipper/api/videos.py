"""Videos: add one, read it, edit its moments and brief, start work on it."""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import campaigns as campaigns_mod
from .. import config, jobs, pipeline
from .. import ingest as ingest_mod
from .. import settings as settings_mod
from .. import transcribe as transcribe_mod
from ..db import get_db
from ..db.models import Campaign, Clip, Job, Moment, Video
from . import schemas as s

router = APIRouter(prefix="/videos", tags=["videos"])


def get_video(video_id: str, db: Session = Depends(get_db)) -> Video:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "Video not found")
    return video


def require_idle(video: Video = Depends(get_video), db: Session = Depends(get_db)) -> Video:
    if jobs.has_active(db, video.id):
        raise HTTPException(409, "A job is already running for this video. Wait for it to finish.")
    return video


def clip_out(clip: Clip) -> s.ClipOut:
    out = s.ClipOut.model_validate(clip)
    out.file_exists = Path(clip.path).exists()
    return out


def _summary(video: Video, busy: set[str], failures: dict[str, dict] | None = None) -> dict:
    failure = (failures or {}).get(video.id) if not video.clips else None
    return {
        "id": video.id, "url": video.url, "title": video.title, "uploader": video.uploader,
        "duration": video.duration, "width": video.width, "height": video.height,
        "created_at": video.created_at,
        "moment_count": len(video.moments),
        "clip_count": len(video.clips),
        "approved_count": sum(1 for c in video.clips if c.status == "approved"),
        "has_file": ingest_mod.find_video(ingest_mod.source_dir(video.id)) is not None,
        "busy": video.id in busy,
        "failed": failure is not None and video.id not in busy,
        "error": (failure or {}).get("error") or "",
        "failed_job_id": (failure or {}).get("id"),
    }


def _failures(db: Session) -> dict[str, dict]:
    """For each video, its latest job if that job failed: {video_id: {id, error}}."""
    latest: dict[str, Job] = {}
    for job in db.scalars(select(Job).where(Job.video_id.is_not(None)).order_by(Job.id)):
        latest[job.video_id] = job
    return {vid: {"id": job.id, "error": job.error or job.message} for vid, job in latest.items()
            if job.state == "failed"}


def _busy_ids(db: Session) -> set[str]:
    return set(db.scalars(select(Job.video_id).where(Job.state.in_(jobs.ACTIVE), Job.video_id.is_not(None))))


def _detail(video: Video, db: Session) -> s.VideoDetail:
    src_dir = ingest_mod.source_dir(video.id)
    file = ingest_mod.find_video(src_dir)
    settings = settings_mod.load()
    moments = [
        s.MomentOut(id=m.id, position=m.position, start=m.start_s, end=m.end_s, title=m.title, hook=m.hook,
                    caption=m.caption, score=m.score, reason=m.reason,
                    clip=clip_out(m.clip) if m.clip else None)
        for m in video.moments
    ]
    return s.VideoDetail(
        **_summary(video, _busy_ids(db), _failures(db)),
        brief=video.brief,
        file_mb=round(file.stat().st_size / 1e6) if file else 0,
        has_transcript=transcribe_mod.transcript_path(src_dir).exists(),
        subtitles=video.subtitles,
        subtitle_mode=video.subtitle_mode,
        effective_subtitle_mode=pipeline.effective_subtitle_mode(video, settings),
        campaign=video.campaign,
        moments=moments,
    )


def _stages(kind: str) -> list[str]:
    """The brief check only runs on its own, or after renders when the setting is on."""
    auto = settings_mod.load().auto_compliance
    return [stage for stage in jobs.STAGES[kind] if stage != "check" or kind == "check" or auto]


def _enqueue(db: Session, kind: str, label: str, params: dict, video_id: str | None = None) -> Job:
    job = jobs.enqueue(db, kind, label, params, video_id)
    job.stages = _stages(kind)
    return job


@router.get("", response_model=list[s.VideoSummary])
def list_videos(db: Session = Depends(get_db)):
    videos = db.scalars(
        select(Video).options(selectinload(Video.moments), selectinload(Video.clips))
        .order_by(Video.created_at.desc())
    ).all()
    busy, failures = _busy_ids(db), _failures(db)
    return [_summary(video, busy, failures) for video in videos]


@router.post("", response_model=s.JobOut, status_code=202)
def add_video(body: s.VideoCreate, db: Session = Depends(get_db)):
    """Queue the whole pipeline for a link or a file: fetch, transcribe, select, render."""
    src = body.src.strip()
    label = src if ingest_mod.is_url(src) else Path(src).name
    params = body.model_dump()
    if body.campaign_id is not None:
        campaign = db.get(Campaign, body.campaign_id)
        if campaign is None:
            raise HTTPException(400, "That campaign no longer exists")
        if not body.brief.strip():
            params["brief"] = campaigns_mod.brief_for(campaign)
    # Only files received through the upload endpoint are moved; a path the user
    # typed is copied, so their original is never touched.
    uploads = config.uploads_dir().resolve()
    move = not ingest_mod.is_url(src) and uploads in Path(src).expanduser().resolve().parents
    return _enqueue(db, "process", label, {**params, "src": src, "move": move})


@router.post("/upload", response_model=s.UploadOut)
async def upload(file: UploadFile):
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(file.filename or "video.mp4").name).strip("-.")
    if Path(name).suffix.lower() not in ingest_mod.VIDEO_SUFFIXES:
        raise HTTPException(400, "Unsupported file type. Use mp4, mov, mkv, webm or m4v.")
    target = config.uploads_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
    return {"path": str(target), "name": name}


@router.get("/{video_id}", response_model=s.VideoDetail)
def read_video(video: Video = Depends(get_video), db: Session = Depends(get_db)):
    return _detail(video, db)


@router.patch("/{video_id}", response_model=s.VideoDetail)
def update_video(body: s.VideoPatch, video: Video = Depends(get_video), db: Session = Depends(get_db)):
    if body.brief is not None:
        video.brief = body.brief.strip()
    if "campaign_id" in body.model_fields_set:
        campaign = db.get(Campaign, body.campaign_id) if body.campaign_id is not None else None
        if body.campaign_id is not None and campaign is None:
            raise HTTPException(400, "That campaign no longer exists")
        video.campaign = campaign
        if campaign is not None and body.use_campaign_brief:
            video.brief = campaigns_mod.brief_for(campaign)
    if body.subtitle_mode is not None:
        video.subtitle_mode = None if body.subtitle_mode == "default" else body.subtitle_mode
    db.flush()
    return _detail(video, db)


@router.put("/{video_id}/moments", response_model=s.VideoDetail)
def replace_moments(body: s.MomentsIn, video: Video = Depends(require_idle), db: Session = Depends(get_db)):
    pipeline.save_moments(db, video, [m.model_dump() for m in body.moments])
    db.flush()
    db.refresh(video)
    return _detail(video, db)


@router.delete("/{video_id}", status_code=204)
def delete_video(video: Video = Depends(require_idle), db: Session = Depends(get_db)):
    pipeline.delete_video(db, video)


@router.delete("/{video_id}/source-file", status_code=204)
def delete_source_file(video: Video = Depends(require_idle)):
    file = ingest_mod.find_video(ingest_mod.source_dir(video.id))
    if file:
        file.unlink()


@router.post("/{video_id}/select", response_model=s.JobOut, status_code=202)
def select_moments(body: s.SelectRequest, video: Video = Depends(require_idle), db: Session = Depends(get_db)):
    return _enqueue(db, "select", video.title, body.model_dump(), video.id)


@router.post("/{video_id}/render", response_model=s.JobOut, status_code=202)
def render_clips(body: s.RenderRequest, video: Video = Depends(require_idle), db: Session = Depends(get_db)):
    if body.moment_ids:
        known = set(db.scalars(select(Moment.id).where(Moment.video_id == video.id)))
        if not set(body.moment_ids) <= known:
            raise HTTPException(400, "Some of those moments do not belong to this video")
    return _enqueue(db, "render", video.title, body.model_dump(), video.id)


@router.post("/{video_id}/check", response_model=s.JobOut, status_code=202)
def check_clips(body: s.CheckRequest, video: Video = Depends(require_idle), db: Session = Depends(get_db)):
    if not video.brief.strip():
        raise HTTPException(400, "Add a campaign brief to this video first.")
    if not video.clips:
        raise HTTPException(400, "There are no rendered clips to check.")
    return _enqueue(db, "check", video.title, body.model_dump(), video.id)


@router.post("/{video_id}/detect-subtitles", response_model=s.JobOut, status_code=202)
def detect_subtitles(video: Video = Depends(require_idle), db: Session = Depends(get_db)):
    return _enqueue(db, "subtitles", video.title, {}, video.id)
