"""Glow-look and per-board caption override regressions."""

import numpy as np
import pytest
from PIL import Image
from test_storyboard import SVG

from clipper import captions
from clipper.settings import Settings, resolve_font
from clipper.studio import animate, beats, composition, film, looks, stage, wall


def _words() -> list[dict]:
    return [
        {"w": word, "s": index * 0.45, "e": index * 0.45 + 0.35}
        for index, word in enumerate("A clear fixed caption stays inside its safe lower third".split())
    ]


def _element(lane: str | None = None) -> dict:
    value = {
        "kind": "text",
        "name": "",
        "slot": "center",
        "to_slot": None,
        "size": "medium",
        "pose": "stand",
        "expression": "neutral",
        "facing": "right",
        "talking": False,
        "text": "CLEAR",
        "enter": "none",
        "enter_word": None,
        "motion": "none",
    }
    if lane:
        value["lane"] = lane
    return value


@pytest.mark.parametrize("fmt", ["short", "long"])
@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("mode", ["on", "off"])
def test_caption_override_controls_layout_for_every_shape_and_composition(fmt: str, split: bool, mode: str):
    spec = {"kind": "split", "divider": "line"} if split else {"kind": "single"}
    elements = [_element("a"), _element("b")] if split else [_element()]
    scene = {
        "start_word": 0,
        "note": "caption layout",
        "background": "paper",
        "camera": "still",
        "composition": spec,
        "elements": elements,
    }
    board = {"format": fmt, "caption_mode": mode, "words": _words(), "start": 0.0, "end": 4.5, "scenes": [scene]}
    composer = animate.Composer(board, {}, Settings(captions_enabled=False), None)
    assert (composer.captions is not None) is (mode == "on")
    geo = composer.compositions[0]
    expected = geo.height * (0.665 if fmt == "short" else (0.82 if mode == "on" else 1.0))
    assert geo.caption_top == pytest.approx(expected)
    assert all(view.y1 <= geo.caption_top for view in geo.lanes)
    if split:
        for element, (x, y, scale) in zip(elements, composer.placement(0), strict=True):
            sprite = composer.base_sprite(element, False)
            box = (
                x - sprite.width * scale / 2,
                y - sprite.height * scale / 2,
                x + sprite.width * scale / 2,
                y + sprite.height * scale / 2,
            )
            assert composition.contains(geo.viewport(element["lane"]), box)


def test_default_caption_mode_is_legacy_and_explicit_on_overrides_global_setting():
    assert captions.resolved_mode("short", "default", True)
    assert not captions.resolved_mode("long", "default", True)
    assert not captions.resolved_mode("short", "default", False)
    assert captions.resolved_mode("long", "on", False)
    assert not captions.resolved_mode("short", "off", True)


def test_horizontal_caption_overlay_stays_inside_lower_safe_band():
    groups = captions.group_words(_words(), 0, 4.5)
    renderer = captions.CaptionRenderer(groups, str(resolve_font(Settings())), fmt="horizontal")
    overlay = renderer._render(groups[0], 0)
    layout = captions.LAYOUTS["horizontal"]
    assert 0 <= overlay.x and overlay.x + overlay.w <= layout.width
    assert layout.height * 0.82 <= overlay.y < overlay.y + overlay.h <= layout.height


def test_ink_and_film_long_renderers_receive_horizontal_caption_reserve():
    words = _words()
    ink_scenes = beats.parse_plan(
        '0 shot "caption" split line\n0 write "A" center lane a\n1 write "B" center lane b', len(words)
    )
    ink = stage.InkComposer(
        {
            "format": "long",
            "caption_mode": "on",
            "words": words,
            "start": 0,
            "end": 4.5,
            "scenes": ink_scenes,
            "headings": {},
            "look": "ink",
        },
        {},
        Settings(captions_enabled=False, ink_wobble="off"),
        None,
    )
    assert ink.captions is not None and ink.compositions[0].caption_top == pytest.approx(1080 * 0.82)

    film_scenes = film.parse_plan('0 shot "caption" split line\n0 big "A" lane a\n1 big "B" lane b', len(words))
    film_scenes, dropped = film.clean(film_scenes, len(words))
    assert not dropped
    documentary = wall.FilmComposer(
        {
            "format": "long",
            "caption_mode": "on",
            "words": words,
            "start": 0,
            "end": 4.5,
            "scenes": film_scenes,
            "photos": {},
            "look": "studio",
        },
        {},
        Settings(captions_enabled=False),
    )
    assert documentary.captions is not None
    assert documentary.compositions[0].caption_top == pytest.approx(1080 * 0.82)


def test_glow_keeps_plan_and_raster_colours_while_adding_a_bounded_halo():
    look = looks.get("glow")
    assert look.background_kind == "dark_neutral" and look.glow
    sprite = Image.new("RGBA", (80, 50), (0, 0, 0, 0))
    sprite.paste((24, 25, 25, 255), (18, 20, 62, 28))
    profiled = looks.profiled_sprite(sprite, look)
    visible = np.asarray(profiled)[..., 3] > 0
    assert np.asarray(profiled)[..., :3][visible].mean() > 150
    halo = looks.glow_understroke(profiled)
    assert halo is not None and halo.size == profiled.size and np.asarray(halo)[..., 3].max() > 0

    plan = beats.parse_plan('0 shot "glow"\n0 actor narrator left\n1 draw fridge right', len(_words()))
    board = {
        "format": "long",
        "caption_mode": "off",
        "words": _words(),
        "start": 0,
        "end": 4.5,
        "scenes": plan,
        "headings": {},
        "look": "glow",
    }
    composer = stage.InkComposer(board, {"fridge": SVG}, Settings(captions_enabled=False, ink_wobble="off"), None)
    original = Image.new("RGBA", (24, 20), (12, 91, 203, 255))
    assert np.array_equal(np.asarray(composer._dress("photo", original, 1.0)), np.asarray(original))
    frame = np.asarray(composer.frame(2.0).convert("RGB"))
    assert composer.scenes is board["scenes"] and tuple(composer.paper.getpixel((0, 0))[:3]) == (20, 23, 27)
    assert frame.mean() < 80 and frame.max() > 220
