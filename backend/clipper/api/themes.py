"""Themes: everything of a channel in one place, from its ideas to its published videos and their numbers."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import buffer as buffer_mod
from .. import config, jobs, llm
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Job, Theme, as_utc, utcnow
from ..studio import banner as banner_mod
from ..studio import learning
from ..studio import themes as themes_mod
from . import schemas as s
from .ideas import _latest_scripts, idea_out
from .scripts import active_jobs

router = APIRouter(prefix="/themes", tags=["studio"])
log = logging.getLogger("clipper.studio")

REFRESH_GAP_S = 15 * 60          # a manual statistics refresh at most this often (Buffer counts requests)


def get_theme(theme_id: int, db: Session = Depends(get_db)) -> Theme:
    theme = db.get(Theme, theme_id)
    if theme is None:
        raise HTTPException(404, "Theme not found")
    return theme


def _banner_jobs(db: Session) -> dict[int, Job]:
    return {(j.params or {}).get("theme_id"): j for j in db.scalars(
        select(Job).where(Job.kind == "banner", Job.state.in_(jobs.ACTIVE)).order_by(Job.id))}


def theme_out(db: Session, theme: Theme, board: dict | None = None, banner_jobs: dict | None = None) -> s.ThemeOut:
    board = board or themes_mod.board(db, theme)
    fields = {f: getattr(theme, f) for f in s.ThemeOut.model_fields if hasattr(Theme, f)}
    job = (banner_jobs if banner_jobs is not None else _banner_jobs(db)).get(theme.id)
    if theme.banner and not banner_mod.path(theme.id).is_file():
        fields["banner"] = None                 # the file is gone (data folder reset): show none
    return s.ThemeOut(**{**fields, "channels": theme.channels or []}, counts=board["counts"], inbox=board["inbox"],
                      banner_job=s.JobOut.model_validate(job) if job else None,
                      performance=learning.summary(db, theme.id),
                      next=themes_mod.next_step(board["counts"], board["inbox"]))


@router.get("", response_model=list[s.ThemeOut])
def list_themes(db: Session = Depends(get_db)):
    """Active themes first, then paused, then archived; the most recently touched first within each."""
    order = {"active": 0, "paused": 1, "archived": 2}
    themes = sorted(db.scalars(select(Theme)), key=lambda t: (order.get(t.status, 3), -as_utc(t.updated_at)
                                                               .timestamp()))
    banner_jobs = _banner_jobs(db)
    return [theme_out(db, t, banner_jobs=banner_jobs) for t in themes]


@router.post("", response_model=s.ThemeOut, status_code=201)
def create_theme(body: s.ThemeCreate, db: Session = Depends(get_db)):
    if themes_mod.by_name(db, body.name):
        raise HTTPException(409, "A theme with this name exists already")
    theme = themes_mod.create(db, body.name, body.description)
    if settings_mod.load().theme_banners:
        _queue_banner(db, theme)
    return theme_out(db, theme)


def _queue_banner(db: Session, theme: Theme, note: str = "") -> Job:
    running = _banner_jobs(db).get(theme.id)
    if running is not None:
        return running
    return jobs.enqueue(db, "banner", f"Banner for “{theme.name}”", {"theme_id": theme.id, "note": note})


@router.get("/{theme_id}", response_model=s.ThemeBoard)
def theme_board(theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    """The theme's work by stage."""
    board = themes_mod.board(db, theme)
    running = active_jobs(db)
    scripts = _latest_scripts(db)
    items = [s.ThemeBoardItem(**item, job=s.JobOut.model_validate(running[item["id"]])
                              if item["id"] in running else None) for item in board["items"]]
    return s.ThemeBoard(theme=theme_out(db, theme, board), items=items,
                        ideas=[idea_out(i, scripts.get(i.id)) for i in board["ideas"]],
                        proven=learning.proven(db, theme))


@router.patch("/{theme_id}", response_model=s.ThemeOut)
def update_theme(body: s.ThemePatch, theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    if body.name is not None and body.name.strip() != theme.name:
        other = themes_mod.by_name(db, body.name)
        if other is not None and other.id != theme.id:
            raise HTTPException(409, "A theme with this name exists already: merge them instead")
        themes_mod.rename(db, theme, body.name)
    for field in ("description", "color", "status", "notes"):
        value = getattr(body, field)
        if value is not None:
            setattr(theme, field, value.strip() if isinstance(value, str) and field != "color" else value)
    if body.channels is not None:
        theme.channels = list(dict.fromkeys(c for c in body.channels if c))
    theme.updated_at = utcnow()
    db.flush()
    return theme_out(db, theme)


@router.post("/{theme_id}/merge", response_model=s.ThemeOut)
def merge_theme(body: s.ThemeMerge, theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    """Move this theme's ideas and scripts into another theme and delete this one."""
    target = db.get(Theme, body.into)
    if target is None or target.id == theme.id:
        raise HTTPException(400, "Choose another theme to merge into")
    moved = themes_mod.merge(db, theme, target)
    target.updated_at = utcnow()
    log.info("themes merged", extra={"into": target.id, "moved": moved})
    return theme_out(db, target)


@router.get("/{theme_id}/contents", response_model=s.ThemeContents)
def theme_contents(theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    """What deleting the theme would remove, and what stops it right now."""
    found = themes_mod.contents(db, theme)
    return s.ThemeContents(**{k: found[k] for k in ("ideas", "scripts", "takes", "videos")},
                           blockers=themes_mod.blockers(db, theme))


@router.delete("/{theme_id}", status_code=204)
def delete_theme(confirm: str = Query("", max_length=300), theme: Theme = Depends(get_theme),
                 db: Session = Depends(get_db)):
    """Delete the theme with all its ideas, scripts, voice takes, videos and their files.

    A theme with work in it needs its name in `confirm`. Exported copies in the output folder stay."""
    found = themes_mod.contents(db, theme)
    if (found["ideas"] or found["scripts"]) and confirm.strip().lower() != theme.name.strip().lower():
        raise HTTPException(409, "Type the theme's name to confirm: everything in it is deleted")
    problems = themes_mod.blockers(db, theme)
    if problems:
        raise HTTPException(409, problems[0])
    removed = themes_mod.delete_everything(db, theme)
    log.info("theme deleted", extra={"theme_id": theme.id, **removed})


@router.post("/{theme_id}/banner", response_model=s.JobOut, status_code=202)
def draw_banner(body: s.BannerRequest | None = None, theme: Theme = Depends(get_theme),
                db: Session = Depends(get_db)):
    """Have the AI draw (or draw again) the banner. Writing tier, one call or a few."""
    if not settings_mod.load().theme_banners:
        raise HTTPException(409, "Theme banners are off: turn them on in Settings")
    return _queue_banner(db, theme, body.note if body else "")


@router.put("/{theme_id}/banner", response_model=s.ThemeOut)
async def upload_banner(file: UploadFile, theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    """Your own picture as the banner, cropped to 10:3."""
    try:
        banner_mod.upload(theme.id, await file.read(banner_mod.MAX_UPLOAD + 1))
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    db.expire(theme)
    return theme_out(db, theme)


@router.delete("/{theme_id}/banner", response_model=s.ThemeOut)
def remove_banner(theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    banner_mod.remove(theme.id)
    db.expire(theme)
    return theme_out(db, theme)


@router.get("/{theme_id}/banner.jpg", include_in_schema=False)
def banner_image(theme: Theme = Depends(get_theme)):
    path = banner_mod.path(theme.id)
    if not path.is_file():
        raise HTTPException(404, "No banner")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/{theme_id}/learning", response_model=s.ThemeLearning)
def theme_learning(theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    """Its published videos ranked by their numbers, the lessons drawn, and what new ideas would build on."""
    table = learning.ranked(learning.published(db, theme.id))
    return s.ThemeLearning(**table, insights=theme.insights, can_learn=table["with_views"] >= learning.MIN_FOR_LESSONS,
                           proven=learning.proven(db, theme),
                           stats_sources={"buffer": buffer_mod.configured("storyboard"),
                                          "youtube": bool(config.youtube_api_key())})


@router.post("/{theme_id}/insights", response_model=s.ThemeLearning)
def draw_lessons(theme: Theme = Depends(get_theme), db: Session = Depends(get_db)):
    """Ask the AI what the numbers say. Writing tier, one call."""
    try:
        learning.insights(theme.id)
    except LookupError as err:
        raise HTTPException(409, str(err)) from err
    except llm.AiUnavailable as err:
        raise HTTPException(503, f"The AI is unavailable: {err}") from err
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    db.expire_all()
    return theme_learning(db.get(Theme, theme.id), db)


@router.post("/stats/refresh", response_model=s.JobOut, status_code=202)
def refresh_stats(db: Session = Depends(get_db)):
    """Read views and reactions now (Buffer and YouTube) instead of waiting for the daily pass."""
    last = db.scalar(select(Job).where(Job.kind == "stats").order_by(Job.id.desc()).limit(1))
    if last is not None and last.state in jobs.ACTIVE:
        return last
    if last is not None and last.finished_at and (utcnow() - as_utc(last.finished_at)).total_seconds() < REFRESH_GAP_S:
        raise HTTPException(429, "Statistics were read a few minutes ago. Buffer counts every request: "
                                 "try again in a quarter of an hour.")
    return jobs.enqueue(db, "stats", "Reading views and reactions", {"force": True})
