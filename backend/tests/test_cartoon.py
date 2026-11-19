"""The cartoon look: settings on shot lines, places drawn by code or by Claude, colour characters."""

import numpy as np
import pytest
from test_storyboard import SVG

from clipper import llm
from clipper.db import session_scope
from clipper.db.models import Idea, Script, Storyboard, StudioPlace
from clipper.settings import Settings
from clipper.studio import beats, cartoon, ink, places, stage

PLAN = """0 shot "The narrator in his study" in study
0 actor narrator left talk
2 draw fridge right
4 shot "Out in the town" cut in Town
4 do narrator cheer
7 shot "Still in town"
7 write "42" top big
9 shot "The monks" in scriptorium
9 actor narrator center"""

WIDE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2400 1080">'
        '<rect width="2400" height="1080" fill="#abc"/></svg>')
TALL = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1500 1920">'
        '<rect width="1500" height="1920" fill="#cba"/></svg>')


def board(fmt: str = "long", look: str = "cartoon") -> dict:
    words = [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.4} for i in range(12)]
    shots = beats.parse_plan(PLAN, 12)
    return {"format": fmt, "words": words, "start": 0.0, "end": 6.2, "scenes": shots, "headings": {}, "look": look,
            "places": places.resolved(shots), "place_svgs": {"scriptorium": WIDE if fmt == "long" else TALL}}


def test_settings_are_read_kept_and_carried_forward():
    shots = beats.parse_plan(PLAN, 12)
    assert [s["place"] for s in shots] == ["study", "town", "", "scriptorium"]
    assert places.resolved(shots) == ["study", "town", "town", "scriptorium"]
    assert beats.place_names(shots) == ["study", "town", "scriptorium"]
    again = beats.parse_plan(beats.to_text(shots), 12)
    assert [s["place"] for s in again] == ["study", "town", "", "scriptorium"]
    assert beats.shot(0, place="Not A Name!")["place"] == "not_a_name"
    assert places.resolved([beats.shot(0), beats.shot(3, place="night")]) == ["night", "night"]


@pytest.mark.parametrize("variant,traits", [("narrator", []), ("person", []), ("elder", []),
                                            ("person", ["tonsure", "robe"]), ("person", ["crown", "beard", "cape"]),
                                            ("person", ["suit", "top_hat", "dark_skin"])])
def test_colour_characters_draw_on_the_ink_rig(variant, traits):
    for pose in ("stand", "push", "cheer", "scared"):
        svg = cartoon.figure_svg(variant, ink.Pose.named(pose), "happy", 1, False, "left", 0.5,
                                 ink.look_of(traits) or None)
        image = np.asarray(ink.render(svg))
        assert image[..., 3].sum() > 0 and image[..., :3][image[..., 3] > 200].std() > 10   # in colour


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1920)])
def test_places_are_drawn_larger_than_the_frame_in_both_shapes(size):
    for name in cartoon.PLACES:
        picture = cartoon.place_image(name, *size)
        assert picture.size == cartoon.picture_size(*size)
        assert picture.width > size[0] and picture.height > size[1]
    stored = cartoon.place_image("scriptorium", *size, WIDE if size[0] > size[1] else TALL)
    assert stored.size == cartoon.picture_size(*size)


@pytest.mark.parametrize("fmt", ["long", "short"])
def test_a_cartoon_frame_has_its_place_behind_and_cards_under_words(fmt):
    composer = stage.InkComposer(board(fmt), {"fridge": SVG}, Settings(), None)
    assert composer.look.cartoon and not composer.boil
    frame = np.asarray(composer.frame(1.0).convert("RGB"))
    assert frame.std() > 20                                   # a drawn place, not plain paper
    town = np.asarray(composer.frame(4.4).convert("RGB")).astype(int)
    scriptorium = np.asarray(composer.frame(5.6).convert("RGB")).astype(int)
    corner = (slice(0, 40), slice(0, 40))
    assert np.abs(town[corner].mean((0, 1)) - scriptorium[corner].mean((0, 1))).max() > 10
    ink_look = stage.InkComposer(board(fmt, "ink"), {"fridge": SVG}, Settings(), None)
    assert np.asarray(ink_look.frame(1.0).convert("RGB")).std() < frame.std()


def test_older_storyboards_get_settings_and_new_places_get_drawn(app_env, monkeypatch):
    def fake_ask(task, system, content, output_format, effort="high", cache=False):
        assert task == "places_assign" and "<place_library>" in system and "1. The narrator" in content
        return output_format(places=["study", "roman_forum", "roman_forum"],
                             new_places=[{"name": "roman_forum", "description": "Columns and a temple, AD 100"}])

    drawn = []

    def fake_draw(name, description, tall, instruction=""):
        drawn.append((name, description, tall))
        return TALL if tall else WIDE

    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr(places, "draw", fake_draw)
    shots = [{**sh, "place": ""} for sh in beats.parse_plan(PLAN, 12)]
    with session_scope() as db:
        script = Script(idea_id=None, title="x", sections=[])
        db.add(script)
        db.flush()
        b = Storyboard(script_id=script.id, format="short", style="ink", look="cartoon", title="x",
                       words=board()["words"], scenes=shots)
        db.add(b)
        db.flush()
        board_id = b.id

    class Rep:
        def info(self, *a):
            pass
        warn = progress = info

    places.prepare(board_id, Rep())
    with session_scope() as db:
        b = db.get(Storyboard, board_id)
        assert places.resolved(b.scenes) == ["study", "roman_forum", "roman_forum", "roman_forum"]
        assert [s["place"] for s in b.scenes] == ["study", "roman_forum", "", ""]
        forum = db.get(StudioPlace, "roman_forum")
        assert forum.svg_tall == TALL and forum.svg is None and "temple" in forum.description
    assert drawn == [("roman_forum", "Columns and a temple, AD 100", True)]
    places.prepare(board_id, Rep())            # nothing left to do: no second drawing
    assert len(drawn) == 1
    with pytest.raises(ValueError):
        places.check(WIDE, tall=True)


def test_places_in_the_api(client, app_env):
    listed = client.get("/api/v1/places").json()
    assert [p["name"] for p in listed][:6] == list(cartoon.PLACES) and all(p["built_in"] for p in listed)
    image = client.get("/api/v1/places/night/image", params={"shape": "tall", "size": 120})
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.delete("/api/v1/places/night").status_code == 404

    with session_scope() as db:
        idea = Idea(title="x")
        db.add(idea)
        db.flush()
        script = Script(idea_id=idea.id, title="x", sections=[])
        db.add(script)
        db.flush()
        b = Storyboard(script_id=script.id, format="long", style="ink", title="x", words=board()["words"],
                       scenes=beats.parse_plan(PLAN, 12))
        db.add(b)
        db.flush()
        board_id = b.id
    assert client.patch(f"/api/v1/storyboards/{board_id}", json={"look": "cartoon"}).json()["look"] == "cartoon"
    shots = client.get(f"/api/v1/storyboards/{board_id}").json()["scenes"]
    shots[1]["place"] = "Lighthouse"
    assert client.put(f"/api/v1/storyboards/{board_id}/scenes", json={"scenes": shots}).status_code == 200
    names = {p["name"]: p for p in client.get("/api/v1/places").json()}
    assert not names["lighthouse"]["wide"] and names["lighthouse"]["used_in"] == 1
    assert client.get("/api/v1/places/lighthouse/image").status_code == 404


def test_a_code_place_can_be_redrawn_and_put_back(client, app_env, monkeypatch):
    asked = []

    def fake_draw(name, description, tall, instruction=""):
        asked.append((name, description, tall, instruction))
        return TALL if tall else WIDE

    monkeypatch.setattr(places, "draw", fake_draw)
    code_png = client.get("/api/v1/places/night/image", params={"size": 120}).content
    out = client.post("/api/v1/places/night/redraw", json={"instruction": "more stars"}).json()
    assert asked == [("night", cartoon.BUILT_IN["night"], False, "more stars")]
    assert out["built_in"] and out["redrawn"] == ["wide"] and out["origin"] == "claude"
    assert client.get("/api/v1/places/night/image", params={"size": 120}).content != code_png
    # only the shape Claude drew replaces the code: a Short still gets the code's tall night
    assert places.drawings(["night"], tall=False) == {"night": WIDE}
    assert places.drawings(["night"], tall=True) == {}
    listed = client.get("/api/v1/places").json()
    assert [p["name"] for p in listed].count("night") == 1
    assert client.delete("/api/v1/places/night").status_code == 204
    night = next(p for p in client.get("/api/v1/places").json() if p["name"] == "night")
    assert night["redrawn"] == [] and night["origin"] == "code"
    assert client.get("/api/v1/places/night/image", params={"size": 120}).content == code_png
    assert client.delete("/api/v1/places/night").status_code == 404
