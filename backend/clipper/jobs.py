"""The job queue, kept in the database.

The API enqueues, the worker claims. Every job records its stages, progress
and a log of events, so what happened is visible in the UI and survives a
restart. Reporters are how pipeline code talks about progress without knowing
whether it runs inside a job or from the command line.
"""

import logging
import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .db import session_scope
from .db.models import Job, JobEvent, utcnow

log = logging.getLogger("clipper.jobs")

ACTIVE = ("queued", "running")
FINISHED = ("succeeded", "failed", "cancelled")

STAGES = {
    "process": ["download", "transcribe", "subtitles", "select", "render", "check"],
    "select": ["select"],
    "render": ["render", "check"],
    "check": ["check"],
    "subtitles": ["subtitles"],
    "scout": ["scout"],
    # Studio scripts
    "script": ["research", "write", "factcheck"],
    "redraft": ["write", "factcheck"],
    "factcheck": ["factcheck"],
    "spoken": ["spoken"],
    "simpler": ["simpler"],
    "voice_ai": ["speak", "align"],
    "voice_rec": ["prepare", "align"],
    "align": ["align"],
    "storyboard": ["plan", "draw"],
    "animate": ["draw", "animate"],
}
STAGE_LABELS = {
    "download": "Fetching the video",
    "transcribe": "Transcribing",
    "subtitles": "Looking for burned-in subtitles",
    "select": "Choosing moments",
    "render": "Rendering clips",
    "check": "Checking clips against the brief",
    "scout": "Reading public campaign listings",
    "research": "Researching with web sources",
    "write": "Writing the script",
    "factcheck": "Checking the facts",
    "spoken": "Rewriting the script to sound spoken",
    "simpler": "Making the script simpler",
    "speak": "Generating the AI voice",
    "prepare": "Preparing the recording",
    "align": "Timing every word against the script",
    "plan": "Planning the scenes",
    "draw": "Drawing new props and finding photos",
    "animate": "Animating the video",
}


class JobCancelled(Exception):
    """Raised inside a running job when the user asked to stop it."""


# --- queue operations ------------------------------------------------------------

def enqueue(db: Session, kind: str, label: str, params: dict, video_id: str | None = None) -> Job:
    job = Job(kind=kind, label=label, params=params, video_id=video_id,
              stages=STAGES[kind], state="queued", message="Waiting")
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, message="Queued"))
    log.info("job queued", extra={"job_id": job.id, "kind": kind, "video_id": video_id})
    return job


def has_active(db: Session, video_id: str) -> bool:
    return db.scalar(select(Job.id).where(Job.video_id == video_id, Job.state.in_(ACTIVE)).limit(1)) is not None


def claim_next(worker_id: str) -> int | None:
    """Atomically move the oldest queued job to running. Returns its id."""
    with session_scope() as db:
        job_id = db.scalar(select(Job.id).where(Job.state == "queued").order_by(Job.id).limit(1))
        if job_id is None:
            return None
        # The WHERE on state makes the claim safe if two workers race for the same row.
        claimed = db.execute(
            update(Job).where(Job.id == job_id, Job.state == "queued")
            .values(state="running", started_at=utcnow(), worker_id=worker_id, message="Starting")
        ).rowcount
        return job_id if claimed else None


def recover_interrupted() -> int:
    """Jobs left 'running' by a process that died are failed so they can be retried."""
    with session_scope() as db:
        stuck = db.scalars(select(Job).where(Job.state == "running")).all()
        for job in stuck:
            job.state, job.finished_at, job.progress = "failed", utcnow(), None
            job.error = "Interrupted: the worker stopped while this job was running. Retry it."
            job.message = "Interrupted"
            db.add(JobEvent(job_id=job.id, level="error", stage=job.stage, message=job.error))
        return len(stuck)


def request_cancel(db: Session, job: Job) -> None:
    if job.state == "queued":
        job.state, job.finished_at, job.message = "cancelled", utcnow(), "Cancelled"
        db.add(JobEvent(job_id=job.id, level="warning", message="Cancelled before it started"))
    elif job.state == "running":
        job.cancel_requested = True
        db.add(JobEvent(job_id=job.id, level="warning", stage=job.stage,
                        message="Cancel requested; stopping at the next safe point"))


def finish(job_id: int, state: str, message: str, error: str | None = None) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.state, job.message, job.error = state, message, error
        job.progress, job.finished_at = None, utcnow()
        level = {"succeeded": "info", "cancelled": "warning"}.get(state, "error")
        db.add(JobEvent(job_id=job_id, level=level, stage=job.stage, message=error or message))


# --- reporters ---------------------------------------------------------------------

class Reporter:
    """Progress sink. This base class ignores everything (used by tests)."""

    def stage(self, name: str) -> None: ...
    def progress(self, fraction: float | None, message: str | None = None) -> None: ...
    def info(self, message: str) -> None: ...
    def warn(self, message: str) -> None: ...
    def set_video(self, video_id: str, title: str | None = None) -> None: ...


class ConsoleReporter(Reporter):
    """Prints stages and messages; used by the command line."""

    def stage(self, name: str) -> None:
        print(f"{STAGE_LABELS.get(name, name)}...", flush=True)

    def info(self, message: str) -> None:
        print(f"  {message}", flush=True)

    def warn(self, message: str) -> None:
        print(f"  note: {message}", flush=True)


class JobReporter(Reporter):
    """Writes a job's progress and log to the database and the process log."""

    WRITE_EVERY = 0.7   # seconds between progress writes; also how often cancel is checked

    def __init__(self, job_id: int):
        self.job_id = job_id
        self._stage: str | None = None
        self._last_write = 0.0

    def _event(self, level: str, message: str) -> None:
        with session_scope() as db:
            db.add(JobEvent(job_id=self.job_id, level=level, stage=self._stage, message=message))
        log.log(logging.WARNING if level == "warning" else logging.INFO, message,
                extra={"job_id": self.job_id, "stage": self._stage})

    def stage(self, name: str) -> None:
        self._stage = name
        label = STAGE_LABELS.get(name, name)
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job.cancel_requested:
                raise JobCancelled
            job.stage, job.progress, job.message = name, None, label
        self._event("info", label)

    def progress(self, fraction: float | None, message: str | None = None) -> None:
        now = time.monotonic()
        if now - self._last_write < self.WRITE_EVERY:
            return
        self._last_write = now
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            if job.cancel_requested:
                raise JobCancelled
            job.progress = fraction
            if message:
                job.message = message

    def info(self, message: str) -> None:
        self._event("info", message)

    def warn(self, message: str) -> None:
        self._event("warning", message)

    def set_video(self, video_id: str, title: str | None = None) -> None:
        with session_scope() as db:
            job = db.get(Job, self.job_id)
            job.video_id = video_id
            if title:
                job.label = title   # nicer than the raw link the job was queued with
