"""The film style: the documentary wall's beat language, its renderer and the flow through the API."""

import json
import subprocess

import numpy as np
import pytest
from test_storyboard import run_job, script_with_take  # noqa: F401 - the fixture is used by name

from clipper import llm
from clipper.settings import Settings
from clipper.studio import animate, film, photos, sound, wall
from clipper.studio import storyboard as sb

PLAN = """
0 shot "The date and the photo"
0 big "DECEMBER 17, 1903" bar as date
2 photo "Wright Flyer first flight" label "KITTY HAWK" as flight
4 count "120" "FEET" as feet
5 string flight feet
6 shot "The rival" cut
6 card "THE RIVAL" "Samuel Langley" "$50,000 of public money" as rival
8 highlight rival "public money"
9 stamp "FAILED" on rival
10 doc "THE NEW YORK TIMES" "9 OCTOBER 1903" "one million to ten million years" as nyt
11 highlight nyt "ten million years"
12 map us fr as m
13 pin us "DAYTON" on m
14 route us fr
15 circle feet
16 wide
"""


def words(n: int = 20, step: float = 1.0) -> list[dict]:
    return [{"w": f"w{i}", "s": i * step, "e": i * step + 0.4} for i in range(n)]


def board(look: str = "wall", fmt: str = "short", n: int = 20) -> dict:
    shots, dropped = film.clean(film.parse_plan(PLAN, n), n)
    assert dropped == []
    w = words(n)
    return {"id": 1, "script_id": 1, "format": fmt, "style": "film", "look": look, "start": 0.0, "end": w[-1]["e"],
            "words": w, "scenes": shots, "photos": {}}


def test_lines_are_read_and_written_back_the_same_way():
    for raw in [line for line in PLAN.splitlines() if line and " shot " not in line]:
        line = film.parse_line(raw)
        assert line is not None, raw
        assert film.parse_line(line.render()).render() == line.render()
    assert film.parse_line('3 doc "ONLY A HEADER"') is None                   # a page needs the quoted words
    assert film.parse_line('3 count "many" "THINGS"') is None                 # a counter needs a number
    assert film.parse_line("3 flag atlantis") is None
    assert film.parse_line('3 map fr de as m').lines == ["fr", "de"]


def test_clean_drops_what_cannot_be_drawn_and_says_what():
    shots, dropped = film.clean([{"start_word": 0, "lines": [
        '0 stamp "NO"',                       # nothing on the wall yet
        '1 big "HELLO" as hi',
        "2 circle nobody",                    # no such thing
        '3 pin fr "PARIS"',                   # no map yet
        "4 juggle hi",
        '5 card "T" "a" as t',
        "6 string hi t",
        '99 stamp "LATE" on t',               # past the last word: moved to it
    ]}], 10)
    assert shots[0]["lines"] == ['1 big "HELLO" as hi', '5 card "T" "a" as t', "6 string hi t", '9 stamp "LATE" on t']
    assert len(dropped) == 4 and any("juggle" in d for d in dropped)


def test_plan_text_becomes_shots_with_their_word_numbers():
    shots = film.parse_plan('0 shot "One"\n0 big "A"\n2 shot "Two" cut\n2 big "B"', 10, offset=5)
    assert [(s["start_word"], s["cut"]) for s in shots] == [(5, False), (7, True)]
    assert shots[1]["lines"] == ['7 big "B"']
    assert "shot" in film.to_text(shots) and film.photo_queries(film.clean(film.parse_plan(PLAN, 20), 20)[0]) \
        == ["Wright Flyer first flight"]


def test_the_wall_puts_every_hit_on_its_word_and_the_camera_on_each_hero():
    composer = wall.FilmComposer(board(), {}, Settings())
    kinds = {t.kind for t in composer.things}
    assert {"big", "photo", "label", "count", "card", "highlight", "stamp", "doc", "map", "mappin", "mark"} <= kinds
    stamp = next(t for t in composer.things if t.kind == "stamp")
    assert abs(stamp.born - composer.word_time(9)) < 0.1
    assert len([t for t in composer.things if t.kind == "highlight" and t.target.kind == "doc"]) >= 1
    keys = composer.keys
    assert all(b.t > a.t for a, b in zip(keys, keys[1:], strict=False))     # the camera always moves on
    assert keys[-1].t > composer.duration                                  # the pull-back comes after the voice
    assert len(composer.strings) == 1                                      # the second shot is a cut: no thread
    events = {kind for _, kind in composer.sound_events()}
    assert {"stamp", "pin", "ticks", "marker", "string"} <= events
    frame = composer.bgr(9.2)
    assert frame.shape == (1920, 1080, 3) and frame.std() > 10


def test_a_missing_photo_is_a_card_with_its_search_and_looks_only_change_drawing():
    wall_look = wall.FilmComposer(board("wall"), {}, Settings())
    studio = wall.FilmComposer(board("studio"), {}, Settings())
    assert [t.id for t in wall_look.things] == [t.id for t in studio.things]          # the same plan
    assert [round(k.t, 3) for k in wall_look.keys] == [round(k.t, 3) for k in studio.keys]
    dark, light = wall_look.frame_rgb(3.0), studio.frame_rgb(3.0)
    assert light.mean() > dark.mean() + 40
    long = wall.FilmComposer(board("wall", "long"), {}, Settings())
    assert long.frame_rgb(3.0).shape == (1080, 1920, 3)


def test_the_tail_is_silence_after_the_voice():
    graph, _ = sound.mix_graph(12.0, True, False, -18, tail=2.4)
    assert "apad=pad_dur=2.400" in graph
    assert "apad" not in sound.mix_graph(12.0, True, False, -18)[0]
    for family in ("stamp", "pin", "ticks", "impact", "typing", "marker"):
        clip = sound.FAMILIES[family][0](np.random.default_rng(1))
        assert np.isfinite(clip).all() and np.abs(clip).max() < 2


# --- through the API -------------------------------------------------------------------------------

@pytest.fixture()
def fake_film_planner(monkeypatch):
    seen = []

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        seen.append((task, cache, output_format.__name__))
        assert output_format is sb.FilmPlan and "evidence wall" in system
        return sb.FilmPlan(beats='0 shot "Fridge"\n0 big "NEVER COLD" bar as cold\n1 photo "refrigerator 1920s" '
                                 'label "THE ICEBOX" as box\n3 stamp "MYTH" on cold\n5 shot "Out" cut\n'
                                 '5 count "300" "WATTS" as w\n6 circle w\n9 bogus line')
    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr(photos, "find", lambda query, rank=0: None)       # no network in tests
    return seen


def test_film_short_from_plan_to_video(client, script_with_take, fake_film_planner):  # noqa: F811
    sid = script_with_take
    made = client.post(f"/api/v1/scripts/{sid}/storyboards",
                       json={"format": "short", "section_key": "a", "style": "film", "look": "ink"}).json()
    assert made["style"] == "film" and made["look"] == "wall"          # an ink look falls back to the wall
    run_job(made["job"]["id"])
    assert ("storyboard", True, "FilmPlan") in fake_film_planner
    planned = client.get(f"/api/v1/storyboards/{made['id']}").json()
    assert [s["start_word"] for s in planned["scenes"]] == [0, 5]
    assert planned["photo_searches_missing"] == ["refrigerator 1920s"] and planned["missing_props"] == []
    assert client.patch(f"/api/v1/storyboards/{made['id']}", json={"look": "cartoon"}).status_code == 400
    assert client.patch(f"/api/v1/storyboards/{made['id']}", json={"look": "studio"}).json()["look"] == "studio"

    scenes = planned["scenes"]
    scenes[1]["lines"].append('5 big "OUT"')
    edited = client.put(f"/api/v1/storyboards/{made['id']}/scenes", json={"scenes": scenes}).json()
    assert edited["scenes"][1]["lines"][-1] == '5 big "OUT"'      # the section has 6 words
    scenes[1]["lines"].append("5 circle nothing")
    bad = client.put(f"/api/v1/storyboards/{made['id']}/scenes", json={"scenes": scenes})
    assert bad.status_code == 400 and "nothing" in bad.json()["detail"]
    still = client.get(f"/api/v1/storyboards/{made['id']}/still", params={"t": 1.5, "width": 270})
    assert still.status_code == 200 and still.content[:4] == b"\x89PNG"

    job = client.post(f"/api/v1/storyboards/{made['id']}/render").json()
    run_job(job["id"])
    video = animate.video_path({"script_id": sid, "id": made["id"], "format": "short"})
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                       "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
    voiced = planned["duration"]
    assert float(probe["format"]["duration"]) == pytest.approx(voiced + 0.6, abs=0.15)    # a short tail: 0.6 s
    check = client.get(f"/api/v1/storyboards/{made['id']}/publish-check").json()
    assert check is not None
