"""Thumbnails of long videos: three options for YouTube's A/B test, drawn from the video's own cast."""

from pathlib import Path

import pytest
from PIL import Image
from test_director import PLAN
from test_storyboard import SVG, script_with_take  # noqa: F401 - the fixture is used by name

from clipper import llm
from clipper.ai_errors import AiUnavailable
from clipper.db import session_scope
from clipper.db.models import Job, Storyboard
from clipper.jobs import ConsoleReporter
from clipper.settings import Settings
from clipper.studio import beats, export, thumbnail
from clipper.studio import storyboard as sb


def cast(scenes_text: str = PLAN, drawn=("fridge", "snowflake")) -> thumbnail.Cast:
    scenes = beats.parse_plan(scenes_text + '\n6 who ada "Ada Lovelace" tonsure robe', 12)
    return thumbnail.Cast(scenes, set(drawn), set())


# --- pure parts -------------------------------------------------------------------------------

def test_the_picture_keeps_to_the_video_cast_and_pulls_things_together():
    lines, dropped = thumbnail.picture_lines(
        "1 actor narrator right surprised\ndraw fridge right large\ndraw piano left\n"
        "actor ada left jump happy\nmove narrator walk left\ndo narrator point fridge", cast())
    assert lines[0] == '0 who ada "Ada Lovelace" tonsure robe'            # people come with their own description
    assert "0 actor narrator surprised center" in lines             # numbered again, on neighbouring slots
    assert "2 draw fridge right large" in lines and "4 actor ada left" in " ".join(lines)
    assert any(x.startswith("4 actor ada") and "jump" not in x for x in lines)   # a move becomes a still pose
    assert any("piano" in d and "not in this video" in d for d in dropped)
    assert any("'move' is not used" in d for d in dropped)


def test_starred_words_are_coloured_even_across_two_words():
    assert thumbnail.starred("Born in *4 BC*?") == [("BORN", False), ("IN", False), ("4", True), ("BC?", True)]
    assert thumbnail.starred("A monk's *guess*") == [("A", False), ("MONK'S", False), ("GUESS", True)]
    assert thumbnail.starred("  ") == []


def test_the_headline_breaks_into_lines_that_fill_its_half():
    zone, _ = thumbnail._zones("left", "x")
    breaks, size = thumbnail._fit(["JESUS", "BORN", "IN", "4", "BC?"], zone)
    assert len(breaks) == 3 and size > 140
    assert [i for line in breaks for i in line] == [0, 1, 2, 3, 4]
    layer, placed = thumbnail.headline_layer("A *guess*", "right", "gold", plate=False)
    assert layer.size == (1920, 1080) and placed["box"][0] > 960        # on the right half


def option(**over) -> dict:
    base = {"key": "A", "angle": "reaction", "headline": "A monk's *guess*", "title": 1, "mode": "drawn",
            "measures": {"text_size": 160, "text_box": (80, 200, 860, 760), "things": 2, "subject": 0.3}}
    return {**base, **over}


def picture(shade: int, stripes: bool = True) -> Image.Image:
    image = Image.new("RGB", (1920, 1080), (shade, shade, shade))
    if stripes:
        for x in range(0, 1920, 240):
            image.paste((255 - shade,) * 3, (x, 0, x + 120, 1080))
    return image


def test_a_good_thumbnail_scores_full_marks_and_every_point_has_a_line():
    result = thumbnail.check(option(), picture(20), {"B": picture(200, stripes=False)},
                             ["How a monk counted the years"], "A monk made a guess in 525.")
    assert result["score"] == 100 and not result["problems"]
    assert all({"text", "points"} <= set(line) for line in result["lines"])


def test_the_check_names_what_breaks_youtube_rules_and_what_reads_badly():
    narration = "Jesus was born around 4 BC, a monk guessed 525."
    bad = option(headline="Shocking 1999 secret the monk hid from everyone",
                 measures={"text_size": 60, "text_box": (1700, 950, 1900, 1070), "things": 7, "subject": 0.05})
    result = thumbnail.check(bad, picture(120, stripes=False), {"B": picture(120, stripes=False)}, ["T"], narration)
    text = " | ".join(line["text"] for line in result["lines"])
    assert "more than 4" in text and "unreadable" in text and "duration" in text and "cluttered" in text
    assert "only 5% of the frame's height" in text and "Weak contrast" in text and "Too close to B" in text
    assert "Clickbait word: 'shocking'" in text and "1999 is not in the narration" in text
    assert len(result["problems"]) == 2 and result["score"] == 0
    fine = thumbnail.check(option(headline="Born in *4 BC*"), picture(20), {}, ["T"], narration)
    assert not fine["problems"]                                       # numbers the narration says are fine


def test_a_headline_that_repeats_its_title_loses_points():
    result = thumbnail.check(option(headline="Monk counted years"), picture(20), {},
                             ["How a monk counted the years"], "")
    assert any(line["points"] == -10 and "repeats its title" in line["text"] for line in result["lines"])


def test_the_jpeg_stays_under_youtubes_two_megabytes():
    noise = Image.effect_noise((1920, 1080), 120).convert("RGB")
    assert len(thumbnail.jpeg(noise)) <= thumbnail.MAX_BYTES


def test_the_youtube_file_lists_which_title_goes_with_which_thumbnail():
    text = export.youtube_file({"title": "T1", "titles": ["T1", "T2"], "description": "D", "tags": []},
                               [{"key": "A", "angle": "reaction", "title": 2}])
    assert "A/B TEST" in text and "A: thumbnail-A.jpg (reaction), with the title: T2" in text
    assert "A/B TEST" not in export.youtube_file({"title": "T", "description": "D"})


# --- the job and the routes -----------------------------------------------------------------

IDEAS = thumbnail.AiThumbnails(thumbnails=[
    thumbnail.AiThumbnail(angle="mystery", headline="", title=3, side="left", accent="gold",
                          beats="draw snowflake center large\ncircle snowflake bad", shot=0, why="Why a snowflake?"),
    thumbnail.AiThumbnail(angle="reaction", headline="Never *cold*", title=1, side="left", accent="gold",
                          beats="actor narrator left surprised\ndraw fridge right large\ndo narrator point fridge",
                          shot=0, why="A face"),
    thumbnail.AiThumbnail(angle="stakes", headline="It moves *heat*", title=2, side="right", accent="warm",
                          beats="draw fridge left large\ndraw piano right", shot=0, why="The turn"),
])


@pytest.fixture()
def fake_ai(monkeypatch):
    seen = {"tasks": [], "fail": None, "unclear": set()}

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        seen["tasks"].append((task, system, content))
        if output_format is sb.InkPlan:
            return sb.InkPlan(beats=PLAN, new_props=[sb.PropRequest(name="fridge", description="A white fridge"),
                                                     sb.PropRequest(name="snowflake", description="A snowflake")])
        if output_format is sb.Drawing:
            return sb.Drawing(svg=SVG)
        if output_format is sb.Look:
            return sb.Look(matches=True, quality=5, fix="")
        if seen["fail"]:
            raise seen["fail"]
        if output_format is thumbnail.AiThumbnails:
            if "Plan one thumbnail" in system:
                return thumbnail.AiThumbnails(thumbnails=[IDEAS.thumbnails[1].model_copy(
                    update={"headline": "Cold? *Never*"})])
            return IDEAS
        if output_format is thumbnail.Looks:
            return thumbnail.Looks(verdicts=[thumbnail.Verdict(key=k, clear=k not in seen["unclear"], honest=True,
                                                               note="Too small" if k in seen["unclear"] else "")
                                             for k in "ABC"])
        raise AssertionError(output_format)
    monkeypatch.setattr(llm, "ask", fake_ask)
    return seen


def run_job(job_id: int) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        stages, params = list(job.stages), dict(job.params)
    for stage in stages:
        if stage == "plan":
            sb.plan(params["storyboard_id"], ConsoleReporter())
        elif stage == "draw":
            sb.draw_missing(params["storyboard_id"], ConsoleReporter())
        elif stage == "thumbnails":
            thumbnail.run(params["storyboard_id"], Settings(), ConsoleReporter(), params.get("only"),
                          params.get("note", ""))
    with session_scope() as db:
        db.get(Job, job_id).state = "succeeded"


def long_board(client, script_id: int) -> int:
    from clipper.db.models import Script, VoiceTake
    from clipper.studio import script as script_mod

    with session_scope() as db:          # a long video needs a take of the current script
        take = db.query(VoiceTake).filter_by(script_id=script_id).one()
        take.script_hash = script_mod.text_hash(db.get(Script, script_id).sections)
    board = client.post(f"/api/v1/scripts/{script_id}/storyboards", json={"format": "long"}).json()
    run_job(board["job"]["id"])
    return board["id"]


def test_three_thumbnails_are_planned_drawn_checked_and_edited(client, script_with_take, fake_ai):  # noqa: F811
    board_id = long_board(client, script_with_take)
    fake_ai["unclear"] = {"C"}
    job = client.post(f"/api/v1/storyboards/{board_id}/thumbnails").json()
    assert job["stages"] == ["thumbnails"]
    run_job(job["id"])
    task, system, content = next(t for t in fake_ai["tasks"] if t[0] == "thumbnail")
    assert "reaction" in system and "fridge" in system
    assert "<narration>" in content and "Your fridge" in content

    thumbs = client.get(f"/api/v1/storyboards/{board_id}").json()["thumbnails"]
    a, b, c = thumbs["options"]
    assert [o["key"] for o in thumbs["options"]] == ["A", "B", "C"] and thumbs["by"] == "ai"
    assert [o["angle"] for o in thumbs["options"]] == ["reaction", "stakes", "mystery"]   # in angle order
    assert a["headline"] == "Never *cold*" and a["mode"] == "drawn" and "draw fridge" in a["beats"]
    assert any("piano" in d for d in b["dropped"])                   # not in this video: left out
    assert c["look"]["redrawn"] and c["headline"] == "Cold? *Never*"    # judged unclear: planned once more
    assert all("score" in o["check"] and o["check"]["lines"] for o in thumbs["options"])
    assert "path" not in a

    image = client.get(f"/api/v1/storyboards/{board_id}/thumbnails/A.jpg")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert Image.open(__import__("io").BytesIO(image.content)).size == (1920, 1080)

    edited = client.put(f"/api/v1/storyboards/{board_id}/thumbnails/A",
                        json={"headline": "Heat goes *out*", "side": "right", "mode": "frame", "at": 1.0}).json()
    a2 = edited["thumbnails"]["options"][0]
    assert a2["headline"] == "Heat goes *out*" and a2["by"] == "you" and a2["mode"] == "frame"
    assert a2["version"] != a["version"] and "look" not in a2
    with session_scope() as db:
        files = sorted(p.name for p in thumbnail.folder(db.get(Storyboard, board_id)).iterdir())
    assert len(files) == 3 and "A-2.jpg" in files                     # the old drawing is gone

    again = client.post(f"/api/v1/storyboards/{board_id}/thumbnails", json={"only": "B", "note": "bigger"}).json()
    run_job(again["id"])
    *_, (task, system, content) = [t for t in fake_ai["tasks"] if t[0] == "thumbnail"]
    assert "<request>bigger</request>" in content and "<angle>stakes</angle>" in content
    b2 = client.get(f"/api/v1/storyboards/{board_id}").json()["thumbnails"]["options"][1]
    assert b2["angle"] == "stakes" and b2["headline"] == "Cold? *Never*"

    with session_scope() as db:
        folder = thumbnail.folder(db.get(Storyboard, board_id))
    assert client.delete(f"/api/v1/storyboards/{board_id}").status_code == 204
    assert not folder.exists()


def test_an_ai_outage_gives_three_frames_picked_by_rules(client, script_with_take, fake_ai):  # noqa: F811
    board_id = long_board(client, script_with_take)
    fake_ai["fail"] = AiUnavailable("no credit")
    run_job(client.post(f"/api/v1/storyboards/{board_id}/thumbnails").json()["id"])
    thumbs = client.get(f"/api/v1/storyboards/{board_id}").json()["thumbnails"]
    assert thumbs["by"] == "rules"
    assert all(o["mode"] == "frame" and o["headline"] == "" and "rules" in o["why"] for o in thumbs["options"])
    with session_scope() as db:
        paths = [Path(o["path"]) for o in db.get(Storyboard, board_id).thumbnails["options"]]
    assert all(p.is_file() for p in paths)


def test_shorts_and_unplanned_boards_get_no_thumbnails(client, script_with_take, fake_ai):  # noqa: F811
    short = client.post(f"/api/v1/scripts/{script_with_take}/storyboards",
                        json={"format": "short", "section_key": "a"}).json()
    run_job(short["job"]["id"])
    assert client.post(f"/api/v1/storyboards/{short['id']}/thumbnails").status_code == 409
    board_id = long_board(client, script_with_take)
    assert client.post(f"/api/v1/storyboards/{board_id}/thumbnails", json={"only": "A"}).status_code == 409
    assert client.put(f"/api/v1/storyboards/{board_id}/thumbnails/A", json={"headline": "x"}).status_code == 404
    assert client.get(f"/api/v1/storyboards/{board_id}/thumbnails/A.jpg").status_code == 404
