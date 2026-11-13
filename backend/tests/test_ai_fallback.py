"""Clipping when the AI is unavailable: rules for moments and brief checks, waiting listings, the banner."""

import json

import pytest

from clipper import llm, moments, pipeline, rules
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import AiUsage, Campaign, Clip, Job, Moment, Video
from clipper.settings import Settings

SPEECH = (
    "Um so welcome back everyone. And then we talked about it for a while. "
    "Why do most people lose money on their first 3 investments? "
    "Because they buy what everyone is already buying, and the best deals are gone. "
    "That's why the only rule that matters is to wait. "
    "It is a slow and boring part of the show now. We read some messages from listeners and say thanks. "
    "Here's the secret nobody tells you about 401k plans. "
    "You get free money from your employer, up to 6 percent, and most people never claim it. "
    "So check your plan today."
)


def timed(text: str, rate: float = 2.6) -> list[dict]:
    out, t = [], 0.0
    for w in text.split():
        out.append({"w": w, "s": round(t, 2), "e": round(t + 0.3, 2)})
        t += 1 / rate
    return out


class Rep:
    def __init__(self):
        self.lines = []

    def info(self, text):
        self.lines.append(text)

    warn = info

    def progress(self, *a):
        pass


def test_rules_pick_strong_openings_and_explain_every_point():
    words = timed(SPEECH)
    lines = moments.build_lines(words)
    found, warnings = rules.pick_moments(lines, words, "Investing for beginners", words[-1]["e"], 2, 8, 20)
    assert len(found) == 2 and not warnings
    first = found[0]
    assert first["reason"].startswith(rules.RULES_MARK)
    assert "opens on a question" in first["reason"] or "a curious turn" in first["reason"]
    assert not first["hook"].lower().startswith(("um", "so"))
    assert "#Investing" in first["caption"]
    assert found[0]["start"] >= found[1]["end"] or found[1]["start"] >= found[0]["end"]   # no overlap
    starts = [m["start"] for m in found]
    slow = next(line["start"] for line in lines if line["text"].startswith("It is a slow"))
    assert slow not in starts


def test_rules_check_the_brief_they_can_read():
    clip = {"start": 0, "end": 75, "seconds": 75, "hook": "Free money", "caption": "Check your plan #money"}
    brief = ('Clips must be 30-60 seconds. Include #money and @brand in the caption. '
             'Do not use #ad. Never say "guaranteed".')
    result = rules.check_clip(clip, "this is guaranteed free money", brief)
    by = {c["requirement"]: c["status"] for c in result["checks"]}
    assert by["Length 30 to 60 s"] == "fail" and by["Include #money"] == "pass"
    assert by["Include @brand"] == "fail" and by["No #ad"] == "pass"
    assert by['Never say "guaranteed"'] == "fail" and by["Everything else in the brief"] == "manual"
    assert result["verdict"] == "fail" and result["by"] == "rules"


def make_video(app_env, video_id="vid9"):
    src = app_env / "sources" / video_id
    src.mkdir(parents=True)
    words = timed(SPEECH)
    (src / "transcript.json").write_text(json.dumps({"words": words, "language": "en"}))
    with session_scope() as db:
        db.add(Video(id=video_id, title="Investing for beginners", duration=words[-1]["e"] + 1, width=1920,
                     height=1080, brief=""))
    return video_id


def test_select_falls_back_to_rules_when_the_ai_is_down(app_env, monkeypatch):
    video_id = make_video(app_env)

    def down(*a, **k):
        raise AiUnavailable("Your Anthropic credit balance is too low.")

    monkeypatch.setattr(moments, "ask_claude", down)
    rep = Rep()
    added = pipeline.select(video_id, Settings(), rep, n=2, min_len=8, max_len=20)
    assert added == 2 and any("by rules" in line for line in rep.lines)
    with session_scope() as db:
        assert all(m.reason.startswith(rules.RULES_MARK) for m in db.get(Video, video_id).moments)
    with pytest.raises(AiUnavailable):
        pipeline.select(video_id, Settings(ai_fallback="fail"), Rep(), n=2, min_len=8, max_len=20)


def test_outages_show_in_the_status_until_a_call_works(client, app_env, monkeypatch):
    assert client.get("/api/v1/system/status").json()["ai"]["ok"] is True

    def credit(*a, **k):
        raise AiUnavailable("Your Anthropic credit balance is too low.")

    monkeypatch.setattr(llm.claude, "ask_model", credit)
    with pytest.raises(AiUnavailable):
        llm.ask("moments", "s", "c", moments.Picks)
    ai = client.get("/api/v1/system/status").json()["ai"]
    assert ai["ok"] is False and "credit" in ai["message"] and ai["since"]
    monkeypatch.setattr(llm.claude, "ask_model", lambda *a, **k: (moments.Picks(picks=[]), {"input": 1}))
    llm.ask("moments", "s", "c", moments.Picks)
    assert client.get("/api/v1/system/status").json()["ai"]["ok"] is True


def test_a_pasted_listing_waits_for_the_ai_and_is_read_later(client, app_env, monkeypatch):
    from clipper import campaigns

    def down(*a, **k):
        raise AiUnavailable("Claude is unavailable right now (529): overloaded")

    monkeypatch.setattr(campaigns, "extract", down)
    text = "Summer Sneakers Campaign\nPays $2 per 1,000 views on TikTok. 30-60 second clips."
    out = client.post("/api/v1/campaigns", json={"url": "https://example.com/c/1", "text": text}).json()
    assert out["read_by"] == "waiting" and out["title"] == "Summer Sneakers Campaign"
    assert any("Waiting for the AI" in f for f in out["flags"])

    def read(text, theme):
        llm._record("campaign", "quick", "anthropic", "claude-haiku-5-5", {"input": 1000, "output": 200}, 0.0)
        return campaigns.Terms.model_validate({**_terms(), "title": "Summer Sneakers"})

    monkeypatch.setattr(campaigns, "extract", read)
    again = client.post(f"/api/v1/campaigns/{out['id']}/read").json()
    assert again["read_by"] == "ai" and again["title"] == "Summer Sneakers"
    with session_scope() as db:
        assert db.query(AiUsage).filter_by(ref=f"campaign:{out['id']}").count() == 1


def _terms() -> dict:
    from clipper import campaigns

    fields = {}
    for name, field in campaigns.Terms.model_fields.items():
        ann = str(field.annotation)
        fields[name] = [] if "list" in ann else None if "None" in ann else 0 if "int" in ann or "float" in ann \
            else "clipping" if name == "kind" else "cpm" if name == "payout_model" else "open" \
            if name == "listing_status" else "provided" if name == "source_material" else "none" \
            if name == "restricted_category" else "USD" if name == "currency" else "x"
    return fields


def test_a_clipping_video_costs_its_campaign_and_its_own_work(client, app_env):
    with session_scope() as db:
        campaign = Campaign(title="C")
        db.add(campaign)
        db.flush()
        db.add(Video(id="v1", title="t", duration=60, width=1920, height=1080, campaign_id=campaign.id))
        campaign_id = campaign.id
    with llm.attribute(f"campaign:{campaign_id}"):
        llm._record("campaign", "quick", "anthropic", "claude-haiku-5-5", {"input": 4000, "output": 500}, 0.0)
    llm.job_ref = "video:v1"
    try:
        llm._record("moments", "writing", "anthropic", "claude-sonnet-5-5", {"input": 20000, "output": 3000}, 0.0)
    finally:
        llm.job_ref = None
    report = client.get("/api/v1/system/costs", params={"video_id": "v1"}).json()
    assert [line["step"] for line in report["lines"]] == ["Campaign", "This video"]
    assert report["lines"][1]["label"] == "Choosing clip moments" and report["total"] > 0.05


def test_a_failed_clipping_project_can_be_deleted(client, app_env):
    video_id = make_video(app_env, "broken")
    with session_scope() as db:
        job = Job(kind="process", label="x", params={}, video_id=video_id, stages=["download"], state="failed",
                  message="Failed", error="Transcription failed")
        db.add(job)
        db.flush()
        job_id = job.id
    listed = {v["id"]: v for v in client.get("/api/v1/videos").json()}
    assert listed[video_id]["failed"] and listed[video_id]["failed_job_id"] == job_id
    assert "Transcription failed" in listed[video_id]["error"]
    assert client.delete(f"/api/v1/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/v1/videos/{video_id}").status_code == 404
    assert not (app_env / "sources" / video_id).exists()


def test_a_finished_or_running_job_is_not_deleted_and_clips_are_kept(client, app_env):
    video_id = make_video(app_env, "good")
    with session_scope() as db:
        running = Job(kind="select", label="x", params={}, video_id=video_id, stages=["select"], state="running")
        failed = Job(kind="check", label="x", params={}, video_id=video_id, stages=["check"], state="failed")
        db.add_all([running, failed])
        moment = Moment(video_id=video_id, position=1, start_s=0, end_s=10)
        db.add(moment)
        db.flush()
        db.add(Clip(video_id=video_id, moment_id=moment.id, path="/nowhere.mp4", seconds=10, size_bytes=1))
        db.flush()
        ids = running.id, failed.id
    assert client.delete(f"/api/v1/jobs/{ids[0]}").status_code == 409
    assert client.delete(f"/api/v1/jobs/{ids[1]}").status_code == 204
    assert client.get(f"/api/v1/videos/{video_id}").status_code == 200       # it has a clip: kept
