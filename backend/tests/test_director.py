"""The AI director: stills of the shots go to the model, and only safe rewrites are kept."""

import pytest
from test_storyboard import SVG, script_with_take  # noqa: F401 - the fixture is used by name

from clipper import llm
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import Job, Storyboard
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import beats, director, film
from clipper.studio import storyboard as sb

PLAN = '0 shot "Fridge"\n0 actor narrator left talk\n1 draw fridge right\n2 do narrator push fridge\n' \
       '3 write "never cold" top big bad\n4 shot "Out" drift\n4 draw snowflake center\n5 spin snowflake'

KIT = director.KITS["ink"]


def shots(text: str, words: int = 12) -> list[dict]:
    return beats.parse_plan(text, words)


def test_a_rewrite_of_one_shot_is_kept_and_cleaned():
    plan = shots('0 shot "A"\n0 actor narrator left\n1 draw fridge right\n3 shot "B"\n3 draw box center\n'
                 '4 shake box\n8 shot "C"\n8 draw coin center')
    new, why, _ = director.replace_shot(KIT, plan, 1, '3 shot "B, closer" push\n3 draw box center large\n'
                                                      '2 circle box\n40 bounce box', 12)
    assert why == "" and new[1]["note"] == "B, closer" and new[1]["camera"] == "push"
    assert new[1]["lines"] == ["3 draw box center large", "3 circle box", "7 bounce box"]   # kept in its words
    assert new[0] == plan[0] and new[2] == plan[2]


def test_a_rewrite_that_breaks_a_later_shot_is_refused():
    plan = shots('0 shot "A"\n0 actor narrator left\n1 draw fridge right\n4 shot "B"\n5 shake fridge')
    new, why, _ = director.replace_shot(KIT, plan, 0, '0 shot "A"\n0 actor narrator left\n1 draw box right', 12)
    assert new is None and "shot 2" in why


def test_a_rewrite_adds_no_archive_photo_and_must_change_something():
    plan = shots('0 shot "A"\n0 actor narrator left\n1 draw fridge right\n2 shake fridge')
    new, why, _ = director.replace_shot(KIT, plan, 0, '0 shot "A"\n0 actor narrator left\n1 draw fridge right\n'
                                                      '2 shake fridge\n3 photo "Frigidaire 1920"', 12)
    assert new is None and why == "it came back the same"
    assert director.replace_shot(KIT, plan, 0, "nothing at all", 12)[1].startswith("the rewrite had no lines")


def test_film_shots_use_the_same_guard():
    kit = director.KITS["film"]
    plan, _ = film.clean(film.parse_plan('0 shot "Wall"\n0 card "Rome" "753 BC" as rome\n3 shot "Next"\n'
                                         '4 circle rome', 8), 8)
    refused, why, _ = director.replace_shot(kit, plan, 0, '0 shot "Wall"\n0 big "Carthage"', 8)
    assert refused is None and "shot 2" in why
    kept, why, _ = director.replace_shot(kit, plan, 0, '0 shot "Wall, red"\n0 card "Rome" "753 BC" as rome\n'
                                                       '1 stamp "founded" on rome', 8)
    assert why == "" and kept[0]["note"] == "Wall, red" and kept[1] == plan[1]


def test_the_contact_sheet_is_one_png_with_a_row_per_shot():
    import io

    from PIL import Image
    tile = io.BytesIO()
    Image.new("RGB", (200, 356), "white").save(tile, "PNG")
    png = director.sheet([(1, "0:00-0:03", [tile.getvalue()] * 3), (2, "0:03-0:05", [tile.getvalue()] * 3)], "short")
    image = Image.open(io.BytesIO(png))
    assert image.width == director.GAP + 3 * (200 + director.GAP)
    assert image.height == director.GAP + 2 * (director.LABEL_HEIGHT + 356 + director.GAP)


@pytest.fixture()
def fake_ai(monkeypatch):
    seen = {"direct": [], "fail": None}

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        if output_format is sb.InkPlan:
            return sb.InkPlan(beats=PLAN, new_props=[sb.PropRequest(name="fridge", description="A white fridge"),
                                                     sb.PropRequest(name="snowflake", description="A snowflake")])
        if output_format is sb.Drawing:
            return sb.Drawing(svg=SVG)
        if output_format is director.Direction:
            if seen["fail"]:
                raise seen["fail"]
            seen["direct"].append((task, system, content))
            return director.Direction(shots=[
                director.ShotNote(shot=1, problems=["The fridge is tiny next to the narrator"],
                                  beats='0 shot "Fridge"\n0 actor narrator left talk\n1 draw fridge right large\n'
                                        '2 do narrator push fridge\n3 shake fridge'),
                director.ShotNote(shot=2, problems=["The snowflake reads as a star"], beats=""),
            ])
        raise AssertionError(output_format)
    monkeypatch.setattr(llm, "ask", fake_ask)
    return seen


def run_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        stages, board_id = list(job.stages), job.params["storyboard_id"]
    for stage in stages:
        if stage == "plan":
            sb.plan(board_id, ConsoleReporter())
        elif stage == "draw":
            sb.draw_missing(board_id, ConsoleReporter())
        elif stage == "direct":
            director.run(board_id, Settings(), ConsoleReporter())
    with session_scope() as db:
        db.get(Job, job_id).state = "succeeded"


def test_the_director_looks_rewrites_and_can_be_undone(client, script_with_take, fake_ai):  # noqa: F811
    board = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                        json={"format": "short", "section_key": "a"}).json()
    assert board["job"]["stages"] == ["plan", "draw"]          # the director is off by default
    run_job(board["job"]["id"])
    planned = client.get(f"/api/v1/storyboards/{board['id']}").json()

    job = client.post(f"/api/v1/storyboards/{board['id']}/direct").json()
    assert job["stages"] == ["draw", "direct"]
    run_job(job["id"])
    (task, system, content), = fake_ai["direct"]                # 2 shots of a Short: one sheet
    assert task == "direct" and "beat language" in system
    assert content[1]["type"] == "image" and content[1]["source"]["media_type"] == "image/png"
    assert '<shot number="1" words="0-3"' in content[0]["text"] and "[1] fridge" in content[0]["text"]

    done = client.get(f"/api/v1/storyboards/{board['id']}").json()
    assert done["scenes"][0]["lines"][-1] == "3 shake fridge" and done["scenes"][1] == planned["scenes"][1]
    report = done["director"]
    assert report["changed"] == 1 and report["refused"] == 0 and "before" not in report
    assert [n["outcome"] for n in report["notes"]] == ["changed", "noted"]
    assert done["director_undoable"]

    undone = client.post(f"/api/v1/storyboards/{board['id']}/director/undo").json()
    assert undone["scenes"] == planned["scenes"] and not undone["director_undoable"]
    assert undone["director"]["undone_at"]
    assert client.post(f"/api/v1/storyboards/{board['id']}/director/undo").status_code == 409


def test_an_edit_after_the_pass_blocks_the_undo(client, script_with_take, fake_ai):  # noqa: F811
    board = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                        json={"format": "short", "section_key": "a"}).json()
    run_job(board["job"]["id"])
    run_job(client.post(f"/api/v1/storyboards/{board['id']}/direct").json()["id"])
    scenes = client.get(f"/api/v1/storyboards/{board['id']}").json()["scenes"]
    scenes[1]["lines"].append("5 bounce snowflake")
    edited = client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes}).json()
    assert not edited["director_undoable"]
    assert client.post(f"/api/v1/storyboards/{board['id']}/director/undo").status_code == 409


def test_the_setting_adds_the_director_to_every_plan(client, script_with_take, fake_ai):  # noqa: F811
    assert client.put("/api/v1/settings", json={**client.get("/api/v1/settings").json(),
                                                "director_pass": True}).status_code == 200
    board = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                        json={"format": "short", "section_key": "a"}).json()
    assert board["job"]["stages"] == ["plan", "draw", "direct"]
    flat = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                       json={"format": "short", "section_key": "b", "style": "flat"}).json()
    assert flat["job"]["stages"] == ["plan", "draw"]
    assert client.post(f"/api/v1/storyboards/{flat['id']}/direct").status_code == 409


def test_an_ai_outage_fails_the_pass_and_leaves_the_plan(client, script_with_take, fake_ai):  # noqa: F811
    board = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                        json={"format": "short", "section_key": "a"}).json()
    run_job(board["job"]["id"])
    before = client.get(f"/api/v1/storyboards/{board['id']}").json()["scenes"]
    fake_ai["fail"] = AiUnavailable("Your Claude plan's usage limit is reached")
    with pytest.raises(RuntimeError, match="usage limit"):
        director.run(board["id"], Settings(), ConsoleReporter())
    with session_scope() as db:
        row = db.get(Storyboard, board["id"])
        assert row.scenes == before and row.director is None
