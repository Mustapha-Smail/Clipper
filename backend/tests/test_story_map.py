from copy import deepcopy

import pytest
from sqlalchemy import inspect, select

from clipper import llm, prompts, worker
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import Idea, Job, JobEvent, Script, StudioEvent
from clipper.jobs import Reporter
from clipper.studio import script as script_mod
from clipper.studio import story_map

NOTES = "The mechanism moves heat rather than making cold.[1] The rear coils release that heat.[2]"
SOURCES = [
    {"n": 1, "url": "https://example.test/mechanism", "title": "The mechanism"},
    {"n": 2, "url": "https://example.test/coils", "title": "The coils"},
]


def raw_map() -> dict:
    return {
        "template": "story_loop",
        "opening": {
            "context": "A person opens a warm fridge.",
            "lean": "Their food depends on what happens next.",
            "turn": "The fridge does not manufacture cold.",
            "promise": "Explain where the warmth goes.",
            "first_visual": "A person pushes a warm carton into a fridge.",
            "source_numbers": [1],
        },
        "beats": [
            {"id": "where_heat", "purpose": "question", "summary": "Ask where the heat goes.",
             "source_numbers": [1], "opens": ["heat_path"], "resolves": [],
             "visual_event": "Heat arrows leave a carton."},
            {"id": "rear_coils", "purpose": "answer", "summary": "The coils release it into the room.",
             "source_numbers": [2], "opens": [], "resolves": ["heat_path"],
             "visual_event": "A hand feels warm rear coils."},
        ],
        "ending": {"payoff": "The fridge moves heat from the food into the room.",
                   "callback": "The carton cools while the coils warm.", "source_numbers": [1, 2]},
    }


def add_script() -> int:
    with session_scope() as db:
        idea = Idea(title="Your fridge does not make cold", theme="Everyday things", hook="Your fridge is warm",
                    expected="It creates cold air", answer="It moves heat", angle="Follow the heat", format="single",
                    beats=[{"text": "Follow heat to the coils", "short": True}])
        db.add(idea)
        db.flush()
        script = Script(idea_id=idea.id, title=idea.title, theme=idea.theme, format="single", target_minutes=6,
                        video="long", target_seconds=50, research={"notes": NOTES, "sources": SOURCES, "searches": 2},
                        sections=[])
        db.add(script)
        db.flush()
        return script.id


def store_map(script_id: int) -> None:
    """Store a current map without an AI call."""
    with session_scope() as db:
        script = db.get(Script, script_id)
        input_hash, input_parts = story_map.input_fingerprint(script)
        script.story_map = {"version": 1, "input_hash": input_hash, "input_parts": input_parts,
                            "made_at": "2026-11-19T10:00:00+00:00", "by": "ai", **raw_map()}


class Capture(Reporter):
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def warn(self, message):
        self.warnings.append(message)


@pytest.mark.parametrize(("change", "reason"), [
    ("title", "title changed"),
    ("format", "format changed"),
    ("target", "target length changed"),
    ("audience", "audience setting changed"),
    ("idea_hook", "idea hook"),
    ("idea_expected", "idea hook"),
    ("idea_answer", "idea hook"),
    ("idea_angle", "idea hook"),
    ("idea_beats", "idea hook"),
    ("research_notes", "research notes changed"),
    ("research_sources", "research source list changed"),
])
def test_input_hash_is_stable_and_every_input_reports_why_it_is_stale(app_env, monkeypatch, change, reason):
    script_id = add_script()
    with session_scope() as db:
        script = db.get(Script, script_id)
        first_hash, first_parts = story_map.input_fingerprint(script)
        assert story_map.input_fingerprint(script) == (first_hash, first_parts)
        saved = {"input_hash": first_hash, "input_parts": first_parts, **raw_map()}
        if change == "title":
            script.title += " now"
        elif change == "format":
            script.format = "list"
        elif change == "target":
            script.target_minutes += 1
        elif change == "audience":
            monkeypatch.setattr(story_map.plain, "audience", lambda: "specialists")
        elif change.startswith("idea_"):
            field = change.removeprefix("idea_")
            setattr(script.idea, field, ([{"text": "A different beat", "short": False}]
                                         if field == "beats" else getattr(script.idea, field) + " changed"))
        elif change == "research_notes":
            script.research = {**script.research, "notes": NOTES + " More."}
        else:
            changed_sources = deepcopy(SOURCES)
            changed_sources[0]["title"] = "A corrected source title"
            script.research = {**script.research, "sources": changed_sources}
        current, reasons = story_map.status(script, saved)
    assert not current and any(reason in item.lower() for item in reasons)


def test_clean_drops_unknown_sources_and_reports_each_location():
    proposed = raw_map()
    proposed["opening"]["source_numbers"] = [1, 99, 1]
    proposed["beats"][0]["source_numbers"] = [88, 1]
    proposed["ending"]["source_numbers"] = [2, 77]
    cleaned = story_map.clean(proposed, {1, 2})
    assert not cleaned.problems
    assert cleaned.value["opening"]["source_numbers"] == [1]
    assert cleaned.value["beats"][0]["source_numbers"] == [1]
    assert cleaned.value["ending"]["source_numbers"] == [2]
    assert len(cleaned.warnings) == 3 and all("dropped unknown source" in line for line in cleaned.warnings)


@pytest.mark.parametrize(("change", "problem"), [
    (lambda value: value["beats"].append(deepcopy(value["beats"][0])), "repeats beat id"),
    (lambda value: value["beats"][0].update(opens=[], resolves=["later"]), "before it is open"),
    (lambda value: value["beats"][1].update(resolves=[]), "Unresolved loops"),
    (lambda value: value["beats"][0].update(opens=["one", "two", "three"]), "at most two"),
    (lambda value: value["beats"][1].update(opens=["heat_path"]), "repeats loop id"),
])
def test_loop_and_id_problems_are_explicit(change, problem):
    proposed = raw_map()
    change(proposed)
    assert any(problem in line for line in story_map.clean(proposed, {1, 2}).problems)


def test_run_stores_code_owned_metadata_and_reports_dropped_sources(app_env, monkeypatch):
    script_id = add_script()
    proposed = raw_map()
    proposed["opening"]["source_numbers"].append(99)
    monkeypatch.setattr(llm, "ask", lambda *args, **kwargs: story_map.Draft.model_validate(proposed))
    rep = Capture()
    story_map.run(script_id, rep)
    with session_scope() as db:
        script = db.get(Script, script_id)
        assert script.story_map["version"] == 1 and script.story_map["by"] == "ai"
        assert script.story_map["opening"]["source_numbers"] == [1]
        assert story_map.status(script) == (True, [])
        events = db.scalars(select(StudioEvent).where(StudioEvent.script_id == script_id)).all()
        assert any(event.kind == "story_map" for event in events)
    assert any("dropped unknown source number 99" in line for line in rep.warnings)


def test_build_ignores_ai_parallel_fields_for_an_explain_map(app_env, monkeypatch):
    script_id = add_script()
    proposed = raw_map()
    proposed["story_shape"] = "explain"
    proposed["parallel"] = {
        "shared_start": "same timer",
        "shared_clock": "one minute",
        "lanes": [
            {"id": "a", "label": "A", "rule": "one", "promised_consequence": "first"},
            {"id": "b", "label": "B", "rule": "two", "promised_consequence": "second"},
        ],
    }
    for beat in proposed["beats"]:
        beat.update(lane="a", checkpoint="heat")
    calls = []
    monkeypatch.setattr(
        llm,
        "ask",
        lambda *args, **kwargs: calls.append((args, kwargs)) or story_map.Draft.model_validate(proposed),
    )
    with session_scope() as db:
        script = db.get(Script, script_id)
        script.story_shape = "explain"
        result = story_map.build(script, Capture())
    assert len(calls) == 1
    assert "parallel" not in result
    assert all("lane" not in beat and "checkpoint" not in beat for beat in result["beats"])


def test_build_repairs_an_unresolved_loop_once(app_env, monkeypatch):
    script_id = add_script()
    broken, repaired = raw_map(), raw_map()
    broken["beats"][1]["resolves"] = []
    answers = iter([broken, repaired])
    calls = []

    def ask(*args, **kwargs):
        calls.append((args, kwargs))
        return story_map.Draft.model_validate(next(answers))

    monkeypatch.setattr(llm, "ask", ask)
    rep = Capture()
    with session_scope() as db:
        result = story_map.build(db.get(Script, script_id), rep)
    assert len(calls) == 2
    assert "Unresolved loops: heat_path." in calls[1][0][2]
    assert result["beats"][1]["resolves"] == ["heat_path"]
    assert any("Repairing" in message for message in rep.infos)


def test_build_still_refuses_a_failed_repair(app_env, monkeypatch):
    script_id = add_script()
    broken = raw_map()
    broken["beats"][1]["resolves"] = []
    monkeypatch.setattr(llm, "ask", lambda *args, **kwargs: story_map.Draft.model_validate(broken))
    with session_scope() as db, pytest.raises(RuntimeError, match="Unresolved loops: heat_path"):
        story_map.build(db.get(Script, script_id), Capture())


def test_ai_outage_fails_the_new_script_pipeline_and_is_visible(client, monkeypatch):
    with session_scope() as db:
        idea = Idea(title="T", theme="Everyday", hook="H", expected="E", answer="A", angle="X",
                    format="single", beats=[])
        db.add(idea)
        db.flush()
        idea_id = idea.id

    def fake_research(script_id, rep):
        with session_scope() as db:
            db.get(Script, script_id).research = {"notes": NOTES, "sources": SOURCES, "searches": 1}

    monkeypatch.setattr(script_mod, "research", fake_research)
    monkeypatch.setattr(llm, "ask", lambda *args, **kwargs: (_ for _ in ()).throw(AiUnavailable("plan limit")))
    started = client.post(f"/api/v1/ideas/{idea_id}/script", json={}).json()
    worker.run_job(started["job"]["id"])
    with session_scope() as db:
        job = db.get(Job, started["job"]["id"])
        warnings = db.scalars(select(JobEvent).where(JobEvent.job_id == job.id,
                                                     JobEvent.level == "warning")).all()
        assert job.state == "failed" and "Could not build the story map" in job.error
        assert any("Story map unavailable" in event.message for event in warnings)
        assert db.get(Script, started["id"]).story_map is None


def test_writer_receives_current_story_map_and_research(app_env, monkeypatch):
    script_id = add_script()
    store_map(script_id)
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high"):
        seen.update(task=task, content=content)
        return script_mod.Draft(title="Ignored map title", sections=[
            script_mod.Section(heading="Story", text="The heat leaves through the coils.", short=False)])

    monkeypatch.setattr(llm, "ask", fake_ask)
    script_mod.write(script_id, Capture())
    assert seen["task"] == "script_write"
    assert "<story_map>" in seen["content"] and raw_map()["opening"]["promise"] in seen["content"]
    assert f"<research_notes>\n{NOTES}\n</research_notes>" in seen["content"]
    with session_scope() as db:
        assert db.get(Script, script_id).title == "Your fridge does not make cold"


def test_writer_refuses_a_stale_map_before_calling_ai(app_env, monkeypatch):
    script_id = add_script()
    store_map(script_id)
    with session_scope() as db:
        db.get(Script, script_id).title = "A changed title"
    called = False

    def fake_ask(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(llm, "ask", fake_ask)
    with pytest.raises(RuntimeError, match="story map is out of date.*title changed"):
        script_mod.write(script_id, Capture())
    assert not called


def test_rewrites_receive_promise_and_loop_context(app_env, monkeypatch):
    script_id = add_script()
    store_map(script_id)
    with session_scope() as db:
        db.get(Script, script_id).sections = [
            {"key": "open", "heading": "Open", "text": "Where does the heat go?", "short": True},
            {"key": "answer", "heading": "Answer", "text": "The coils release it.", "short": False},
        ]
    calls = []

    def fake_ask(task, system, content, output_format, effort="high"):
        calls.append((task, content))
        if output_format is script_mod.Rewrite:
            return script_mod.Rewrite(text="Follow the heat.")
        return script_mod.SpokenScript(sections=[
            script_mod.SpokenSection(key="open", text="Where does that heat go?"),
            script_mod.SpokenSection(key="answer", text="The coils release it."),
        ])

    monkeypatch.setattr(llm, "ask", fake_ask)
    with session_scope() as db:
        script_mod.rewrite_section(db.get(Script, script_id), "open", "shorter")
    script_mod.make_spoken(script_id, Capture())
    script_mod.make_simpler(script_id, Capture())
    assert [task for task, _ in calls] == ["rewrite", "script_write", "simplify"]
    for _, content in calls:
        assert "<story_map>" in content
        assert "Explain where the warmth goes." in content
        assert "heat_path" in content


def test_story_map_api_round_trip_rebuild_confirmation_and_idle_guard(client, monkeypatch):
    script_id = add_script()
    assert client.get(f"/api/v1/scripts/{script_id}").json()["story_map"] is None
    queued = client.post(f"/api/v1/scripts/{script_id}/story-map", json={})
    assert queued.status_code == 202 and queued.json()["stages"] == ["story_map"]
    assert client.put(f"/api/v1/scripts/{script_id}/story-map", json=raw_map()).status_code == 409
    with session_scope() as db:
        db.get(Job, queued.json()["id"]).state = "cancelled"

    monkeypatch.setattr(llm, "ask", lambda *args, **kwargs: story_map.Draft.model_validate(raw_map()))
    story_map.run(script_id, Capture())
    fetched = client.get(f"/api/v1/scripts/{script_id}").json()["story_map"]
    assert fetched["current"] and fetched["template"] == "story_loop" and not fetched["problems"]
    assert client.post(f"/api/v1/scripts/{script_id}/story-map", json={}).status_code == 409
    rebuild = client.post(f"/api/v1/scripts/{script_id}/story-map", json={"confirm": True})
    assert rebuild.status_code == 202
    with session_scope() as db:
        db.get(Job, rebuild.json()["id"]).state = "cancelled"

    edited = raw_map()
    edited["template"] = "mechanism"
    saved = client.put(f"/api/v1/scripts/{script_id}/story-map", json=edited)
    assert saved.status_code == 200
    result = saved.json()["story_map"]
    assert result["by"] == "you" and result["template"] == "mechanism" and result["current"]
    with session_scope() as db:
        assert any(e.kind == "story_map_edit" for e in db.scalars(
            select(StudioEvent).where(StudioEvent.script_id == script_id)).all())
    invalid = raw_map()
    invalid["ending"]["source_numbers"] = [200]
    assert client.put(f"/api/v1/scripts/{script_id}/story-map", json=invalid).status_code == 422


def test_legacy_redraft_builds_a_map_and_stale_redraft_needs_explicit_rebuild(client, monkeypatch):
    script_id = add_script()
    first = client.post(f"/api/v1/scripts/{script_id}/redraft", json={}).json()
    assert first["stages"] == ["story_map", "write", "factcheck", "hold"]
    with session_scope() as db:
        db.get(Job, first["id"]).state = "cancelled"
    monkeypatch.setattr(llm, "ask", lambda *args, **kwargs: story_map.Draft.model_validate(raw_map()))
    story_map.run(script_id, Capture())
    with session_scope() as db:
        db.get(Script, script_id).title = "A changed title"
    stale = client.post(f"/api/v1/scripts/{script_id}/redraft", json={})
    assert stale.status_code == 409 and "title changed" in stale.json()["detail"].lower()
    rebuild = client.post(f"/api/v1/scripts/{script_id}/redraft", json={"rebuild_story_map": True})
    assert rebuild.status_code == 202 and rebuild.json()["stages"][0] == "story_map"


def test_story_map_migration_goes_down_and_up_on_sqlite(app_env):
    from alembic import command

    from clipper import db

    command.downgrade(db._alembic_config(), "e7a4c19b3f52")
    assert "story_map" not in {column["name"] for column in inspect(db.get_engine()).get_columns("scripts")}
    command.upgrade(db._alembic_config(), "head")
    assert "story_map" in {column["name"] for column in inspect(db.get_engine()).get_columns("scripts")}


def test_story_map_prompt_and_task_are_registered():
    assert llm.TASKS["story_map"][0] == "writing"
    assert not prompts.problems("script_story_map", prompts.default("script_story_map"))
    assert "no more than two" in story_map.PROMPT.format(plain="Plain rules")
    for name in ("script_research", "script_write", "script_rewrite", "script_make_spoken",
                 "script_make_simpler"):
        assert not prompts.problems(name, prompts.default(name))


def test_form_and_story_shape_are_fingerprinted_with_specific_stale_reasons(app_env):
    script_id = add_script()
    with session_scope() as db:
        script = db.get(Script, script_id)
        first_hash, first_parts = story_map.input_fingerprint(script)
        saved = {"input_hash": first_hash, "input_parts": first_parts, **raw_map()}
        script.form = "short"
        current, reasons = story_map.status(script, saved)
        assert not current and any("form" in reason.lower() for reason in reasons)
        script.form = first_parts["form"]
        script.story_shape = "parallel"
        current, reasons = story_map.status(script, saved)
        assert not current and any("shape" in reason.lower() for reason in reasons)


def _parallel_map() -> dict:
    value = raw_map()
    value.update({
        "story_shape": "parallel",
        "parallel": {
            "shared_start": "Two identical fridges start warm.",
            "shared_clock": "The same ten-minute clock runs in both lanes.",
            "lanes": [
                {"id": "a", "label": "No airflow", "rule": "No fan", "promised_consequence": "It warms."},
                {"id": "b", "label": "Airflow", "rule": "Fan on", "promised_consequence": "It cools."},
            ],
        },
    })
    value["beats"] = [
        {"id": "a_start", "purpose": "question", "summary": "A starts.", "visual_event": "A starts.",
         "source_numbers": [1], "lane": "a", "checkpoint": "start"},
        {"id": "b_start", "purpose": "question", "summary": "B starts.", "visual_event": "B starts.",
         "source_numbers": [1], "lane": "b", "checkpoint": "start"},
        {"id": "a_end", "purpose": "answer", "summary": "A ends.", "visual_event": "A ends.",
         "source_numbers": [2], "lane": "a", "checkpoint": "end"},
        {"id": "b_end", "purpose": "answer", "summary": "B ends.", "visual_event": "B ends.",
         "source_numbers": [2], "lane": "b", "checkpoint": "end"},
    ]
    return value


def test_parallel_shape_requires_two_safe_lanes_and_two_shared_checkpoints():
    cleaned = story_map.clean(_parallel_map(), {1, 2})
    assert not cleaned.problems
    broken = _parallel_map()
    broken["beats"][3]["lane"] = "c"
    assert any("unknown lane 'c'" in problem for problem in story_map.clean(broken, {1, 2}).problems)
    broken = _parallel_map()
    broken["beats"][3]["checkpoint"] = "other"
    assert any("at least two checkpoints" in problem for problem in story_map.clean(broken, {1, 2}).problems)


def test_non_parallel_rejects_lane_metadata_and_shape_prompts_are_original():
    broken = raw_map()
    broken["story_shape"] = "explain"
    broken["beats"][0]["lane"] = "a"
    assert any("only allowed for the parallel" in problem
               for problem in story_map.clean(broken, {1, 2}).problems)
    for shape in ("parallel", "explain", "scorecard", "investigation"):
        prompt = prompts.render(f"story_shape_{shape}").lower()
        assert "do not imitate" in prompt or "not a copy" in prompt
        assert "unsupported" in prompt or "evidence" in prompt
