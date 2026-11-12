"""Costs per video, and Shorts-only ideas."""

from clipper import llm
from clipper.db import session_scope
from clipper.db.models import AiUsage, Idea, Script, Storyboard
from clipper.studio import ideas as ideas_mod
from clipper.studio import script as script_mod


def video(seconds: int, views: int) -> dict:
    return {"duration_s": seconds, "views": views, "subscribers": 1000, "title": "t", "channel": "c",
            "published": "2025-01-01T00:00:00Z"}


def test_calls_are_tagged_and_added_up_per_video(client, app_env):
    with session_scope() as db:
        idea = Idea(title="Fridges", theme="t")
        db.add(idea)
        db.flush()
        script = Script(idea_id=idea.id, title="Fridges", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, format="short", title="x", words=[], scenes=[])
        db.add(board)
        db.flush()
        ids = idea.id, script.id, board.id
    llm.job_ref = f"storyboard:{ids[2]}"           # the worker sets this for its job
    try:
        llm._record("storyboard", "writing", "anthropic", "claude-sonnet-5-5", {"input": 1000, "output": 1000}, 0.0)
    finally:
        llm.job_ref = None
    with llm.attribute(f"idea:{ids[0]}"):          # an API request
        llm._record("idea_check", "quick", "anthropic", "claude-haiku-5-5", {"input": 1000, "output": 100}, 0.0)
    llm._record("themes", "quick", "anthropic", "claude-haiku-5-5", {"input": 10, "output": 10}, 0.0)
    with session_scope() as db:
        assert {r.ref for r in db.query(AiUsage)} == {f"storyboard:{ids[2]}", f"idea:{ids[0]}", None}
    report = client.get("/api/v1/system/costs", params={"storyboard_id": ids[2]}).json()
    assert [line["step"] for line in report["lines"]] == ["Idea", "This video"]
    assert report["total"] == round(sum(line["cost"] for line in report["lines"]), 4) and report["total"] > 0.01
    assert report["lines"][1]["label"] == "Planning storyboards" and "ElevenLabs" in report["note"]
    by_script = client.get("/api/v1/system/costs", params={"script_id": ids[1]}).json()
    assert {line["step"] for line in by_script["lines"]} == {"Idea", "Videos"}
    assert client.get("/api/v1/system/costs").status_code == 400


def test_length_is_suggested_from_the_check():
    idea = Idea(title="x", format="single", beats=[{"text": "a"}, {"text": "b"}, {"text": "c"}])
    idea.research = {"videos": [video(600, 900_000), video(480, 500_000), video(720, 300_000), video(30, 50_000)]}
    long = ideas_mod.suggest_length(idea)
    assert (long["video"], long["minutes"]) == ("long", 10) and "8 to 12 min" in long["reason"]
    idea.research = {"videos": [video(600, 100_000), video(40, 2_000_000)]}
    short = ideas_mod.suggest_length(idea)
    assert short["video"] == "short" and short["seconds"] == ideas_mod.SHORT_SECONDS
    assert "Shorts lead" in short["reason"]
    idea.research = None
    idea.format, idea.beats = "list", [{"text": str(i)} for i in range(7)]
    assert ideas_mod.suggest_length(idea)["minutes"] == 11


def test_a_shorts_only_idea_gets_a_one_section_script(client, app_env, monkeypatch):
    with session_scope() as db:
        idea = Idea(title="Why toast lands butter side down", theme="t", hook="h", answer="a", beats=[])
        db.add(idea)
        db.flush()
        idea_id = idea.id
    assert client.patch(f"/api/v1/ideas/{idea_id}", json={"video": "short"}).json()["video"] == "short"
    made = client.post(f"/api/v1/ideas/{idea_id}/script", json={"target_seconds": 45}).json()
    assert (made["video"], made["target_seconds"]) == ("short", 45)
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        seen["brief"] = content
        return script_mod.Draft(title="Toast", sections=[script_mod.Section(heading="Hook", text="One.", short=False),
                                          script_mod.Section(heading="Payoff", text="Two.", short=False)])
    monkeypatch.setattr(llm, "ask", fake_ask)
    with session_scope() as db:
        db.get(Script, made["id"]).research = {"notes": "n", "sources": [], "searches": 0}
    from clipper.jobs import ConsoleReporter
    script_mod.write(made["id"], ConsoleReporter())
    assert "YouTube Short only" in seen["brief"] and "about 45 seconds" in seen["brief"]
    with session_scope() as db:
        sections = db.get(Script, made["id"]).sections
    assert len(sections) == 1 and sections[0]["short"] and sections[0]["text"] == "One.\n\nTwo."


def test_features_default_to_studio_only(client):
    import pytest

    from clipper.settings import Settings
    assert (Settings().feature_studio, Settings().feature_clipping) == (True, False)
    with pytest.raises(ValueError):
        Settings(feature_studio=False, feature_clipping=False)
    assert client.get("/api/v1/system/status").json()["features"] == {"clipping": False, "studio": True}
