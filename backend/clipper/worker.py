"""The worker: takes jobs off the queue and runs them, one at a time.

One at a time is deliberate. Transcription and rendering each want most of the
machine, so two jobs together would only make both slower. Run exactly one
worker process: on start it treats any job still marked 'running' as
interrupted.
"""

import logging
import os
import signal
import socket
import threading
import time

from . import buffer, codex_cli, jobs, llm, pipeline, posting, scout
from . import settings as settings_mod
from .db import session_scope
from .db.models import Job, Worker, utcnow
from .ingest import find_video, source_dir
from .log import configure
from .studio import animate as animate_mod
from .studio import compliance, retention
from .studio import director as director_mod
from .studio import script as script_mod
from .studio import storyboard as storyboard_mod
from .studio import voice as voice_mod

HEARTBEAT_SECONDS = 5
SWEEP_SECONDS = 3600        # how often an idle worker looks for renders past their grace period
SCOUT_CHECK_SECONDS = 300   # how often an idle worker checks whether a scheduled campaign scout is due
POSTS_DUE_SECONDS = 600     # how often an idle worker asks Buffer about posts whose time has come
POSTS_ALL_SECONDS = 86400   # ... and about every waiting post (Buffer counts requests: see posting.py)
log = logging.getLogger("clipper.worker")


def run_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        kind, params, video_id, stages = job.kind, dict(job.params or {}), job.video_id, list(job.stages)

    rep = jobs.JobReporter(job_id)
    started = time.monotonic()
    llm.job_ref = (f"storyboard:{params['storyboard_id']}" if params.get("storyboard_id")
                   else f"script:{params['script_id']}" if params.get("script_id")
                   else f"video:{video_id}" if video_id else None)
    log.info("job started", extra={"job_id": job_id, "kind": kind, "video_id": video_id})
    try:
        settings = settings_mod.load()
        clip_ids = params.get("clip_ids")
        done = "Done"          # the final message; a stage may say more (the scout, when it ran without AI)
        for stage in stages:
            rep.stage(stage)
            if stage == "download":
                # A retried job whose file is already here must not fetch (or move) it again.
                if video_id and find_video(source_dir(video_id)):
                    rep.info("The video file is already on disk; skipping the download")
                else:
                    video_id = pipeline.ingest(params["src"], settings, rep, move=params.get("move", False),
                                               brief=params.get("brief"), campaign_id=params.get("campaign_id"),
                                               clip_format=params.get("clip_format"))
            elif stage == "transcribe":
                pipeline.transcribe(video_id, settings, rep, isolate=True)
            elif stage == "subtitles":
                pipeline.detect_subtitles(video_id, rep, force=kind == "subtitles")
            elif stage == "select":
                pipeline.select(video_id, settings, rep, params.get("n"), params.get("min_len"),
                                params.get("max_len"))
            elif stage == "render":
                clip_ids = pipeline.render(video_id, settings, rep, params.get("moment_ids"))
            elif stage == "check":
                pipeline.check(video_id, settings, rep, clip_ids, skip_without_brief=kind != "check")
            elif stage == "scout":
                done = scout.outcome(scout.run(settings, rep))
            elif stage == "upload":
                posting.upload(params["post_ids"], rep)
            elif stage == "schedule":
                done = posting.schedule(params["post_ids"], rep)
            elif stage == "sync":
                posting.sync(rep)
            elif stage == "research":
                script_mod.research(params["script_id"], rep)
            elif stage == "write":
                script_mod.write(params["script_id"], rep)
            elif stage == "factcheck":
                script_mod.factcheck(params["script_id"], rep)
            elif stage == "spoken":
                script_mod.make_spoken(params["script_id"], rep)
            elif stage == "simpler":
                script_mod.make_simpler(params["script_id"], rep)
            elif stage == "speak":
                voice_mod.speak(params["take_id"], settings, rep)
            elif stage == "prepare":
                voice_mod.prepare(params["take_id"], settings, rep)
            elif stage == "align":
                voice_mod.align(params["take_id"], settings, rep)
            elif stage == "plan":
                storyboard_mod.plan(params["storyboard_id"], rep)
            elif stage == "draw":
                storyboard_mod.draw_missing(params["storyboard_id"], rep)
            elif stage == "animate":
                animate_mod.render(params["storyboard_id"], settings, rep)
            elif stage == "direct":
                director_mod.run(params["storyboard_id"], settings, rep)
        with session_scope() as db:
            compliance.record_job(db, kind, params)
        jobs.finish(job_id, "succeeded", done)
        outcome = "succeeded"
    except jobs.JobCancelled:
        jobs.finish(job_id, "cancelled", "Cancelled")
        outcome = "cancelled"
    except Exception as err:  # noqa: BLE001 - a job must never take the worker down
        log.exception("job failed", extra={"job_id": job_id})
        jobs.finish(job_id, "failed", "Failed", str(err).strip()[:2000] or err.__class__.__name__)
        outcome = "failed"
    log.info("job finished", extra={"job_id": job_id, "outcome": outcome,
                                    "duration_s": round(time.monotonic() - started, 1)})
    llm.job_ref = None
    release_memory()


def release_memory() -> None:
    """Hand freed memory back to the system between jobs.

    Renders and voices leave large freed blocks inside the process; on Linux
    glibc keeps them unless asked, and the next job's child process (Whisper)
    can then run out of memory in Docker.
    """
    import gc

    gc.collect()
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:      # not glibc (macOS): nothing to do
        pass


def _heartbeat(worker_id: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with session_scope() as db:
                row = db.get(Worker, worker_id)
                if row is None:
                    db.add(Worker(id=worker_id))
                else:
                    row.last_seen = utcnow()
        except Exception:  # noqa: BLE001 - keep beating through a database blip
            log.warning("heartbeat failed", exc_info=True)
        stop.wait(HEARTBEAT_SECONDS)


def main() -> None:
    configure("worker")
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    codex_cli.home()   # so `codex login`, run by hand in this container, finds its folder
    interrupted = jobs.recover_interrupted()
    if interrupted:
        log.warning("marked interrupted jobs as failed", extra={"count": interrupted})
    threading.Thread(target=_heartbeat, args=(worker_id, stop), daemon=True, name="heartbeat").start()
    log.info("worker ready", extra={"worker_id": worker_id})

    next_sweep = next_scout = next_due = 0.0
    next_all = time.monotonic() + 60
    while not stop.is_set():
        try:
            job_id = jobs.claim_next(worker_id)
        except Exception:  # noqa: BLE001 - the database may be restarting
            log.warning("could not read the queue", exc_info=True)
            job_id = None
        if job_id is None:
            # Idle: nothing is rendering, so unapproved renders past their grace period can go.
            if time.monotonic() >= next_sweep:
                next_sweep = time.monotonic() + SWEEP_SECONDS
                try:
                    retention.sweep(settings_mod.load())
                except Exception:  # noqa: BLE001 - housekeeping must never stop the worker
                    log.warning("grace period sweep failed", exc_info=True)
            # A scheduled scout is queued like any job, so it waits behind real work.
            if time.monotonic() >= next_scout:
                next_scout = time.monotonic() + SCOUT_CHECK_SECONDS
                try:
                    if scout.queue_if_due(settings_mod.load()):
                        log.info("scheduled campaign scout queued")
                except Exception:  # noqa: BLE001
                    log.warning("could not queue the campaign scout", exc_info=True)
            # Posts handed to Buffer: learn what was sent, and take down videos no longer needed.
            if time.monotonic() >= next_due and buffer.configured():
                everything = time.monotonic() >= next_all
                next_due = time.monotonic() + POSTS_DUE_SECONDS
                if everything:
                    next_all = time.monotonic() + POSTS_ALL_SECONDS
                try:
                    if posting.sync(due_only=not everything):
                        log.info("scheduled posts changed on Buffer")
                except Exception:  # noqa: BLE001
                    log.warning("could not ask Buffer about scheduled posts", exc_info=True)
            stop.wait(1.0)
        else:
            run_job(job_id)
    log.info("worker stopped", extra={"worker_id": worker_id})


if __name__ == "__main__":
    main()
