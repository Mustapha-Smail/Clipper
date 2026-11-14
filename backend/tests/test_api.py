from clipper.db import session_scope
from clipper.db.models import Job


def test_health_and_status(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    status = client.get("/api/v1/system/status").json()
    assert status["database"] == "sqlite" and status["worker_online"] is False
    assert status["queued"] == 0 and "ffmpeg" in status["tools"]


def test_settings_are_validated_and_persisted(client):
    current = client.get("/api/v1/settings").json()["settings"]
    bad = client.put("/api/v1/settings", json={**current, "min_len": 60, "max_len": 30})
    assert bad.status_code == 400 and "longest clip" in bad.json()["detail"]
    bad = client.put("/api/v1/settings", json={**current, "highlight_color": "yellow"})
    assert bad.status_code == 400
    ok = client.put("/api/v1/settings", json={**current, "clips_per_video": 3, "auto_compliance": False})
    assert ok.status_code == 200
    assert client.get("/api/v1/settings").json()["settings"]["clips_per_video"] == 3


def test_adding_a_video_queues_the_whole_pipeline(client):
    job = client.post("/api/v1/videos", json={"src": "https://example.com/watch?v=1", "n": 2})
    assert job.status_code == 202
    body = job.json()
    assert body["state"] == "queued" and body["kind"] == "process"
    assert body["stages"] == ["download", "transcribe", "subtitles", "select", "render", "check"]
    events = client.get(f"/api/v1/jobs/{body['id']}/events").json()
    assert [e["message"] for e in events] == ["Queued"]
    assert client.get("/api/v1/system/status").json()["queued"] == 1


def test_brief_check_stage_follows_the_setting(client):
    current = client.get("/api/v1/settings").json()["settings"]
    client.put("/api/v1/settings", json={**current, "auto_compliance": False})
    job = client.post("/api/v1/videos", json={"src": "https://example.com/v"}).json()
    assert "check" not in job["stages"]


def test_queued_job_can_be_cancelled_then_retried(client):
    job = client.post("/api/v1/videos", json={"src": "https://example.com/v"}).json()
    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel").json()
    assert cancelled["state"] == "cancelled"
    assert client.post(f"/api/v1/jobs/{job['id']}/cancel").status_code == 409
    again = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert again.status_code == 202 and again.json()["id"] != job["id"]


def test_video_detail_and_moment_editing(client, video):
    detail = client.get(f"/api/v1/videos/{video}").json()
    assert [m["title"] for m in detail["moments"]] == ["First", "Second"]
    assert detail["effective_subtitle_mode"] == "ignore" and detail["subtitles"] is None
    first, second = detail["moments"]

    edited = client.put(f"/api/v1/videos/{video}/moments", json={"moments": [
        {"id": second["id"], "start": 61, "end": 96, "title": "Second, renamed"},
        {"start": 120, "end": 150, "title": "Added by hand"},
    ]}).json()
    assert [m["title"] for m in edited["moments"]] == ["Second, renamed", "Added by hand"]
    assert edited["moments"][0]["id"] == second["id"]        # kept, not recreated
    assert first["id"] not in [m["id"] for m in edited["moments"]]

    bad = client.put(f"/api/v1/videos/{video}/moments", json={"moments": [{"start": 50, "end": 20}]})
    assert bad.status_code == 400 and "end must come after" in bad.json()["detail"]
    late = client.put(f"/api/v1/videos/{video}/moments", json={"moments": [{"start": 290, "end": 400}]})
    assert late.status_code == 400


def test_brief_and_subtitle_choice_are_saved(client, video):
    patched = client.patch(f"/api/v1/videos/{video}", json={"brief": " Use #tag ", "subtitle_mode": "keep"}).json()
    assert patched["brief"] == "Use #tag" and patched["effective_subtitle_mode"] == "keep"
    reset = client.patch(f"/api/v1/videos/{video}", json={"subtitle_mode": "default"}).json()
    assert reset["subtitle_mode"] is None and reset["effective_subtitle_mode"] == "ignore"


def test_clip_format_follows_the_setting_unless_the_video_chooses(client, video):
    detail = client.get(f"/api/v1/videos/{video}").json()
    assert detail["clip_format"] is None and detail["effective_clip_format"] == "vertical"
    chosen = client.patch(f"/api/v1/videos/{video}", json={"clip_format": "horizontal"}).json()
    assert chosen["clip_format"] == "horizontal" and chosen["effective_clip_format"] == "horizontal"
    assert chosen["brief"] == detail["brief"]              # nothing else moved
    reset = client.patch(f"/api/v1/videos/{video}", json={"clip_format": "default"}).json()
    assert reset["clip_format"] is None and reset["effective_clip_format"] == "vertical"
    assert client.patch(f"/api/v1/videos/{video}", json={"clip_format": "square"}).status_code == 422

    settings = client.get("/api/v1/settings").json()["settings"]
    assert client.put("/api/v1/settings", json={**settings, "clip_format": "horizontal"}).status_code == 200
    assert client.get(f"/api/v1/videos/{video}").json()["effective_clip_format"] == "horizontal"

    job = client.post("/api/v1/videos", json={"src": "https://example.com/v", "clip_format": "horizontal"}).json()
    with session_scope() as db:
        assert db.get(Job, job["id"]).params["clip_format"] == "horizontal"


def test_actions_are_refused_while_a_job_is_active(client, video):
    assert client.post(f"/api/v1/videos/{video}/render", json={}).status_code == 202
    busy = client.post(f"/api/v1/videos/{video}/select", json={})
    assert busy.status_code == 409
    assert client.get("/api/v1/videos").json()[0]["busy"] is True
    assert client.delete(f"/api/v1/videos/{video}").status_code == 409


def test_check_needs_a_brief_and_clips(client, video):
    assert client.post(f"/api/v1/videos/{video}/check", json={}).status_code == 400
    client.patch(f"/api/v1/videos/{video}", json={"brief": "Rules"})
    no_clips = client.post(f"/api/v1/videos/{video}/check", json={})
    assert no_clips.status_code == 400 and "no rendered clips" in no_clips.json()["detail"]


def test_unknown_things_are_404(client):
    assert client.get("/api/v1/videos/nope").status_code == 404
    assert client.get("/api/v1/jobs/999").status_code == 404
    assert client.patch("/api/v1/clips/999", json={"status": "approved"}).status_code == 404
    assert client.get("/api/v1/does-not-exist").status_code == 404
