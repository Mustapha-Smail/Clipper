"""The ink style: the beat language, ink drawings, the stage and the flow through the API."""

import json
import subprocess

import numpy as np
import pytest
from test_storyboard import SVG, run_job, script_with_take  # noqa: F401 - the fixture is used by name

from clipper import llm
from clipper.settings import Settings
from clipper.studio import animate, beats, ink, stage
from clipper.studio import storyboard as sb

PLAN = """
0 shot "The narrator meets a fridge" push
0 actor narrator left deadpan talk
1 draw fridge right large "fridge"
2 do narrator push fridge worried
3 write "0 cold" top big bad as zero
4 cross zero
5 arrow fridge narrator
5 shot "Heat goes out"
5 draw snowflake center
6 do narrator point snowflake
7 spin snowflake
8 look snowflake
9 erase all
"""


def test_lines_are_read_in_any_order_and_written_back_the_same_way():
    line = beats.parse_line('12 draw LED-Bulb large right "the chip" as chip')
    assert (line.verb, line.who, line.slot, line.size, line.text, line.ident) == \
        ("draw", "led_bulb", "right", "large", "the chip", "chip")
    assert line.render() == '12 draw led_bulb right large "the chip" as chip'
    do = beats.parse_line("[30] do narrator fridge push angry")
    assert (do.word, do.pose, do.target, do.expression) == (30, "push", "fridge", "angry")
    assert do.render() == "30 do narrator push fridge angry"
    assert beats.parse_line('4 write "a b c d e f g h" top').text == "a b c d e f"     # six words at most
    assert beats.parse_line("4 arrow fridge left").target == "left"                  # an arrow may point at a place
    for bad in ("draw fridge", "4 dance narrator", "4 arrow fridge", '4 write top'):
        assert beats.parse_line(bad) is None


def test_clean_keeps_lines_that_make_sense():
    shots = [beats.shot(0, "a", lines=["0 actor narrator left", "1 do ghost point fridge", "2 draw fridge",
                                       "3 actor narrator right smug", "9 nonsense here"]),
             beats.shot(5, "b", lines=["5 do narrator cheer", "6 circle fridge"]),
             beats.shot(5, "dup", lines=["7 draw coin"])]
    out, dropped = beats.clean(shots, word_count=10)
    assert [s["start_word"] for s in out] == [0, 5]
    first = out[0]["lines"]
    # an unknown actor is dropped; the same actor again is a pose change and a walk, not a second actor
    assert first == ["0 actor narrator left", "2 draw fridge", "3 do narrator smug", "3 move narrator right"]
    # the next shot can still use what was on stage: the narrator walks over, the fridge comes along
    assert out[1]["lines"] == ["5 do narrator cheer", "6 circle fridge", "7 draw coin"]
    assert sorted(dropped) == ["1 do ghost point fridge  (nothing called ghost is on stage then)",
                               "9 nonsense here  (not understood)"]
    gone = [beats.shot(0, lines=["0 draw fridge", "1 erase fridge"]), beats.shot(3, lines=["4 circle fridge"])]
    assert beats.clean(gone, 10)[1] == ["4 circle fridge  (nothing called fridge is on stage then)"]


def test_a_crowded_stage_erases_the_oldest_thing():
    lines = [f"{i} draw prop_{i}" for i in range(8)]
    out, _ = beats.clean([beats.shot(0, lines=lines)], word_count=20)
    erased = [x for x in out[0]["lines"] if " erase " in x]
    assert erased == ["6 erase prop_0", "7 erase prop_1"]


def test_plan_text_becomes_shots_and_props():
    shots = beats.parse_plan(PLAN, word_count=12)
    assert [(s["start_word"], s["camera"]) for s in shots] == [(0, "push"), (5, "wide")]
    assert beats.prop_names(shots) == {"fridge", "snowflake"}
    assert beats.to_text(shots).splitlines()[0] == '0 shot "The narrator meets a fridge" push'
    # chapters planned separately keep their own numbering until shifted
    later = beats.parse_plan('0 shot "x"\n1 draw coin', word_count=40, offset=30, check=False)
    assert later[0]["start_word"] == 30 and later[0]["lines"] == ["31 draw coin"]


def test_the_score_explains_every_point():
    def at(i):
        return i * 1.0
    lively = beats.score(beats.parse_plan(PLAN, 12), at, 12.0)
    assert lively["score"] >= 80 and lively["lines"] == []
    dull = [beats.shot(0, lines=["0 actor narrator left", "1 draw fridge"]),
            beats.shot(20, lines=["20 actor narrator left", "21 draw fridge"])]
    report = beats.score(beats.clean(dull, 40)[0], at, 40.0)
    labels = {x["label"] for x in report["lines"]}
    assert {"Still stretches", "Nothing happens", "Idle characters", "Slow pace"} <= labels
    assert report["score"] == 100 + sum(x["points"] for x in report["lines"]) and report["verdict"] == "slideshow risk"


def test_sound_follows_the_pen_the_camera_and_the_feet():
    composer = stage.InkComposer(board(), {"fridge": SVG, "snowflake": SVG}, Settings(), None)
    kinds = [k for _, k in composer.sound_events()]
    assert kinds[0] == "scribble" and {"pan", "cross", "erase", "step", "creak", "swish"} <= set(kinds)


def test_props_turn_into_ink_lines_that_draw_themselves():
    prop = ink.ink_prop(SVG)
    assert prop.shapes and prop.shapes[0][1] == pytest.approx(2 * np.pi * 180, rel=0.01)
    half = ink.prop_lines_svg(prop, 0.5, 100, 100)
    assert "stroke-dasharray" in half and 'fill-opacity="0"' in half
    assert "stroke-dasharray" not in ink.prop_lines_svg(prop, 1.0, 100, 100)    # the cached tree is restored
    assert "#ffd400" not in ink.prop_lines_svg(prop, 1.0, 100, 100) and ink._wash("#ffd400") in prop.wash
    done, start = ink.prop_image(SVG, 120, 1.0), ink.prop_image(SVG, 120, 0.05)
    assert np.asarray(done)[..., 3].sum() > 4 * np.asarray(start)[..., 3].sum()


def test_arms_reach_what_they_point_at():
    pose = ink.reach(ink.Pose.named("stand"), "front_arm", (120.0, -90.0))
    hand = ink.joints(pose)["front_arm"][-1]
    assert np.hypot(hand[0] - 120, hand[1] + 90) < 1.0
    far = ink.joints(ink.reach(ink.Pose.named("stand"), "front_arm", (900.0, -128.0)))["front_arm"][-1]
    assert far[0] == pytest.approx(ink.joints(ink.Pose.named("stand"))["shoulder"][0] + 2 * ink.ARM, abs=2)
    for name in ink.POSE_ANGLES:
        image = ink.render(ink.figure_svg("royal", ink.Pose.named(name), "happy", 1, facing="left", scale_px=0.2))
        assert image.getbbox() is not None


def test_handwriting_writes_from_left_to_right():
    lines = ink.wrap("forty two percent of nothing", 60, 300)
    assert len(lines) > 1
    half = np.asarray(ink.render(ink.text_svg(lines, 60, ink.INK, 0.3)))[..., 3]
    full = np.asarray(ink.render(ink.text_svg(lines, 60, ink.INK, 1.0)))[..., 3]
    assert 0 < half.sum() < full.sum() * 0.6


def board(fmt: str = "long") -> dict:
    words = [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.4, "section": "a" if i < 6 else "b"} for i in range(12)]
    return {"format": fmt, "words": words, "start": 0.0, "end": 6.2, "scenes": beats.parse_plan(PLAN, 12),
            "headings": {"a": "One", "b": "Two"}}


def test_the_stage_walks_actors_to_what_they_push_and_follows_with_the_camera():
    composer = stage.InkComposer(board(), {"fridge": SVG, "snowflake": SVG}, Settings(), None)
    narrator = next(x for x in composer.things if x.id == "narrator")
    fridge = next(x for x in composer.things if x.id == "fridge")
    assert narrator.moves, "the narrator walks up to the fridge before pushing it"
    before, after = composer.position(narrator, 0.9), composer.position(narrator, 2.4)
    assert after[0] > before[0] and abs(after[0] - composer.body_box(fridge, 2.4)[0]) < 220
    # in the next shot he has walked over with the camera
    assert composer.position(narrator, 4.0)[0] > composer.origins[1][0]
    views = [composer.camera(t) for t in (0.5, 4.0, 4.6)]
    assert views[1][0] > views[0][0] and views[2][2] > 1.1          # pans on, then looks in at the snowflake
    frame = composer.bgr(1.8)
    assert frame.shape == (1080, 1920, 3) and (frame < 80).any()     # ink on the paper
    assert composer.chapters == [(3.0, 2, "Two")]


def test_a_short_keeps_its_captions(tmp_path):
    composer = stage.InkComposer(board("short"), {}, Settings(), np.zeros(200, dtype=np.int8))
    assert composer.captions is not None and composer.bgr(1.0).shape == (1920, 1080, 3)


# --- through the API -------------------------------------------------------------------------------

@pytest.fixture()
def fake_planner(monkeypatch):
    seen = []

    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        seen.append((task, cache, output_format.__name__))
        if output_format is sb.InkPlan:
            return sb.InkPlan(beats='0 shot "Fridge"\n0 actor narrator left talk\n1 draw fridge right\n'
                                    '2 do narrator push fridge\n3 write "never cold" top big bad\n'
                                    '4 shot "Out" drift\n4 draw snowflake center\n5 spin snowflake\n9 bogus',
                              new_props=[sb.PropRequest(name="fridge", description="A white fridge"),
                                         sb.PropRequest(name="snowflake", description="A snowflake")])
        if output_format is sb.Drawing:
            return sb.Drawing(svg=SVG)
        raise AssertionError(output_format)
    monkeypatch.setattr(llm, "ask", fake_ask)
    return seen


def test_ink_short_from_plan_to_video(client, script_with_take, fake_planner):  # noqa: F811
    sid = script_with_take
    board = client.post(f"/api/v1/scripts/{sid}/storyboards", json={"format": "short", "section_key": "a"}).json()
    assert board["style"] == "ink"
    run_job(board["job"]["id"])
    assert ("storyboard", True, "InkPlan") in fake_planner          # the shared instructions are cached
    planned = client.get(f"/api/v1/storyboards/{board['id']}").json()
    assert [s["start_word"] for s in planned["scenes"]] == [0, 4]
    assert planned["scenes"][0]["lines"][2] == "2 do narrator push fridge"
    assert planned["missing_props"] == [] and planned["score"]["score"] <= 100
    assets = {a["name"]: a for a in client.get("/api/v1/assets").json()}
    assert assets["fridge"]["used_in"] == 1
    still = client.get(f"/api/v1/storyboards/{board['id']}/still", params={"t": 1.5, "width": 270})
    assert still.status_code == 200 and still.content[:4] == b"\x89PNG"

    scenes = planned["scenes"]
    scenes[1]["lines"].append("5 bounce snowflake")
    edited = client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes}).json()
    assert edited["scenes"][1]["lines"][-1] == "5 bounce snowflake"
    scenes[1]["lines"].append("5 juggle snowflake")
    bad = client.put(f"/api/v1/storyboards/{board['id']}/scenes", json={"scenes": scenes})
    assert bad.status_code == 400 and "juggle" in bad.json()["detail"]

    job = client.post(f"/api/v1/storyboards/{board['id']}/render").json()
    run_job(job["id"])
    video = animate.video_path({"script_id": sid, "id": board["id"], "format": "short"})
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                                       "-of", "json", str(video)], capture_output=True, text=True, check=True).stdout)
    assert {"width": 1080, "height": 1920} in probe["streams"]


def test_cast_members_are_brought_on_and_props_can_slide():
    shots, dropped = beats.clean([beats.shot(0, lines=["0 draw fridge left", "2 do scientist point fridge",
                                                        "3 move fridge right"])], word_count=10)
    assert shots[0]["lines"][:2] == ["0 draw fridge left", "2 actor scientist right"] and not dropped
    composer = stage.InkComposer({**board(), "scenes": shots}, {"fridge": SVG}, Settings(), None)
    fridge = next(x for x in composer.things if x.id == "fridge")
    assert composer.centre(fridge, 5.0)[0] > composer.centre(fridge, 1.0)[0] + 300


def test_a_prop_comes_along_to_the_next_shot():
    shots, _ = beats.clean([beats.shot(0, lines=["0 draw fridge center"]),
                            beats.shot(4, lines=["5 circle fridge"])], word_count=12)
    composer = stage.InkComposer({**board(), "scenes": shots}, {"fridge": SVG}, Settings(), None)
    fridge = next(x for x in composer.things if x.id == "fridge")
    assert composer.centre(fridge, 1.0)[0] < composer.origins[1][0] < composer.centre(fridge, 4.0)[0]


def test_things_can_move_to_other_things():
    shots, dropped = beats.clean([beats.shot(0, lines=["0 actor narrator left", "0 draw led_chip right as chip",
                                                        "1 draw phosphor_blob top as blob", "2 move blob chip",
                                                        "3 move narrator chip"])], word_count=12)
    assert not dropped
    composer = stage.InkComposer({**board(), "scenes": shots}, {}, Settings(), None)
    things = {x.id: x for x in composer.things}
    chip = composer.body_box(things["chip"], 5.0)
    blob = composer.centre(things["blob"], 5.0)
    assert chip[0] < blob[0] < chip[2] and blob[1] < chip[1]          # it sits on the chip
    assert abs(composer.position(things["narrator"], 5.0)[0] - chip[0]) < 300


def test_the_camera_waits_for_something_to_show():
    shots, _ = beats.clean([beats.shot(0, lines=["0 draw fridge"]), beats.shot(4, lines=["4 erase all",
                                                                                         "9 draw coin"])], 12)
    composer = stage.InkComposer({**board(), "scenes": shots}, {"fridge": SVG, "coin": SVG}, Settings(), None)
    assert composer.arrivals == [0.0, 4.5]
    assert composer.camera(3.0)[0] < composer.origins[1][0]            # still on the fridge after the shot starts
    assert composer.camera(5.5)[0] > composer.origins[1][0]


# --- phase 2: moves, carrying, ground ---------------------------------------------------------

def test_recorded_moves_are_bundled_and_make_sounds():
    moves = ink.moves()
    assert set(beats.ACTIONS) | set(beats.GAITS) <= set(moves)
    for name in beats.GAITS:
        assert moves[name]["loop"] and moves[name]["stride"] > 50       # one stride covers ground
    pose, travelled, air = ink.move_frame("jump", 1.0)
    assert isinstance(pose, ink.Pose) and max(f[11] for f in moves["jump"]["frames"]) > 30
    assert {kind for _, kind in ink.move_cues("jump")} == {"swish", "thud"}
    assert any(kind == "swish" for _, kind in ink.move_cues("kick"))


def test_a_figure_rests_its_lowest_point_on_the_ground():
    standing, crouched = ink.lowest(ink.Pose.named("stand")), ink.lowest(ink.Pose.named("scared"))
    assert standing == pytest.approx(ink.FEET_Y, abs=4) and crouched < standing - 10


def test_gaits_and_actions_in_the_language():
    assert beats.parse_line("12 move narrator right run").render() == "12 move narrator run right"
    assert beats.parse_line("3 do woman cartwheel happy").pose == "cartwheel"
    shots, dropped = beats.clean([beats.shot(0, lines=["0 actor elder left crossed", "1 do elder dance"])], 10)
    assert not dropped and shots[0]["lines"][1] == "1 do elder dance"


def test_carried_things_go_where_the_actor_goes_and_fall_when_dropped():
    plan = ["0 actor narrator left", "0 draw fridge right", "1 do narrator carry fridge",
            "4 move narrator center run", "8 do narrator drop", "10 do narrator kick fridge"]
    shots, dropped = beats.clean([beats.shot(0, lines=plan)], 12)
    assert not dropped
    composer = stage.InkComposer({**board(), "scenes": shots}, {"fridge": SVG}, Settings(), None)
    things = {x.id: x for x in composer.things}
    narrator, fridge = things["narrator"], things["fridge"]
    assert fridge.held and fridge.held[0][1] == pytest.approx(4.0, abs=0.6)    # let go on the drop
    carried = composer.centre(fridge, 3.0)
    hands = composer._hands(narrator, 3.0)
    assert abs(carried[1] - hands[1]) < fridge.box[3] * 0.2                     # held at the hands
    assert any(g == "run" for _, _, g in narrator.gaits)
    fallen = composer.centre(fridge, 4.0 + stage.FALL_TIME + 0.2)
    assert fallen[1] > carried[1]                                                # on the floor now
    assert narrator.acts and narrator.acts[-1][1] == "kick"
    kinds = {k for _, k in composer.sound_events()}
    assert {"lift", "thud", "run_step", "swish"} <= kinds
    composer.bgr(3.0)


def test_a_second_pose_in_a_line_is_ignored():
    line = beats.parse_line("4 do narrator crossed hips smug")
    assert (line.pose, line.expression, line.target) == ("crossed", "smug", "")


# --- montage: flags, maps, timelines, people, cuts ----------------------------------------------

def test_countries_flags_and_maps():
    from clipper.studio import geo
    assert geo.code("Romania") == "ro" and geo.code("uk") == "gb" and geo.code("atlantis") is None
    flag = geo.flag_image("it", 200, 1.0)
    assert flag.getbbox() is not None and flag.width > 200
    early = np.asarray(geo.flag_image("it", 200, 0.2))[..., 3].sum()
    assert early < np.asarray(flag)[..., 3].sum()                    # the colours come in after the border
    clon, clat, slon, slat = geo.map_view(["ro", "bg"])
    assert 20 < clon < 30 and 40 < clat < 48 and slon > 10
    assert geo.map_image(["ro", "bg"], 320, 1.0).size == (320, 240)
    assert geo.timeline_image(["525", "532", "626"], 400, 1.0).getbbox() is not None


def test_the_language_shows_what_is_named():
    assert beats.parse_line("3 flag Romania top_left small").render() == "3 flag ro top_left small"
    assert beats.parse_line("4 map ro bulgaria large as dobruja").render() == "4 map ro bg large as dobruja"
    line = beats.parse_line('5 timeline "525" "532" "626" top warm')
    assert line.extra == ["525", "532", "626"] and line.id == "timeline"
    who = beats.parse_line('6 who dionysius "Dionysius Exiguus" tonsure robe dragon_wings')
    assert who.extra == ["tonsure", "robe"] and who.text == "Dionysius Exiguus"
    for bad in ("7 flag atlantis", "8 map", '9 timeline "only one"', "10 who x"):
        assert beats.parse_line(bad) is None
    shots, dropped = beats.clean([beats.shot(0, lines=['0 who pope "Pope John I" mitre robe', "1 actor pope left",
                                                       "2 flag it right", "3 do pope point flag_it"]),
                                  beats.shot(5, lines=["5 map it center"], cut=True)], 12)
    assert not dropped and shots[0]["lines"][1] == "1 actor pope left" and shots[1]["cut"]
    assert beats.to_text(shots).splitlines()[5] == '5 shot "" cut'


def test_people_are_dressed_and_named_and_cuts_are_instant():
    look = ink.look_of(["mitre", "robe", "long_beard", "white_hair", "wings"])
    assert look == {"hat": "mitre", "outfit": "robe", "facial": "long_beard", "colour": "white_hair"}
    plain = ink.figure_svg("person", ink.Pose.named("stand"))
    dressed = ink.figure_svg("person", ink.Pose.named("stand"), look=look)
    assert len(dressed) > len(plain) + 400
    plan = ['0 who pope "Pope John I" mitre robe', "0 actor pope left", "1 flag it right"]
    shots, _ = beats.clean([beats.shot(0, lines=plan), beats.shot(6, lines=["6 map it center", "7 do pope cheer"],
                                                                    cut=True)], 12)
    composer = stage.InkComposer({**board(), "scenes": shots}, {}, Settings(), None)
    tags = [x for x in composer.things if x.kind == "tag"]
    assert len(tags) == 1 and tags[0].line.text == "Pope John I" and tags[0].gone is not None
    assert composer.pan_start(1)[1] == 0.0                          # a hard cut
    before, after = composer.camera(composer.arrivals[1] - 0.05), composer.camera(composer.arrivals[1] + 0.05)
    assert after[0] - before[0] > composer.W * 0.5
    assert {"pan"}.isdisjoint(k for _, k in composer.sound_events())
    composer.bgr(1.5)


def test_the_wobble_can_be_calmed_or_turned_off():
    calm = stage.InkComposer(board(), {"fridge": SVG, "snowflake": SVG}, Settings(ink_wobble="off"), None)
    assert calm.boil == [] and calm.bgr(1.0).shape == (1080, 1920, 3)
    assert stage.WOBBLE["subtle"][0] < stage.WOBBLE["strong"][0] / 2


def test_a_person_keeps_one_look_and_is_named_once():
    shots, _ = beats.clean([beats.shot(0, lines=['0 who monk "Dionysius" tonsure robe', "0 actor monk left"]),
                            beats.shot(4, lines=['4 who monk "Dionysius" crown suit', "4 actor monk right",
                                                 "5 erase monk"]),
                            beats.shot(8, lines=["8 actor monk left"])], 12)
    composer = stage.InkComposer({**board(), "scenes": shots}, {}, Settings(), None)
    monks = [x for x in composer.things if x.kind == "actor"]
    assert all(m.look == {"hair": "tonsure", "outfit": "robe"} for m in monks)
    assert len([x for x in composer.things if x.kind == "tag"]) == 1
