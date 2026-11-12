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
from . import art, beats
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


def prop_names(style: str, scenes: list[dict]) -> set[str]:
    """Every library prop a storyboard uses."""
    if style == "ink":
        return beats.prop_names(scenes)
    return {e["name"] for s in scenes for e in s.get("elements", []) if e["kind"] == "prop"}


def plan(storyboard_id: int, rep: Reporter) -> None:
    """Plan the scenes. A long video is planned one chapter at a time, so no answer gets too long."""
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        words, fmt, title, style = list(board.words), board.format, board.title, board.style
        headings = {s["key"]: s["heading"] for s in board.script.sections}
    if style == "ink":
        plan_ink(storyboard_id, words, fmt, title, headings, rep)
        return
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


# --- planning in ink ---------------------------------------------------------------------------

class InkPlan(BaseModel):
    beats: str = Field(description="The storyboard in the beat language: shot lines and beat lines, one per line")
    new_props: list[PropRequest] = Field(description="Every prop drawn in the beats that is not in the library")


INK_PLAN = """\
You storyboard a narrated explainer for a faceless YouTube channel. It is drawn in \
black ink on one long sheet of paper: stick-figure actors, simple props with a \
watercolour wash, a few handwritten words. The camera travels across the sheet from \
shot to shot, so the video is one continuous drawing, not a run of slides.

How to think about each idea in the narration:
1. Find the action in it: something gets stuck, leaks, grows, is hidden, measured, \
sorted, swapped, pushed uphill, cracked open, falls apart.
2. Give that action one simple physical object (from the prop library when one fits).
3. Have an actor do the action to the object: push it, hold it, point at it, walk to \
it, react to it. If you could delete the actor and lose nothing, the shot is \
decoration: think again.
Then make things happen to objects with marks (circle, cross, tick, underline, arrow) \
and motions (shake, bounce, grow...). Do not just illustrate nouns: a picture of the \
word the narrator is saying is the weakest choice. Words on screen are rare: a number, \
a name or a short label the viewer must remember; the narration is already heard.

The beat language. Each line starts with the number of the narration word on which it \
happens. A shot line opens each shot; beat lines follow it:
  N shot "what the viewer sees, in one sentence" [wide|push|drift|close]
  N actor WHO [PLACE] [POSE] [FACE] [talk] [small|large] [as ID]
  N draw PROP [PLACE] [small|large] ["label of 1 to 3 words"] [as ID]
  N write "1 to 6 words" [PLACE] [big] [COLOUR] [as ID]
  N do ACTOR POSE [TARGET] [FACE]
  N face ACTOR FACE
  N move ACTOR|PROP PLACE|TARGET   (to a thing: walks up to it, or lands on it)
  N arrow FROM TO [COLOUR]
  N circle|underline|cross|tick TARGET [COLOUR]
  N shake|bounce|pulse|spin|grow|shrink TARGET
  N erase TARGET|all
  N look TARGET|all          (the camera moves in on something, or back out)
PLACE: left center right top bottom top_left top_right bottom_left bottom_right
POSE: stand point hold push pull shrug think facepalm cheer wave surprised sad jump. \
point, hold, push and pull take a TARGET, and the actor walks up to it by itself.
FACE: {faces}
COLOUR: warm (flow, emphasis), bad (problems, myths), good (answers, truth), gold, green
TARGET and FROM/TO: an actor's name, a prop's name, an ID given with `as`, or a PLACE.
`talk` makes the actor's mouth move with the narration: use it for the narrator when he \
is the one speaking to the viewer.
Things stay until erased or until their shot ends; an actor mentioned in the next shot \
walks along to it with the camera.

Rules:
- A new shot every 2 to 4 sentences, or when the subject changes; inside a shot, \
something new every 2 to 4 seconds, spread evenly: never more than 4 seconds without a \
new beat. Something happens within the first 2 seconds.
- Bring an actor on with an actor line before anything else mentions them.
- At most 5 things on stage at once: erase what is finished before adding more.
- The narrator (glasses) is the host who handles things and reacts; other cast members \
play people in the story ({cast}). Prefer hands-on poses (hold, push, pull) and \
motions over pointing: point at most once per shot.
- Write at most one word or number per shot, and only when it must be remembered.
- Camera: wide is the default; push slowly moves in, for a key moment; drift for calm \
stretches; close frames small things tightly. Use look for a reveal.
- Vary the patterns: no two shots in a row with the same beats.
- Props are single objects (a coin, a fridge, a human eye), never scenes. Reuse library \
props by their exact names; for anything else invent a snake_case name, use it in a \
draw line and describe it in new_props.
- {format_rules}

A short example, for a different video:
0 shot "The narrator drops toast; it lands butter side down"
0 actor narrator left deadpan talk
2 draw toast center
3 do narrator hold toast
6 bounce toast
7 write "butter side down" top bad as myth
9 do narrator facepalm
12 shot "A table is exactly the wrong height" push
12 draw kitchen_table center large
14 arrow kitchen_table toast warm
16 circle kitchen_table
18 do narrator point kitchen_table surprised
"""

BEAT_SECONDS = 2.2      # one beat line every 2.2 s or so: lively without crowding

INK_FORMAT_RULES = {
    "short": "The frame is vertical (9:16) and captions cover the bottom third: keep things in the top, center "
             "and side places, and use the bottom places only for small things. Shots are short: one to three "
             "sentences each.",
    "long": "The frame is horizontal (16:9) and this is one chapter of a longer video. Pace it a little slower "
            "than a Short: a new shot every 3 to 5 sentences, something new every 2 to 5 seconds. Open the "
            "chapter with a shot that sets up its subject.",
}


def ink_system(fmt: str, props: str) -> str:
    """The planner's instructions with the cast and the prop library: the part every chapter shares."""
    cast = ", ".join(name for name in art.CHARACTERS if name != "narrator")
    return (INK_PLAN.format(faces=" ".join(art.EXPRESSIONS), cast=cast, format_rules=INK_FORMAT_RULES[fmt])
            + f"\n<cast>\n{CAST}\n</cast>\n\n<prop_library>\n{props}\n</prop_library>")


def plan_ink(storyboard_id: int, words: list[dict], fmt: str, title: str, headings: dict, rep: Reporter) -> None:
    """Plan an ink storyboard: one call per chapter, the shared instructions cached between them."""
    known = library()
    props = "\n".join(f"- {name}: {desc}" for name, desc in known.items()) or "(empty so far)"
    system = ink_system(fmt, props)
    chunks = _chunks(words) if fmt == "long" else [(0, words, None)]
    rep.info(f"Planning shots for {words[-1]['e']:.0f} seconds of narration"
              + (f", chapter by chapter ({len(chunks)})" if len(chunks) > 1 else ""))

    def run(chunk: tuple[int, list[dict], str | None]) -> tuple[int, int, InkPlan]:
        offset, part, key = chunk     # the model numbers the chapter's words from 0; shifted back below
        chapter = headings.get(key, "") if key else ""
        chapter_line = f"<chapter>{chapter}</chapter>\n\n" if chapter else ""
        seconds = part[-1]["e"] - part[0]["s"]
        prompt = (f"<video_title>{title}</video_title>\n\n{chapter_line}"
                  f"<length>{seconds:.0f} seconds: aim for about {max(4, round(seconds / BEAT_SECONDS))} beat "
                  f"lines in all</length>\n\n<narration>\n{numbered_words(part)}\n</narration>")
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

    shots, descriptions = [], {}
    for n, (offset, size, result) in enumerate(results):
        part = beats.parse_plan(result.beats, offset + size, offset, check=False)
        if not part:
            rep.warn(f"Chapter {n + 1} came back with no usable shots")
            continue
        part[0]["start_word"] = offset          # every chapter starts its own shot...
        part[0]["page"] = n > 0                 # ...on a new row of the page
        shots.extend(part)
        descriptions.update({p.name: p.description for p in result.new_props})
    shots, dropped = beats.clean(shots, len(words))
    if dropped:
        rep.warn(f"{len(dropped)} planned lines could not be used and were left out, such as: "
                 + "; ".join(dropped[:3]))
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.scenes = shots
        board.video_path = None
        for name in beats.prop_names(shots):
            if db.get(StudioAsset, name) is None:
                db.add(StudioAsset(name=name, description=descriptions.get(name, name.replace("_", " "))))
    lines = sum(len(s["lines"]) for s in shots)
    new = [n for n in beats.prop_names(shots) if n not in known]
    rep.info(f"{len(shots)} shots, {lines} beats, {len(new)} new props to draw")
    score = beats.score(shots, lambda i: words[max(0, min(i, len(words) - 1))]["s"], words[-1]["e"])
    rep.info(f"Liveliness {score['score']}/100 ({score['verdict']})"
             + "".join(f"; {x['label'].lower()} {x['points']}" for x in score["lines"]))


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
        names = prop_names(board.style, board.scenes)
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
