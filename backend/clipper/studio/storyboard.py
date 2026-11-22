"""Storyboards: Claude turns a timed stretch of narration into scenes, and draws the props.

A storyboard covers one stretch of a voice take: one section for a Short, the
whole script for a long video. Its words keep the times Whisper found, so a
scene starts on the word it names, never on a guessed timestamp (the same rule
as moment selection: Claude answers with word numbers).

Two styles. "ink" (the default) is planned in the beat language of `beats.py`:
shots on one long page where actors act on things, filmed by `stage.py`.
"flat" is the first style: scenes of slots filled with stickers, as JSON,
filmed by `animate.py`. Both use the same vocabulary of cast and props. Props
the library lacks are drawn by Claude as SVG in the house style, checked, and
kept for every later video; the ink style redraws them as ink lines and wash.
"""

import base64
import io
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import config, llm, prompts
from ..db import session_scope
from ..db.models import Storyboard, StudioAsset, StudioPhoto, VoiceTake, utcnow
from ..jobs import Reporter
from . import art, beats, composition, data_graphics, film, hold, ink
from . import places as places_mod
from . import voice as voice_mod

log = logging.getLogger("clipper.studio")

Slot = Literal["left", "center", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"]
PROP_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
MAX_ELEMENTS = 4


class Element(BaseModel):
    kind: Literal["character", "prop", "text", "number", "arrow", "data"]
    name: str = Field("", description="character: a cast name. prop: a prop name in snake_case. Else empty")
    slot: Slot
    to_slot: Slot | None = Field(None, description="arrow only: where it points")
    size: Literal["small", "medium", "large"] = "medium"
    pose: Literal[art.POSES] = "stand"
    expression: Literal[art.EXPRESSIONS] = "neutral"
    facing: Literal["left", "right"] = "right"
    talking: bool = Field(False, description="character only: mouth moves with the narration")
    text: str = Field(
        "",
        description="text or number: what to show (at most 5 words). prop: an optional label under it (1 to 3 words)",
    )
    data_card: str = Field("", max_length=32, description="data only: stable ID of a cited story-map data card")
    enter: Literal["pop", "slide", "fade", "none"] = "pop"
    enter_word: int | None = Field(
        None, description="Number of the word on which this element appears; null to appear with the scene"
    )
    motion: Literal["none", "bounce", "shake", "pulse", "float", "spin", "grow"] = "none"
    lane: Literal["a", "b"] | None = Field(None, description="split scene lane; omit for the default lane")


class CompositionSpec(BaseModel):
    """Renderer-independent scene composition.

    ``lanes`` remains accepted for hand-authored plans, but clean() emits the
    canonical ``split`` spelling so all renderers share one contract.
    """

    kind: Literal["single", "split", "lanes"] = "single"
    lanes: list[Literal["a", "b"]] = Field(default_factory=list)
    divider: Literal["none", "line", "vs"] = "none"


class Scene(BaseModel):
    start_word: int = Field(description="Number of the word on which the scene starts")
    note: str = Field(description="One sentence: what the viewer sees and why it is funny or clear")
    background: Literal[art.BACKGROUNDS] = "paper"
    camera: Literal["still", "zoom_in", "zoom_out", "pan_left", "pan_right"] = "still"
    elements: list[Element]
    composition: CompositionSpec = Field(default_factory=CompositionSpec)


class PropRequest(BaseModel):
    name: str = Field(description="snake_case and specific, e.g. roman_denarius rather than coin")
    description: str = Field(
        description="For an illustrator, in one or two sentences: the object, its era and "
        "material, its colours, and the detail that makes it recognisable in "
        "this story"
    )


class Plan(BaseModel):
    scenes: list[Scene]
    new_props: list[PropRequest] = Field(description="Every prop used in the scenes that is not in the library")


# --- planning ----------------------------------------------------------------------------

PLAN = prompts.Prompt("storyboard_flat")
MICRO_PLAN = prompts.Prompt("storyboard_micro_short")

FORMAT_RULES = {fmt: prompts.Prompt(f"storyboard_flat_{fmt}") for fmt in ("short", "long")}

CAST = "\n".join(f"- {name}: {description}" for name, description in art.CHARACTERS.items())


def numbered_words(words: list[dict]) -> str:
    """Words as '[n] word' with a timestamp every sentence, compact enough for long scripts."""
    out, sentence_start = [], True
    for i, w in enumerate(words):
        prefix = f"\n({w['s']:.1f}s) " if sentence_start else ""
        out.append(f"{prefix}[{i}] {w['w']}")
        sentence_start = w["w"].rstrip("\"')”").endswith((".", "?", "!"))
    return " ".join(out).strip()


# --- the turns of the story, from the script's hold check ------------------------------------

MAX_BIG = 3  # emphasised words on screen in a whole video: the payoff and at most two other key beats


def turn_marks(words: list[dict], sections: list[dict], stored: dict | None) -> tuple[dict[int, str], str]:
    """The storyboard word numbers where a twist, rehook or payoff sentence starts, from the hold check.

    Empty, with the reason, when the check is missing or out of date, or when the take's words no longer
    match the script's text (so a number would point at the wrong word)."""
    found = hold.turns(sections, stored)
    if found is None:
        return {}, "the script's hold check is missing or out of date"
    by_section: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        by_section.setdefault(w.get("section"), []).append(i)
    texts = {s["key"]: s["text"].split() for s in sections}
    usable = {key for key, idx in by_section.items() if key in texts and [words[i]["w"] for i in idx] == texts[key]}
    marks = {
        by_section[t["section"]][t["index"]]: t["role"]
        for t in found
        if t["section"] in usable and t["index"] < len(by_section[t["section"]])
    }
    if not marks and by_section.keys() - usable:
        return {}, "the voice take's words no longer match the script"
    return marks, ""


def turns_block(marks: dict[int, str], offset: int, size: int) -> str:
    """The turns inside one chapter, numbered like its narration (from 0), for the planner."""
    inside = sorted((i - offset, role) for i, role in marks.items() if offset <= i < offset + size)
    if not inside:
        return ""
    return "<turns>\n" + "\n".join(f"[{i}] {role}" for i, role in inside) + "\n</turns>\n\n"


def _report_turns(marks: dict[int, str], why: str, rep: Reporter) -> None:
    if marks:
        counts = {r: sum(1 for x in marks.values() if x == r) for r in hold.MARKED}
        rep.info(
            "Planning on the script's turns: "
            + ", ".join(f"{n} {r}{'s' if n > 1 else ''}" for r, n in counts.items() if n)
        )
    else:
        rep.info(f"Planning without the script's turns: {why or 'the hold check marked none'}")


def limit_big(shots: list[dict], parse, flag: str, marks: dict[int, str]) -> list[str]:
    """Keep the emphasis (`flag`: ink's `big`, film's `bar`) on at most MAX_BIG lines in the video: the payoff's
    first, then the other turns, then the earliest. The others keep their text at normal size.

    Returns the texts that lost their emphasis."""
    found = []
    for si, shot in enumerate(shots):
        for li, raw in enumerate(shot.get("lines", [])):
            line = parse(raw)
            if line is not None and getattr(line, flag, False):
                found.append((si, li, line))
    if len(found) <= MAX_BIG:
        return []
    payoff = min((i for i, r in marks.items() if r == "payoff"), default=None)
    turn_words = set(marks)

    def rank(item: tuple) -> tuple:
        line = item[2]
        if payoff is not None and line.word >= payoff:
            return (0, line.word)
        if line.word in turn_words:
            return (1, line.word)
        return (2, line.word)

    if not marks:  # no check: the last one (likely the payoff) and the first two
        keep = {id(found[-1][2]), id(found[0][2]), id(found[1][2])}
    else:
        keep = {id(item[2]) for item in sorted(found, key=rank)[:MAX_BIG]}
    lost = []
    for si, li, line in found:
        if id(line) not in keep:
            setattr(line, flag, False)
            shots[si]["lines"][li] = line.render()
            lost.append(line.text)
    return lost


def _report_big(lost: list[str], rep: Reporter) -> None:
    if lost:
        rep.info(
            f"{len(lost)} big words shown at normal size (big text is kept for the payoff and at most "
            f"{MAX_BIG - 1} other key beats): " + ", ".join(f'"{t}"' for t in lost[:5])
        )


def library() -> dict[str, str]:
    """The props the planner may reuse: yours, and those drawn in the current illustrated style.

    Older flat drawings are left out, so the planner asks for them again and they are redrawn."""
    with session_scope() as db:
        return {
            a.name: a.description
            for a in db.scalars(select(StudioAsset).order_by(StudioAsset.name))
            if a.origin == "you" or art.current_style(a.svg)
        }


def clean(scenes: list[dict], word_count: int, data_cards: set[str] | None = None) -> list[dict]:
    """Make a plan safe to render: ordered scenes, valid word numbers, known names, few elements."""
    out = []
    last = -1
    for scene in sorted(scenes, key=lambda s: s["start_word"]):
        start = max(0, min(scene["start_word"], word_count - 1))
        if start <= last and out:
            continue
        last = start
        spec = composition.normalize(scene.get("composition"))
        elements = []
        for e in scene["elements"]:
            if e["kind"] == "character" and e["name"] not in art.CHARACTERS:
                e["name"] = "person"
            if e["kind"] == "prop":
                e["name"] = re.sub(r"[^a-z0-9_]", "_", e["name"].lower()).strip("_")[:40] or "thing"
            if e["kind"] == "arrow" and not e.get("to_slot"):
                continue
            if e["kind"] in ("text", "number") and not e["text"].strip():
                continue
            if e["kind"] == "data" and (
                not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", e.get("data_card", ""))
                or (data_cards is not None and e["data_card"] not in data_cards)
            ):
                continue
            if e.get("enter_word") is not None:
                e["enter_word"] = max(start, min(e["enter_word"], word_count - 1))
            lane = str(e.get("lane") or "").lower()
            if lane not in {"a", "b"}:
                e.pop("lane", None)
            elif spec["kind"] != "split":
                # A lane modifier in a single scene is invalid, but dropping
                # the modifier keeps the otherwise valid element usable.
                e.pop("lane", None)
            else:
                e["lane"] = lane
            elements.append(e)
        item = {**scene, "start_word": start, "elements": elements[:MAX_ELEMENTS]}
        if scene.get("composition") is not None or spec["kind"] != "single":
            item["composition"] = spec
        else:
            item.pop("composition", None)
        out.append(item)
    if out:
        out[0]["start_word"] = 0
    return out


def _chunks(words: list[dict]) -> list[tuple[int, list[dict], str | None]]:
    """Split the words into chapters (sections) for planning: (offset, words, section key)."""
    chunks: list[tuple[int, list[dict], str | None]] = []
    for i, w in enumerate(words):
        key = w.get("section")
        if not chunks or chunks[-1][2] != key:
            chunks.append((i, [], key))
        chunks[-1][1].append(w)
    return chunks


def _timed_input(words: list[dict], micro: bool) -> str:
    tag = "cue_sheet" if micro else "narration"
    return f"<{tag}>\n{numbered_words(words)}\n</{tag}>"


def _plan_chunk(
    system: str, title: str, props: str, words: list[dict], chapter: str, turns: str = "", micro: bool = False
) -> Plan:
    chapter_line = f"<chapter>{chapter}</chapter>\n\n" if chapter else ""
    prompt = (
        f"<video_title>{title}</video_title>\n\n{chapter_line}<cast>\n{CAST}\n</cast>\n\n"
        f"<prop_library>\n{props}\n</prop_library>\n\n{turns}{_timed_input(words, micro)}"
    )
    return llm.ask("storyboard", system, prompt, Plan, effort="high")


def prop_names(style: str, scenes: list[dict]) -> set[str]:
    """Every library prop a storyboard uses."""
    if style == "ink":
        return beats.prop_names(scenes)
    if style == "film":
        return set()
    return {e["name"] for s in scenes for e in s.get("elements", []) if e["kind"] == "prop"}


def plan(storyboard_id: int, rep: Reporter) -> None:
    """Plan the scenes. A long video is planned one chapter at a time, so no answer gets too long."""
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        words, fmt, title, style = list(board.words), board.format, board.title, board.style
        micro = board.form == "micro_short"
        headings = {s["key"]: s["heading"] for s in board.script.sections}
        marks, why = turn_marks(words, board.script.sections, board.script.hold)
        cards = data_graphics.stored(board.script.story_map)
    if micro:
        rep.info("Planning from designed silent cue tokens; no narration or word captions")
    else:
        _report_turns(marks, why, rep)
    if style == "ink":
        plan_ink(storyboard_id, words, fmt, title, headings, rep, marks, cards, micro=micro)
        return
    if style == "film":
        plan_film(storyboard_id, words, fmt, title, headings, rep, marks, cards, micro=micro)
        return
    known = library()
    system = PLAN.format(max_elements=MAX_ELEMENTS, format_rules=FORMAT_RULES[fmt])
    if micro:
        system += "\n\n" + str(MICRO_PLAN)
    if cards:
        system += "\n\n" + data_graphics.prompt_block(cards)
    props = "\n".join(f"- {name}: {desc}" for name, desc in known.items()) or "(empty so far)"
    chunks = _chunks(words) if fmt == "long" else [(0, words, None)]
    rep.info(
        f"Planning scenes for {words[-1]['e']:.0f} seconds of {'designed visual cues' if micro else 'narration'}"
        + (f", chapter by chapter ({len(chunks)})" if len(chunks) > 1 else "")
    )

    def run(chunk: tuple[int, list[dict], str | None]) -> tuple[int, int, Plan]:
        offset, part, key = chunk  # the model numbers the chapter's words from 0; shifted back below
        chapter = headings.get(key, "") if key else ""
        turns = turns_block(marks, offset, len(part))
        planned = (
            _plan_chunk(system, title, props, part, chapter, turns, micro=True)
            if micro
            else _plan_chunk(system, title, props, part, chapter, turns)
        )
        return (
            offset,
            len(part),
            planned,
        )

    results, done = [], 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(run, chunks):
            results.append(result)
            done += 1
            rep.progress(done / len(chunks), f"Planned {done} of {len(chunks)} chapters")

    scenes, descriptions = [], {}
    for offset, size, result in results:
        part = clean([s.model_dump() for s in result.scenes], size, set(cards))
        for scene in part:
            scene["start_word"] += offset
            for e in scene["elements"]:
                if e.get("enter_word") is not None:
                    e["enter_word"] += offset
        if part:
            part[0]["start_word"] = offset  # every chapter starts its own scene
        scenes.extend(part)
        descriptions.update({p.name: p.description for p in result.new_props})
    scenes = clean(scenes, len(words), set(cards))
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.scenes = scenes
        board.video_path = board.published_at = None
        for name in {e["name"] for s in scenes for e in s["elements"] if e["kind"] == "prop"}:
            if db.get(StudioAsset, name) is None:
                db.add(StudioAsset(name=name, description=descriptions.get(name, name.replace("_", " "))))
    used = sum(len(s["elements"]) for s in scenes)
    rep.info(f"{len(scenes)} scenes, {used} elements, {len(descriptions)} new props to draw")


# --- planning in ink ---------------------------------------------------------------------------


class InkPlan(BaseModel):
    beats: str = Field(description="The storyboard in the beat language: shot lines and beat lines, one per line")
    new_props: list[PropRequest] = Field(description="Every prop drawn in the beats that is not in the library")
    new_settings: list[PropRequest] = Field(
        default_factory=list, description="Every setting used on shot lines that is not in the library"
    )


INK_PLAN = prompts.Prompt("storyboard_ink")

BEAT_SECONDS = {"long": 2.2, "short": 1.6}  # a beat line this often: lively without crowding

INK_FORMAT_RULES = {fmt: prompts.Prompt(f"storyboard_ink_{fmt}") for fmt in ("short", "long")}


def ink_system(fmt: str, props: str, cards: dict[str, dict] | None = None) -> str:
    """The planner's instructions with the cast and the prop library: the part every chapter shares."""
    cast = ", ".join(name for name in art.CHARACTERS if name != "narrator")
    actions = ", ".join(f"{name} ({clip['what']})" for name, clip in ink.moves().items() if name in beats.ACTIONS)
    value = (
        INK_PLAN.format(
            faces=" ".join(art.EXPRESSIONS),
            cast=cast,
            actions=actions,
            format_rules=INK_FORMAT_RULES[fmt],
            hair=" ".join(ink.HAIR),
            colours=" ".join(ink.HAIR_COLOURS),
            facial=" ".join(ink.FACIAL),
            hats=" ".join(ink.HEADWEAR),
            eyes=" ".join(ink.EYEWEAR),
            outfits=" ".join(ink.OUTFITS),
            skins=" ".join(ink.SKINS),
        )
        + f"\n\n<cast>\n{CAST}\n</cast>\n\n<prop_library>\n{props}\n</prop_library>"
        + f"\n\n<setting_library>\n{places_mod.listing()}\n</setting_library>"
    )
    if cards:
        value += "\n\n" + data_graphics.prompt_block(cards)
    return value


PHOTO_EVERY = {"ink": 40, "film": 30}  # at most one archive photo per this many seconds of narration


def photo_budget(style: str, seconds: float) -> int:
    """How many archive photos a stretch of narration may have: drawings and cards carry the rest."""
    return max(1, int(seconds // PHOTO_EVERY[style]))


def _seconds(words: list[dict], offset: int, size: int) -> float:
    return words[offset + size - 1]["e"] - words[offset]["s"]


def limit_photos(shots: list[dict], budget: int, parse) -> int:
    """Keep the first `budget` photo lines of a chapter, and drop the marks made on the others.

    Returns how many photos were left out."""
    kept, cut, gone = 0, 0, set()
    for shot in shots:
        lines = []
        for raw in shot.get("lines", []):
            line = parse(raw)
            if line is not None and line.verb == "photo":
                if kept >= budget:
                    gone.add(line.id)
                    cut += 1
                    continue
                kept += 1
            elif line is not None and gone & {getattr(line, k, "") for k in ("who", "target", "target2")}:
                continue
            lines.append(raw)
        shot["lines"] = lines
    return cut


def _report_photo_cut(cut: int, style: str, rep: Reporter) -> None:
    if cut:
        rep.info(
            f"{cut} archive photos over the budget (one per {PHOTO_EVERY[style]} s of narration) were left "
            f"out; {'the drawings' if style == 'ink' else 'cards and type'} carry those moments"
        )


def plan_ink(
    storyboard_id: int,
    words: list[dict],
    fmt: str,
    title: str,
    headings: dict,
    rep: Reporter,
    marks: dict[int, str] | None = None,
    cards: dict[str, dict] | None = None,
    micro: bool = False,
) -> None:
    """Plan an ink storyboard: one call per chapter, the shared instructions cached between them."""
    known = library()
    props = "\n".join(f"- {name}: {desc}" for name, desc in known.items()) or "(empty so far)"
    cards = cards or {}
    system = ink_system(fmt, props, cards)
    if micro:
        system += "\n\n" + str(MICRO_PLAN)
    chunks = _chunks(words) if fmt == "long" else [(0, words, None)]
    rep.info(
        f"Planning shots for {words[-1]['e']:.0f} seconds of {'designed visual cues' if micro else 'narration'}"
        + (f", chapter by chapter ({len(chunks)})" if len(chunks) > 1 else "")
    )

    def run(chunk: tuple[int, list[dict], str | None]) -> tuple[int, int, InkPlan]:
        offset, part, key = chunk  # the model numbers the chapter's words from 0; shifted back below
        chapter = headings.get(key, "") if key else ""
        chapter_line = f"<chapter>{chapter}</chapter>\n\n" if chapter else ""
        seconds = part[-1]["e"] - part[0]["s"]
        prompt = (
            f"<video_title>{title}</video_title>\n\n{chapter_line}"
            f"<length>{seconds:.0f} seconds: aim for about {max(4, round(seconds / BEAT_SECONDS[fmt]))} beat "
            f"lines in all, including about {max(1, round(seconds / 25))} ACTION lines and "
            f"{max(1, round(seconds / 30))} move lines for actors; at most {photo_budget('ink', seconds)} "
            f"photo lines</length>\n\n{turns_block(marks or {}, offset, len(part))}"
            f"{_timed_input(part, micro)}"
        )
        # Medium effort: at high, most of the answer was the model thinking (about 85% of the tokens)
        # for a plan of the same quality.
        return offset, len(part), llm.ask("storyboard", system, prompt, InkPlan, effort="medium", cache=True)

    # The first chapter alone writes the cache; the others then read it in parallel.
    results = [run(chunks[0])]
    rep.progress(1 / len(chunks), f"Planned 1 of {len(chunks)} chapters")
    if len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for result in pool.map(run, chunks[1:]):
                results.append(result)
                rep.progress(len(results) / len(chunks), f"Planned {len(results)} of {len(chunks)} chapters")

    shots, descriptions, settings, cut = [], {}, {}, 0
    for n, (offset, size, result) in enumerate(results):
        part = beats.parse_plan(result.beats, offset + size, offset, check=False)
        if not part:
            rep.warn(f"Chapter {n + 1} came back with no usable shots")
            continue
        cut += limit_photos(part, photo_budget("ink", _seconds(words, offset, size)), beats.parse_line)
        part[0]["start_word"] = offset  # every chapter starts its own shot...
        part[0]["page"] = n > 0  # ...on a new row of the page
        shots.extend(part)
        descriptions.update({p.name: p.description for p in result.new_props})
        settings.update({beats._name(p.name): p.description for p in result.new_settings})
    _report_photo_cut(cut, "ink", rep)
    _report_big(limit_big(shots, beats.parse_line, "big", marks or {}), rep)
    shots, dropped = beats.clean(shots, len(words), set(cards))
    if dropped:
        rep.warn(
            f"{len(dropped)} planned lines could not be used and were left out, such as: " + "; ".join(dropped[:3])
        )
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.scenes = shots
        board.video_path = board.published_at = None
        for name in beats.prop_names(shots):
            if db.get(StudioAsset, name) is None:
                db.add(StudioAsset(name=name, description=descriptions.get(name, name.replace("_", " "))))
    places_mod.register(beats.place_names(shots), settings)
    lines = sum(len(s["lines"]) for s in shots)
    new = [n for n in beats.prop_names(shots) if n not in known]
    rep.info(f"{len(shots)} shots, {lines} beats, {len(new)} new props to draw")
    score = beats.score(shots, lambda i: words[max(0, min(i, len(words) - 1))]["s"], words[-1]["e"])
    rep.info(
        f"Liveliness {score['score']}/100 ({score['verdict']})"
        + "".join(f"; {x['label'].lower()} {x['points']}" for x in score["lines"])
    )


# --- planning a film (the evidence wall) -----------------------------------------------------


class FilmPlan(BaseModel):
    beats: str = Field(description="The storyboard: shot lines and beat lines, one per line")


FILM_PLAN = prompts.Prompt("storyboard_film")

FILM_SECONDS = {"long": 3.2, "short": 2.4}  # a beat line this often

FILM_FORMAT_RULES = {fmt: prompts.Prompt(f"storyboard_film_{fmt}") for fmt in ("short", "long")}


def plan_film(
    storyboard_id: int,
    words: list[dict],
    fmt: str,
    title: str,
    headings: dict,
    rep: Reporter,
    marks: dict[int, str] | None = None,
    cards: dict[str, dict] | None = None,
    micro: bool = False,
) -> None:
    """Plan a film storyboard: shots of beat lines on the evidence wall, one call per chapter."""
    cards = cards or {}
    system = FILM_PLAN.format(format_rules=FILM_FORMAT_RULES[fmt])
    if micro:
        system += "\n\n" + str(MICRO_PLAN)
    if cards:
        system += "\n\n" + data_graphics.prompt_block(cards)
    chunks = _chunks(words) if fmt == "long" else [(0, words, None)]
    rep.info(
        f"Planning the wall for {words[-1]['e']:.0f} seconds of {'designed visual cues' if micro else 'narration'}"
        + (f", chapter by chapter ({len(chunks)})" if len(chunks) > 1 else "")
    )

    def run(chunk: tuple[int, list[dict], str | None]) -> tuple[int, int, FilmPlan]:
        offset, part, key = chunk
        chapter = headings.get(key, "") if key else ""
        chapter_line = f"<chapter>{chapter}</chapter>\n\n" if chapter else ""
        seconds = part[-1]["e"] - part[0]["s"]
        prompt = (
            f"<video_title>{title}</video_title>\n\n{chapter_line}"
            f"<length>{seconds:.0f} seconds: aim for about {max(4, round(seconds / FILM_SECONDS[fmt]))} "
            f"beat lines in all, at most {photo_budget('film', seconds)} photo lines</length>\n\n"
            f"{turns_block(marks or {}, offset, len(part))}{_timed_input(part, micro)}"
        )
        return offset, len(part), llm.ask("storyboard", system, prompt, FilmPlan, effort="medium", cache=True)

    results = [run(chunks[0])]
    rep.progress(1 / len(chunks), f"Planned 1 of {len(chunks)} chapters")
    if len(chunks) > 1:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for result in pool.map(run, chunks[1:]):
                results.append(result)
                rep.progress(len(results) / len(chunks), f"Planned {len(results)} of {len(chunks)} chapters")
    shots: list[dict] = []
    cut = 0
    for n, (offset, size, result) in enumerate(results):
        part = film.parse_plan(result.beats, offset + size, offset)
        if not part:
            rep.warn(f"Chapter {n + 1} came back with no usable shots")
            continue
        cut += limit_photos(part, photo_budget("film", _seconds(words, offset, size)), film.parse_line)
        part[0]["start_word"] = offset
        shots.extend(part)
    _report_photo_cut(cut, "film", rep)
    _report_big(limit_big(shots, film.parse_line, "bar", marks or {}), rep)
    shots, dropped = film.clean(shots, len(words), set(cards))
    if dropped:
        rep.warn(
            f"{len(dropped)} planned lines could not be used and were left out, such as: " + "; ".join(dropped[:3])
        )
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.scenes = shots
        board.video_path = board.published_at = None
    lines = sum(len(s["lines"]) for s in shots)
    photos = len(film.photo_queries(shots))
    rep.info(f"{len(shots)} shots, {lines} beats, {photos} archive pictures to find")


# --- drawing props ----------------------------------------------------------------------------


class Drawing(BaseModel):
    svg: str = Field(description="One complete SVG document")


SHADE_LIST = "; ".join(f"{k} {art.PALETTE[k]} (shadow {d}, light {li})" for k, (d, li) in art.SHADES.items())

DRAW = prompts.Prompt("prop_draw")


class Look(BaseModel):
    matches: bool = Field(description="A viewer would name it as the object described")
    quality: int = Field(ge=1, le=10, description="As an illustration: 1 crude, 5 plain icon, 8 crafted, 10 superb")
    fix: str = Field(description="The most important things to change, in one or two sentences; empty if none")


LOOK = prompts.Prompt("prop_check")

GOOD_ENOUGH = 7  # below this, or not the object, the prop is drawn once more with the critique


def look_at_prop(svg: str, name: str, description: str) -> Look:
    """The quick tier looks at the rendered drawing; a failure counts as a pass (the drawing is valid)."""
    image = art.rasterize(svg, 512)
    flat = Image.new("RGB", image.size, art.PAPER)
    flat.paste(image, mask=image.getchannel("A"))
    buffer = io.BytesIO()
    flat.save(buffer, "PNG")
    content = [
        {"type": "text", "text": f"<brief>{name.replace('_', ' ')}: {description}</brief>"},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(buffer.getvalue()).decode(),
            },
        },
    ]
    try:
        return llm.ask("prop_check", LOOK, content, Look, effort="low")
    except Exception as err:  # noqa: BLE001 - an unchecked drawing is still a drawing
        log.warning("prop check failed", extra={"prop": name, "error": str(err)[:200]})
        return Look(matches=True, quality=GOOD_ENOUGH, fix="")


def draw_prop(name: str, description: str, instruction: str = "") -> str:
    """Draw a prop as an illustration, look at it, and draw it once more if it misses the brief.

    Settings.prop_drawing sets the effort: careful thinks more and draws more detail, at about
    three times the cost."""
    from .. import settings as settings_mod

    effort = "medium" if settings_mod.load().prop_drawing == "careful" else "low"
    prompt = f"Draw: {name.replace('_', ' ')}. {description}"
    if instruction:
        prompt += f"\nChange requested by the creator: {instruction}"
    note, best, looked = "", None, False
    for _ in range(4):
        brief = DRAW.format(ink=art.INK, stroke=art.STROKE, detail_stroke=art.DETAIL_STROKE, shades=SHADE_LIST)
        svg = llm.ask("props", brief, prompt + note, Drawing, effort=effort).svg
        try:
            svg = art.check_prop_svg(svg)
        except Exception as err:  # noqa: BLE001 - any rendering failure means: draw it again
            log.info("prop drawing rejected", extra={"prop": name, "error": str(err)[:200]})
            note = f"\n\nYour last drawing was rejected: {str(err)[:300]}. Fix that."
            continue
        if looked:
            return art.mark_style(svg)
        best, looked = svg, True
        review = look_at_prop(svg, name, description)
        if review.matches and review.quality >= GOOD_ENOUGH:
            return art.mark_style(svg)
        note = (
            f"\n\nAn art director looked at your last drawing ({review.quality}/10"
            f"{'' if review.matches else ', not recognisable as the object'}): {review.fix} Draw it again, better."
        )
    if best:
        return art.mark_style(best)
    raise RuntimeError(f"Could not draw {name}: {note.strip()[:300]}")


def find_photos(storyboard_id: int, rep: Reporter, sleep=time.sleep) -> None:
    """Find the archive pictures an ink or film storyboard asks for, on Wikimedia Commons; each search once.

    When Commons limits requests (429) the search waits and asks again, a few times; if it is still
    refused the remaining searches are left for the next render rather than refused one by one.
    """
    from . import photos

    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        queries = film.photo_searches(board.style, board.scenes)
        known = {p.query for p in db.query(StudioPhoto).filter(StudioPhoto.query.in_(queries))} if queries else set()
    todo = [q for q in queries if q not in known]
    if not todo:
        return
    minutes = round(len(todo) * photos.api_gap() / 60)
    slow = (
        ""
        if config.wikimedia_contact()
        else (": Commons allows 10 searches a minute without WIKIMEDIA_CONTACT in .env")
    )
    rep.info(
        f"Finding {len(todo)} archive pictures on Wikimedia Commons (public domain, CC0 and CC BY only)"
        + (f". About {minutes} min{slow}" if minutes else "")
    )
    missing, later, waits = [], [], 0
    for n, query in enumerate(todo, 1):
        found = None
        while True:
            try:
                found = photos.find(query)
                waits = 0
            except photos.Busy as err:
                if waits < len(photos.WAITS):
                    seconds = err.wait or photos.WAITS[waits]
                    waits += 1
                    rep.info(f'Wikimedia Commons asks to slow down: waiting {seconds:.0f} s, then "{query}" again')
                    sleep(seconds)
                    continue
                later = todo[n - 1 :]
            except RuntimeError as err:
                rep.warn(f"{query}: {err}")
            break
        if later:
            break
        if found is None:
            missing.append(query)
        else:
            rep.info(f'"{query}": {found.title} ({found.license})')
        rep.progress(n / len(todo), f"Found {n - len(missing)} of {len(todo)} pictures")
    if missing:
        rep.warn(
            f"No usable picture for: {', '.join(missing)}. They show as paper cards with the search; "
            "edit the search words in the beats and render again."
        )
    if later:
        rep.warn(
            f"Wikimedia Commons is still limiting requests, so {len(later)} pictures were not searched: "
            f"{', '.join(later)}. They show as paper cards for now; render again in a few minutes and only "
            "these are searched."
        )


def draw_missing(storyboard_id: int, rep: Reporter) -> None:
    find_photos(storyboard_id, rep)
    places_mod.prepare(storyboard_id, rep)
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        names = prop_names(board.style, board.scenes)
        for name in names:
            if db.get(StudioAsset, name) is None:
                db.add(StudioAsset(name=name, description=name.replace("_", " ")))
        db.flush()
        todo = [
            (a.name, a.description)
            for a in db.scalars(select(StudioAsset).where(StudioAsset.name.in_(names)))
            if a.svg is None or (a.origin != "you" and not art.current_style(a.svg))
        ]
        old = sum(
            1
            for a in db.scalars(select(StudioAsset).where(StudioAsset.name.in_([n for n, _ in todo])))
            if a.svg is not None
        )
    if not todo:
        rep.info("Every prop is already in the library")
        return
    rep.info(
        f"Drawing {len(todo)} props as illustrations: {', '.join(n for n, _ in todo)}"
        + (f" ({old} of them were flat drawings in the old style, redrawn; your own are never touched)" if old else "")
    )
    failed, done = [], 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(draw_prop, name, desc): name for name, desc in todo}
        for future in futures:
            name = futures[future]
            try:
                svg = future.result()
            except RuntimeError as err:
                failed.append(name)
                rep.warn(str(err))
                continue
            with session_scope() as db:
                asset = db.get(StudioAsset, name)
                asset.svg, asset.origin, asset.updated_at = svg, "ai", utcnow()
            done += 1
            rep.progress(done / len(todo), f"Drawn {done} of {len(todo)} props")
    if failed:
        rep.warn(f"{len(failed)} props could not be drawn and will show as placeholders: {', '.join(failed)}")


# --- creating a storyboard ---------------------------------------------------------------------


def words_for(take: VoiceTake, section_key: str | None) -> tuple[list[dict], float, float]:
    """The take's timed words for one section (or all), with times made relative to its start."""
    timeline = json.loads(voice_mod.timeline_path(take).read_text())
    chosen = [w for w in timeline if section_key is None or w["section"] == section_key]
    if not chosen:
        raise ValueError("That section has no timed words in this take")
    start = max(0.0, chosen[0]["s"] - 0.15)
    end = min(take.duration or chosen[-1]["e"] + 0.6, chosen[-1]["e"] + 0.6)
    return (
        [
            {"w": w["w"], "s": round(w["s"] - start, 3), "e": round(w["e"] - start, 3), "section": w["section"]}
            for w in chosen
        ],
        start,
        end,
    )
