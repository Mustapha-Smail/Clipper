"""Storyboards: Claude turns a timed stretch of narration into scenes, and draws the props.

A storyboard covers one stretch of a voice take: one section for a Short, the
whole script for a long video. Its words keep the times Whisper found, so a
scene starts on the word it names, never on a guessed timestamp (the same rule
as moment selection: Claude answers with word numbers).

Scenes are a small vocabulary the renderer knows how to draw: cast members with
a pose and an expression, props from the library, big text, numbers, arrows,
placed in named slots. Props the library lacks are drawn by Claude as SVG in
the house style, checked, and kept for every later video.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import llm
from ..db import session_scope
from ..db.models import Storyboard, StudioAsset, VoiceTake, utcnow
from ..jobs import Reporter
from . import art
from . import voice as voice_mod

log = logging.getLogger("clipper.studio")

Slot = Literal["left", "center", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"]
PROP_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
MAX_ELEMENTS = 4


class Element(BaseModel):
    kind: Literal["character", "prop", "text", "number", "arrow"]
    name: str = Field("", description="character: a cast name. prop: a prop name in snake_case. Else empty")
    slot: Slot
    to_slot: Slot | None = Field(None, description="arrow only: where it points")
    size: Literal["small", "medium", "large"] = "medium"
    pose: Literal[art.POSES] = "stand"
    expression: Literal[art.EXPRESSIONS] = "neutral"
    facing: Literal["left", "right"] = "right"
    talking: bool = Field(False, description="character only: mouth moves with the narration")
    text: str = Field("", description="text or number: what to show (at most 5 words). prop: an optional "
                                      "label under it (1 to 3 words)")
    enter: Literal["pop", "slide", "fade", "none"] = "pop"
    enter_word: int | None = Field(None, description="Number of the word on which this element appears; "
                                                     "null to appear with the scene")
    motion: Literal["none", "bounce", "shake", "pulse", "float", "spin", "grow"] = "none"


class Scene(BaseModel):
    start_word: int = Field(description="Number of the word on which the scene starts")
    note: str = Field(description="One sentence: what the viewer sees and why it is funny or clear")
    background: Literal[art.BACKGROUNDS] = "paper"
    camera: Literal["still", "zoom_in", "zoom_out", "pan_left", "pan_right"] = "still"
    elements: list[Element]


class PropRequest(BaseModel):
    name: str = Field(description="snake_case, e.g. led_bulb")
    description: str = Field(description="What to draw, in one sentence, for an illustrator")


class Plan(BaseModel):
    scenes: list[Scene]
    new_props: list[PropRequest] = Field(description="Every prop used in the scenes that is not in the library")


# --- planning ----------------------------------------------------------------------------

PLAN = """\
You storyboard a narrated animated video for a faceless YouTube channel. The look is \
simple doodles: stick figures with expressive faces, bold flat props with thick black \
outlines, big words on screen. The humour is deadpan: the pictures take the narration \
literally, react to it, or undercut it.

You get the narration as numbered words with their times. Cut it into scenes, usually \
one per sentence or idea, 2 to 5 seconds each; change scenes more often in the first 5 \
seconds to hold attention. Each scene starts on a word number. Inside a scene, elements \
can appear later on the word that introduces them (enter_word), which keeps the picture \
in step with the voice.

Composition rules:
- At most {max_elements} elements per scene, and leave space: one clear idea per picture.
- Slots: top, center and bottom run down the middle; left/right and the corner slots \
sit beside them. {format_rules}
- The narrator (glasses) is the host: use him to react, point at things, shrug at \
absurd facts. Use other cast members for people in the story (scientist, royal, \
fancy for anyone historical, worker, kid, chef, person).
- Props are single objects (a light bulb, a fridge, a human eye, a coin), not scenes. \
Reuse library props by their exact name; invent a new snake_case name for anything \
else and describe it in new_props.
- Text elements show key words or short phrases the viewer should remember, written \
exactly as displayed (capitals for punch, correct case for names and formulas such as \
InGaN); number elements show a striking figure ("450 nm", "1993", "x8"). Do not \
repeat the whole narration on screen: captions already do that.
- Arrows link two slots (slot to to_slot), to show causes, flows or labels.
- Motion sparingly: bounce for excitement, shake for alarm, pulse for emphasis, float \
for things in the air, spin for anything turning, grow for something getting bigger.
- Vary backgrounds a little (paper, grid, floor, sky, yellow, blue) but keep \
the same one for scenes that belong together."""

FORMAT_RULES = {
    "short": "The frame is vertical (9:16). Captions cover the bottom third, so keep everything in the "
             "top, center and side slots; use bottom slots only for small things.",
    "long": "The frame is horizontal (16:9), a long YouTube video. Use left, center and right for the main "
            "row, top for titles; bottom slots are free since there are no burned-in captions. This is one "
            "chapter of a longer video: pace it a little slower than a Short (3 to 6 seconds per scene), and "
            "open the chapter with a picture that sets up its topic.",
}

CAST = "\n".join(f"- {name}: {description}" for name, description in art.CHARACTERS.items())


def numbered_words(words: list[dict]) -> str:
    """Words as '[n] word' with a timestamp every sentence, compact enough for long scripts."""
    out, sentence_start = [], True
    for i, w in enumerate(words):
        prefix = f"\n({w['s']:.1f}s) " if sentence_start else ""
        out.append(f"{prefix}[{i}] {w['w']}")
        sentence_start = w["w"].rstrip("\"')”").endswith((".", "?", "!"))
    return " ".join(out).strip()


def library() -> dict[str, str]:
    with session_scope() as db:
        return {a.name: a.description for a in db.scalars(select(StudioAsset).order_by(StudioAsset.name))}


def clean(scenes: list[dict], word_count: int) -> list[dict]:
    """Make a plan safe to render: ordered scenes, valid word numbers, known names, few elements."""
    out = []
    last = -1
    for scene in sorted(scenes, key=lambda s: s["start_word"]):
        start = max(0, min(scene["start_word"], word_count - 1))
        if start <= last and out:
            continue
        last = start
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
            if e.get("enter_word") is not None:
                e["enter_word"] = max(start, min(e["enter_word"], word_count - 1))
            elements.append(e)
        out.append({**scene, "start_word": start, "elements": elements[:MAX_ELEMENTS]})
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


def _plan_chunk(system: str, title: str, props: str, words: list[dict], chapter: str) -> Plan:
    chapter_line = f"<chapter>{chapter}</chapter>\n\n" if chapter else ""
    prompt = (f"<video_title>{title}</video_title>\n\n{chapter_line}<cast>\n{CAST}\n</cast>\n\n"
              f"<prop_library>\n{props}\n</prop_library>\n\n"
              f"<narration>\n{numbered_words(words)}\n</narration>")
    return llm.ask("storyboard", system, prompt, Plan, effort="high")


def plan(storyboard_id: int, rep: Reporter) -> None:
    """Plan the scenes. A long video is planned one chapter at a time, so no answer gets too long."""
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        words, fmt, title = list(board.words), board.format, board.title
        headings = {s["key"]: s["heading"] for s in board.script.sections}
    known = library()
    system = PLAN.format(max_elements=MAX_ELEMENTS, format_rules=FORMAT_RULES[fmt])
    props = "\n".join(f"- {name}: {desc}" for name, desc in known.items()) or "(empty so far)"
    chunks = _chunks(words) if fmt == "long" else [(0, words, None)]
    rep.info(f"Planning scenes for {words[-1]['e']:.0f} seconds of narration"
             + (f", chapter by chapter ({len(chunks)})" if len(chunks) > 1 else ""))

    def run(chunk: tuple[int, list[dict], str | None]) -> tuple[int, int, Plan]:
        offset, part, key = chunk   # the model numbers the chapter's words from 0; shifted back below
        chapter = headings.get(key, "") if key else ""
        return offset, len(part), _plan_chunk(system, title, props, part, chapter)

    results, done = [], 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(run, chunks):
            results.append(result)
            done += 1
            rep.progress(done / len(chunks), f"Planned {done} of {len(chunks)} chapters")

    scenes, descriptions = [], {}
    for offset, size, result in results:
        part = clean([s.model_dump() for s in result.scenes], size)
        for scene in part:
            scene["start_word"] += offset
            for e in scene["elements"]:
                if e.get("enter_word") is not None:
                    e["enter_word"] += offset
        if part:
            part[0]["start_word"] = offset   # every chapter starts its own scene
        scenes.extend(part)
        descriptions.update({p.name: p.description for p in result.new_props})
    scenes = clean(scenes, len(words))
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.scenes = scenes
        board.video_path = None
        for name in {e["name"] for s in scenes for e in s["elements"] if e["kind"] == "prop"}:
            if db.get(StudioAsset, name) is None:
                db.add(StudioAsset(name=name, description=descriptions.get(name, name.replace("_", " "))))
    used = sum(len(s["elements"]) for s in scenes)
    rep.info(f"{len(scenes)} scenes, {used} elements, {len(descriptions)} new props to draw")


# --- drawing props ----------------------------------------------------------------------------

class Drawing(BaseModel):
    svg: str = Field(description="One complete SVG document")


DRAW = f"""\
You draw one prop for an animated YouTube channel, as SVG. House style:
- viewBox="0 0 512 512"; the object centred and filling about 80 percent of it; \
front or three-quarter view; no background.
- Bold, flat and simple, like a sticker or an icon: at most about 30 shapes.
- Every shape outlined in {art.INK} with stroke-width {art.STROKE}, \
stroke-linecap="round" and stroke-linejoin="round".
- Fills only from this palette: {", ".join(f"{k} {v}" for k, v in art.PALETTE.items())}. \
A few white highlight strokes are welcome.
- Allowed elements: path, rect, circle, ellipse, line, polyline, polygon, g. No text, \
no gradients, filters, masks, images, styles, scripts or links.
- Recognisable at a glance at small size; a little character (a slight tilt, a \
cartoon proportion) is good, faces on objects only if asked."""


def draw_prop(name: str, description: str, instruction: str = "") -> str:
    prompt = f"Draw: {name.replace('_', ' ')}. {description}"
    if instruction:
        prompt += f"\nChange requested by the creator: {instruction}"
    error = ""
    for _ in range(3):
        svg = llm.ask("props", DRAW, prompt + (f"\n\nYour last drawing was rejected: {error}. Fix that." if error
                                                else ""), Drawing, effort="medium").svg
        try:
            return art.check_prop_svg(svg)
        except Exception as err:  # noqa: BLE001 - any rendering failure means: draw it again
            error = str(err)[:300]
    raise RuntimeError(f"Could not draw {name}: {error}")


def draw_missing(storyboard_id: int, rep: Reporter) -> None:
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        names = {e["name"] for s in board.scenes for e in s["elements"] if e["kind"] == "prop"}
        for name in names:
            if db.get(StudioAsset, name) is None:
                db.add(StudioAsset(name=name, description=name.replace("_", " ")))
        db.flush()
        todo = [(a.name, a.description) for a in db.scalars(select(StudioAsset).where(
            StudioAsset.name.in_(names), StudioAsset.svg.is_(None)))]
    if not todo:
        rep.info("Every prop is already in the library")
        return
    rep.info(f"Drawing {len(todo)} new props: {', '.join(n for n, _ in todo)}")
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
    return [{"w": w["w"], "s": round(w["s"] - start, 3), "e": round(w["e"] - start, 3), "section": w["section"]}
            for w in chosen], start, end
