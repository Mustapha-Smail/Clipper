import json

import pytest

from clipper import llm, prompts
from clipper.db import session_scope
from clipper.db.models import Job, Script, Setting, Storyboard, VoiceTake
from clipper.jobs import ConsoleReporter
from clipper.settings import KEY, Settings
from clipper.studio import learning, story_map, storyboard, voice, world3d
from clipper.studio import script as script_mod

LEGAL_PLAN = """
0 shot "A nail and hammer establish the scale"
0 add plank center large as plank
1 add nail top small as nail
2 add hammer right medium as hammer
3 strike hammer nail
4 scale nail 100
5 camera wide nail
6 duplicate nail left as nail_copy
7 move hammer top
8 rotate hammer left
9 lift hammer
10 drop hammer nail
11 label nail "NAIL"
12 arrow hammer nail "FORCE"
13 measure nail nail_copy "100x"
14 hide nail_copy
15 show nail_copy
16 highlight nail
17 dim plank
18 hold
19 remove nail_copy
20 clear
21 add pile center large as pile
22 add ram top medium as ram
23 add anvil center medium as anvil
24 add cylinder top medium as cylinder
25 add guide_rods left large as guides
26 assemble pile_hammer pile ram anvil cylinder guides as machine
27 explode machine
28 cutaway machine
29 section machine
30 cycle machine 3
31 camera orbit machine
"""


def test_every_legal_operation_parses_and_round_trips_canonically():
    shots, issues = world3d.parse_plan(LEGAL_PLAN, 40)
    assert issues == []
    assert {line.verb for shot in shots for line in world3d.parsed(shot)} == world3d.VERBS - {"shot"}
    canonical = world3d.to_text(shots)
    again, issues = world3d.parse_plan(canonical, 40)
    assert issues == [] and world3d.to_text(again) == canonical
    report = world3d.complexity(shots)
    assert report["units"] == sum(line["units"] for line in report["lines"])
    assert report["objects"] == 10 and report["max_live_objects"] == 6


def test_unknown_code_targets_coordinates_and_unsafe_text_are_rejected():
    plan = """
0 shot "Safe world"
0 add spaceship center medium as ship
1 add nail 1,2,3 small as nail
2 add nail center small as nail
3 wobble nail
4 strike hammer nail
5 label nail "https://bad.example/script.py"
6 camera orbit
"""
    shots, issues = world3d.parse_plan(plan, 10)
    joined = "\n".join(issues)
    assert "unknown asset 'spaceship'" in joined
    assert "unknown semantic slot '1,2,3'" in joined
    assert "unknown verb 'wobble'" in joined
    assert "target 'hammer' is used before it is created" in joined
    assert "path, a URL or unsafe markup" in joined
    assert "camera orbit needs a visible target" in joined
    assert world3d.to_text(shots).splitlines() == ['0 shot "Safe world"', "2 add nail center small as nail"]


def test_bounds_are_explained_and_persistent_state_is_checked_across_shots():
    lines = [
        '4 shot "World one"',
        "99 add pump center medium as pump",
        "99 cycle pump 99",
        '99 label pump "PUMP"',
        '6 shot "The same pump remains"',
        "6 camera close pump",
        "7 clear",
        "8 cycle pump 2",
        "9 add pump left small as pump",
    ]
    lines += [f"9 add nail center small as nail_{i}" for i in range(world3d.MAX_LIVE + 2)]
    shots, issues = world3d.parse_plan("\n".join(lines), 10)
    joined = "\n".join(issues)
    assert "first start word 4 bounded to 0" in joined
    assert "word 99 bounded" in joined
    assert "cycle count 99 bounded to 3" in joined
    assert "target 'pump' is used before it is created or after removal" in joined
    assert "object ID 'pump' already exists in this plan" in joined
    assert f"more than {world3d.MAX_LIVE} objects would be live" in joined
    # A shot never resets the world: the camera may still target the pump in shot two.
    assert "6 camera close pump" in world3d.to_text(shots)


def test_fixed_format_contracts_have_semantic_slots_and_no_coordinates():
    assert set(world3d.FRAME_CONTRACTS) == {"short", "long"}
    assert {contract["aspect"] for contract in world3d.FRAME_CONTRACTS.values()} == {"9:16", "16:9"}
    assert all(contract["safe_slots"] == list(world3d.SLOTS) for contract in world3d.FRAME_CONTRACTS.values())
    assert world3d.parse_line("0 add nail 0,0,0 small as nail") is None


def test_learning_excludes_3d_from_visual_style_comparisons():
    videos = []
    for index in range(6):
        is_3d = index >= 3
        videos.append(
            {
                "form": "short",
                "format": "short",
                "story_shape": "explain",
                "seconds": 30,
                "style": "3d" if is_3d else "ink",
                "look": "3d" if is_3d else "ink",
                "kept_at_5s": 90 if is_3d else 50,
                "avg_viewed": None,
            }
        )
    assert learning.craft_comparisons(videos) == [
        "not enough comparable videos (need at least 6 in the same format and duration group, "
        "story shape and duration group, with at least 3 videos in each craft group)"
    ]


def test_3d_prompts_have_complete_headers_and_explicit_anti_imitation_rules():
    for name in ("storyboard_3d", "storyboard_3d_short", "storyboard_3d_long"):
        assert not prompts.problems(name, prompts.default(name))
        assert prompts.meta(name).task == "storyboard"
    text = prompts.default("storyboard_3d").lower()
    forbidden_copying = (
        "script", "model", "composition", "captions", "colours", "camera sequence", "music", "branding", "wording"
    )
    for forbidden_copy in forbidden_copying:
        assert forbidden_copy in text
    assert "never emit python" in text and "never a timestamp" in text
    for unsupported in (
        "n clear",
        "n hold",
        "n remove",
        "n duplicate",
        "n assemble",
        "n move",
        "n rotate",
        "n arrow",
        "n measure",
        "n section",
        "n show|hide|highlight|dim",
    ):
        assert f"  {unsupported}" not in text
    assert '9 label outlet "flow"' in text and "15 highlight outlet" not in text


@pytest.fixture()
def world_script(app_env):
    sections = [
        {
            "key": "a",
            "heading": "Mechanism",
            "text": "A pump moves water. Then pressure rises.",
            "short": True,
        }
    ]
    with session_scope() as db:
        script = Script(
            title="How the pump works",
            theme="Mechanisms",
            video="short",
            form="short",
            sections=sections,
            research={
                "notes": "The pressure value comes from the source. [1]",
                "sources": [{"n": 1, "url": "https://example.test/source", "title": "Pump source"}],
            },
        )
        db.add(script)
        db.flush()
        input_hash, input_parts = story_map.input_fingerprint(script)
        script.story_map = {
            "template": "mechanism",
            "opening": {
                "context": "A pump beside a pipe",
                "lean": "Water waits",
                "turn": "The chamber changes pressure",
                "promise": "See why water moves",
                "first_visual": "A cutaway pump",
                "source_numbers": [1],
            },
            "beats": [
                {
                    "id": "pressure",
                    "purpose": "answer",
                    "summary": "Pressure pushes the water",
                    "source_numbers": [1],
                    "opens": [],
                    "resolves": [],
                    "visual_event": "The pump cycles into the outlet",
                }
            ],
            "ending": {"payoff": "Water exits", "callback": "the pump", "source_numbers": [1]},
            "data_cards": [
                {
                    "id": "pressure_value",
                    "kind": "metric",
                    "label": "Pressure",
                    "value": 12,
                    "unit": "bar",
                    "source_numbers": [1],
                }
            ],
            "input_hash": input_hash,
            "input_parts": input_parts,
        }
        script.hold = {
            "hash": script_mod.text_hash(sections),
            "topic": "pump",
            "roles": [{"n": 1, "role": "twist"}, {"n": 2, "role": "payoff"}],
            "checked_at": "now",
        }
        take = VoiceTake(
            script_id=script.id,
            source="kokoro",
            voice="am_michael",
            chosen=True,
            duration=4.0,
            script_hash=script_mod.text_hash(sections),
            alignment={
                "coverage": 1.0,
                "sections": [],
                "missing": [],
                "extra": [],
                "spoken_words": 7,
                "script_hash": script_mod.text_hash(sections),
                "wpm": 140,
            },
        )
        db.add(take)
        db.flush()
        folder = voice.take_dir(script.id, take.id)
        folder.mkdir(parents=True)
        timeline = [
            {"section": "a", "w": word, "s": index * 0.4, "e": index * 0.4 + 0.3}
            for index, word in enumerate(sections[0]["text"].split())
        ]
        (folder / "timeline.json").write_text(json.dumps(timeline))
        return script.id


def _setting(enabled: bool) -> None:
    with session_scope() as db:
        row = db.get(Setting, KEY)
        value = Settings(studio_3d_enabled=enabled).model_dump()
        if row is None:
            db.add(Setting(key=KEY, value=value))
        else:
            row.value = value


def test_planner_receives_current_map_cards_turns_and_numbered_narration(world_script, monkeypatch):
    seen = {}

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        seen.update(task=task, system=system, content=content, output_format=output_format, cache=cache)
        return storyboard.World3dPlan(
            beats="""
0 shot "The pump moves water into the outlet"
0 add pump left medium as pump
1 add pipe right large as outlet
2 cutaway pump
3 arrow pump outlet "FLOW"
4 cycle pump 2
5 camera orbit pump
"""
        )

    monkeypatch.setattr(llm, "ask", fake_ask)
    with session_scope() as db:
        script = db.get(Script, world_script)
        take = script.takes[0]
        words, start, end = storyboard.words_for(take, "a")
        board = Storyboard(
            script_id=script.id,
            take_id=take.id,
            format="short",
            form="short",
            style="3d",
            look="3d",
            section_key="a",
            title=script.title,
            start=start,
            end=end,
            words=words,
            scenes=[],
        )
        db.add(board)
        db.flush()
        board_id = board.id
    storyboard.plan(board_id, ConsoleReporter())
    assert seen["task"] == "storyboard" and seen["output_format"] is storyboard.World3dPlan
    assert "<story_map>" in seen["content"] and "pressure_value" in seen["content"]
    assert "<turns>" in seen["content"] and "[0] twist" in seen["content"]
    assert "<narration>" in seen["content"] and "[0] A" in seen["content"]
    assert "Do not copy any reference video's script" in seen["system"]
    with session_scope() as db:
        board = db.get(Storyboard, board_id)
        assert board.scenes[0]["lines"][0] == "0 add pump left medium as pump"


def test_api_beta_gate_round_trip_save_read_and_replan(client, world_script):
    path = f"/api/v1/scripts/{world_script}/storyboards"
    body = {"format": "short", "section_key": "a", "style": "3d"}
    disabled = client.post(path, json=body)
    assert disabled.status_code == 409 and "Turn on 3D explainers" in disabled.json()["detail"]

    _setting(True)
    made = client.post(path, json=body)
    assert made.status_code == 202
    board = made.json()
    assert (board["style"], board["look"], board["job"]["stages"]) == (
        "3d",
        "3d",
        ["plan", "build3d"],
    )
    assert board["director"] is None and not board["director_undoable"]
    with session_scope() as db:
        db.get(Job, board["job"]["id"]).state = "succeeded"

    scenes, issues = world3d.parse_plan(
        '0 shot "A pump"\n0 add pump center medium as pump\n2 cutaway pump\n4 cycle pump 2', 7
    )
    assert issues == []
    saved = client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes})
    assert saved.status_code == 200
    assert saved.json()["complexity"]["objects"] == 1
    bad = scenes.copy()
    bad[0] = {**bad[0], "lines": [*bad[0]["lines"], "5 strike hammer pump"]}
    refused = client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": bad})
    assert refused.status_code == 400 and "hammer" in refused.json()["detail"]

    # Disabling the beta hides new creation but never strands an existing plan.
    _setting(False)
    assert client.get(f"/api/v1/storyboards/{board['id']}").json()["scenes"] == scenes
    assert client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes}).status_code == 200
    replanned = client.post(f"/api/v1/storyboards/{board['id']}/replan")
    assert replanned.status_code == 202 and replanned.json()["stages"] == ["plan", "build3d"]
    assert client.post(f"/api/v1/storyboards/{board['id']}/direct").status_code == 409
    with session_scope() as db:
        db.get(Job, replanned.json()["id"]).state = "succeeded"
    previews = client.post(f"/api/v1/storyboards/{board['id']}/previews")
    assert previews.status_code == 202 and previews.json()["stages"] == ["build3d"]
    with session_scope() as db:
        db.get(Job, previews.json()["id"]).state = "succeeded"
    render = client.post(f"/api/v1/storyboards/{board['id']}/render")
    assert render.status_code == 202 and render.json()["stages"] == ["build3d", "animate"]
    assert client.get(f"/api/v1/storyboards/{board['id']}/still").status_code == 409
