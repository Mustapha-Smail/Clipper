"""Shared split-composition contracts and renderer regressions."""

import numpy as np
import pytest

from clipper.settings import Settings
from clipper.studio import animate, beats, composition, film, stage, storyboard, wall


def _words(n: int = 8) -> list[dict]:
    return [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.4} for i in range(n)]


def _element(lane: str, text: str) -> dict:
    return {
        "kind": "text", "name": "", "slot": "center", "to_slot": None,
        "size": "medium", "pose": "stand", "expression": "neutral", "facing": "right",
        "talking": False, "text": text, "enter": "none", "enter_word": None,
        "motion": "none", "lane": lane,
    }


@pytest.mark.parametrize("fmt,axis", [("long", "x"), ("short", "y")])
def test_shared_geometry_keeps_lanes_out_of_captions(fmt: str, axis: str):
    geo = composition.geometry(fmt, {"kind": "lanes", "divider": "vs"}, captions=True)
    assert geo.kind == "split" and geo.divider == "vs" and [v.lane for v in geo.lanes] == ["a", "b"]
    assert all(0 <= v.x0 < v.x1 <= geo.width and 0 <= v.y0 < v.y1 <= geo.caption_top for v in geo.lanes)
    if axis == "x":
        assert geo.lanes[0].x1 < geo.lanes[1].x0 and geo.caption_top == pytest.approx(0.82 * geo.height)
    else:
        assert geo.lanes[0].y1 < geo.lanes[1].y0 and geo.caption_top == pytest.approx(0.665 * geo.height)


def test_ink_split_ids_are_canonical_and_round_trip_without_cross_lane_refs():
    raw = beats.parse_plan(
        '0 shot "compare" split vs\n'
        '0 actor narrator center as host lane a\n'
        '1 actor narrator center as host lane b\n'
        '2 do host cheer lane b\n'
        '3 do host point lane a',
        8,
    )
    assert raw[0]["lines"] == [
        "0 actor narrator center as host lane a",
        "1 actor narrator center as host_2 lane b",
        "2 do host_2 cheer lane b",
        "3 do host point lane a",
    ]
    text = beats.to_text(raw)
    assert 'shot "compare" split vs' in text
    assert beats.parse_plan(text, 8) == raw
    _, dropped = beats.clean([beats.shot(0, lines=["0 draw coin lane a", "1 circle coin lane b"],
                                                  composition_spec={"kind": "split"})], 8)
    assert dropped and "nothing called coin" in dropped[0]


def test_film_split_ids_are_canonical_and_round_trip_without_cross_lane_refs():
    raw = film.parse_plan(
        '0 shot "compare" split line\n'
        '0 big "SAME" as fact lane a\n'
        '1 big "SAME" as fact lane b\n'
        '2 circle fact lane b',
        8,
    )
    clean, dropped = film.clean(raw, 8)
    assert not dropped
    assert clean[0]["lines"] == [
        '0 big "SAME" as fact lane a',
        '1 big "SAME" as fact_2 lane b',
        "2 circle fact_2 lane b",
    ]
    assert film.parse_plan(film.to_text(clean), 8) == clean


@pytest.mark.parametrize("fmt", ["long", "short"])
def test_flat_renderer_keeps_each_sprite_inside_its_lane(fmt: str):
    scene = {
        "start_word": 0, "note": "compare", "background": "paper", "camera": "zoom_in",
        "composition": {"kind": "split", "lanes": ["a", "b"], "divider": "vs"},
        "elements": [_element("a", "BEFORE"), _element("b", "AFTER")],
    }
    board = {"format": fmt, "words": _words(), "start": 0.0, "end": 4.0, "scenes": [scene]}
    composer = animate.Composer(board, {}, Settings(captions_enabled=False), None)
    geo = composer.compositions[0]
    for element, (x, y, scale) in zip(scene["elements"], composer.placement(0), strict=True):
        sprite = composer.base_sprite(element, False)
        box = (x - sprite.width * scale / 2, y - sprite.height * scale / 2,
               x + sprite.width * scale / 2, y + sprite.height * scale / 2)
        assert composition.contains(geo.viewport(element["lane"]), box)
    image = np.asarray(composer.frame(2.0))
    assert image.shape[:2] == (geo.height, geo.width)
    if fmt == "long":
        divider = image[:, geo.width // 2, :3]
    else:
        divider = image[round((geo.lanes[0].y1 + geo.lanes[1].y0) / 2), :, :3]
    assert divider.std() > 0 or divider.mean() < 180


def test_ink_and_film_renderers_keep_split_camera_and_divider_stable():
    words = _words()
    ink_scenes = beats.parse_plan(
        '0 shot "compare" split line\n0 write "BEFORE" center lane a\n1 write "AFTER" center lane b', 8)
    ink_board = {"format": "long", "words": words, "start": 0.0, "end": 4.0, "scenes": ink_scenes,
                 "headings": {}}
    ink_composer = stage.InkComposer(ink_board, {}, Settings(captions_enabled=False, ink_wobble="off"), None)
    assert ink_composer.camera(2.0)[2] == pytest.approx(1.0)
    ink_frame = ink_composer.frame_rgb(2.0)
    assert ink_frame.shape == (1080, 1920, 3)
    assert ink_frame[:, 960, :3].mean() < ink_frame[:, :100, :3].mean()

    film_raw = film.parse_plan(
        '0 shot "compare" split line\n0 big "BEFORE" lane a\n1 big "AFTER" lane b', 8)
    film_scenes, dropped = film.clean(film_raw, 8)
    assert not dropped
    film_board = {"format": "long", "words": words, "start": 0.0, "end": 4.0, "scenes": film_scenes,
                  "photos": {}, "look": "studio"}
    film_composer = wall.FilmComposer(film_board, {}, Settings(captions_enabled=False))
    assert film_composer.keys[0].split
    film_frame = film_composer.frame_rgb(2.0)
    assert film_frame.shape == (1080, 1920, 3)
    assert film_frame[:, 960, :3].mean() < film_frame[:, :100, :3].mean()


def test_flat_clean_preserves_legacy_single_plans_and_normalizes_split_lanes():
    legacy = {"start_word": 0, "note": "", "background": "paper", "camera": "still",
              "elements": [_element("a", "OLD")]}
    single = storyboard.clean([legacy], 4)[0]
    assert "composition" not in single and "lane" not in single["elements"][0]
    split = {**legacy, "elements": [_element("a", "A"), _element("b", "B")],
             "composition": {"kind": "lanes", "divider": "line"}}
    cleaned = storyboard.clean([split], 4)[0]
    assert cleaned["composition"] == {"kind": "split", "lanes": ["a", "b"], "divider": "line"}
