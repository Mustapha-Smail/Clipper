from clipper import jobs
from clipper.db import session_scope
from clipper.db.models import Job, JobEvent


def enqueue(kind="render", video_id="vid1"):
    with session_scope() as db:
        return jobs.enqueue(db, kind, "label", {}, video_id).id


def test_jobs_are_claimed_oldest_first_and_only_once(app_env):
    first, second = enqueue(), enqueue()
    assert jobs.claim_next("w1") == first
    assert jobs.claim_next("w1") == second
    assert jobs.claim_next("w1") is None
    with session_scope() as db:
        job = db.get(Job, first)
        assert job.state == "running" and job.worker_id == "w1" and job.started_at is not None


def test_running_jobs_are_failed_when_a_worker_restarts(app_env):
    job_id = enqueue()
    jobs.claim_next("w1")
    assert jobs.recover_interrupted() == 1
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job.state == "failed" and "Interrupted" in job.error


def test_reporter_records_stage_progress_and_log(app_env):
    job_id = enqueue()
    jobs.claim_next("w1")
    rep = jobs.JobReporter(job_id)
    rep.stage("render")
    rep.progress(0.5, "Rendering clip 1 of 2")
    rep.warn("careful")
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert (job.stage, job.progress, job.message) == ("render", 0.5, "Rendering clip 1 of 2")
        events = [(e.level, e.message) for e in db.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.id)]
    assert events == [("info", "Queued"), ("info", "Rendering clips"), ("warning", "careful")]


def test_cancel_request_stops_a_running_job_at_the_next_report(app_env):
    import pytest

    job_id = enqueue()
    jobs.claim_next("w1")
    rep = jobs.JobReporter(job_id)
    with session_scope() as db:
        jobs.request_cancel(db, db.get(Job, job_id))
    with pytest.raises(jobs.JobCancelled):
        rep.progress(0.1)


def test_has_active_only_counts_unfinished_jobs(app_env):
    job_id = enqueue(video_id="v9")
    with session_scope() as db:
        assert jobs.has_active(db, "v9")
    jobs.finish(job_id, "succeeded", "Done")
    with session_scope() as db:
        assert not jobs.has_active(db, "v9")
