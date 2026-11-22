"""Source-backed fixed graphics across contracts and renderers."""

import numpy as np
import pytest

from clipper.settings import Settings
from clipper.studio import animate, beats, data_graphics, film, stage, storyboard, wall


def _card(kind: str) -> dict:
    card = {
        "id": f"card_{kind}",
        "kind": kind,
        "label": f"Readable {kind}",
        "unit": "%",
        "source_numbers": [3],
    }
    if kind in {"metric", "gauge", "clock"}:
        card["value"] = 68
    if kind == "gauge":
        card["max_value"] = 100
    if kind in {"comparison", "bars", "cohort", "scorecard"}:
        card["items"] = [{"label": "Before", "value": 42}, {"label": "After", "value": 68}]
    return card


@pytest.mark.parametrize("kind", ["metric", "comparison", "bars", "gauge", "cohort", "clock", "scorecard"])
@pytest.mark.parametrize("size", [(760, 430), (430, 260)])
def test_every_primitive_is_deterministic_and_legible_at_frame_and_lane_sizes(kind: str, size: tuple[int, int]):
    card = data_graphics.Card.model_validate(_card(kind)).model_dump()
    first = np.asarray(data_graphics.render(card, *size))
    second = np.asarray(data_graphics.render(card, *size))
    assert np.array_equal(first, second)
    assert first.shape == (size[1], size[0], 4)
    assert first[..., 3].mean() > 240
    assert first[..., :3].std() > 25


def test_citation_resolution_and_unknown_references_fail_closed():
    good = _card("bars")
    bad = {**_card("metric"), "id": "uncited", "source_numbers": []}
    resolved = data_graphics.resolve([good, bad], {3})
    assert list(resolved.cards) == ["card_bars"]
    assert "no source numbers" in resolved.omissions[0]
    assert data_graphics.referenced("missing", resolved.cards)[0] is None

    ink_plan = beats.parse_plan('0 shot "data"\n0 data missing center', 4, check=False)
    _, dropped = beats.clean(ink_plan, 4, {"card_bars"})
    assert "unknown or is not source-backed" in dropped[0]
    film_plan = film.parse_plan('0 shot "data"\n0 data missing', 4)
    _, dropped = film.clean(film_plan, 4, {"card_bars"})
    assert "unknown or is not source-backed" in dropped[0]


def _words() -> list[dict]:
    return [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.4} for i in range(6)]


def test_data_card_renders_in_flat_ink_and_film_split_lanes():
    card = data_graphics.Card.model_validate(_card("bars")).model_dump()
    cards = {card["id"]: card}
    words = _words()
    element = {
        "kind": "data",
        "name": "",
        "data_card": card["id"],
        "slot": "center",
        "to_slot": None,
        "size": "medium",
        "pose": "stand",
        "expression": "neutral",
        "facing": "right",
        "talking": False,
        "text": "",
        "enter": "none",
        "enter_word": None,
        "motion": "none",
        "lane": "a",
    }
    scene = {
        "start_word": 0,
        "note": "fixed data",
        "background": "paper",
        "camera": "still",
        "composition": {"kind": "split", "divider": "line"},
        "elements": [element],
    }
    cleaned = storyboard.clean([scene], len(words), set(cards))
    flat = animate.Composer(
        {"format": "long", "words": words, "start": 0, "end": 3, "scenes": cleaned, "data_cards": cards},
        {},
        Settings(captions_enabled=False),
        None,
    )
    assert np.asarray(flat.frame(1.0))[..., :3].std() > 20

    ink_scenes = beats.parse_plan(
        f'0 shot "fixed data" split line\n0 data {card["id"]} center lane a', len(words), check=False
    )
    ink_scenes, dropped = beats.clean(ink_scenes, len(words), set(cards))
    assert not dropped
    ink = stage.InkComposer(
        {
            "format": "long",
            "words": words,
            "start": 0,
            "end": 3,
            "scenes": ink_scenes,
            "headings": {},
            "data_cards": cards,
        },
        {},
        Settings(captions_enabled=False, ink_wobble="off"),
        None,
    )
    assert ink.frame_rgb(1.0).std() > 20

    film_scenes = film.parse_plan(f'0 shot "fixed data" split line\n0 data {card["id"]} lane a', len(words))
    film_scenes, dropped = film.clean(film_scenes, len(words), set(cards))
    assert not dropped
    film_composer = wall.FilmComposer(
        {
            "format": "long",
            "words": words,
            "start": 0,
            "end": 3,
            "scenes": film_scenes,
            "photos": {},
            "look": "studio",
            "data_cards": cards,
        },
        {},
        Settings(captions_enabled=False),
    )
    assert film_composer.frame_rgb(1.0).std() > 20
