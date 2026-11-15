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

from .. import config, llm
from ..db import session_scope
from ..db.models import Storyboard, StudioAsset, StudioPhoto, VoiceTake, utcnow
from ..jobs import Reporter
from . import art, beats, film, ink
from . import places as places_mod
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
    name: str = Field(description="snake_case and specific, e.g. roman_denarius rather than coin")
    description: str = Field(description="For an illustrator, in one or two sentences: the object, its era and "
                                         "material, its colours, and the detail that makes it recognisable in "
                                         "this story")


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
    """The props the planner may reuse: yours, and those drawn in the current illustrated style.

    Older flat drawings are left out, so the planner asks for them again and they are redrawn."""
    with session_scope() as db:
        return {a.name: a.description for a in db.scalars(select(StudioAsset).order_by(StudioAsset.name))
                if a.origin == "you" or art.current_style(a.svg)}


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
    if style == "film":
        return set()
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
    if style == "film":
        plan_film(storyboard_id, words, fmt, title, headings, rep)
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
    new_settings: list[PropRequest] = Field(default_factory=list,
                                            description="Every setting used on shot lines that is not in the library")


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

Show what is named, the way a good editor cuts in a visual:
- A country, a city or a region: its flag, or a map of it (with its neighbours). For an \
empire or an old region, map the countries it covered today, with a label.
- A real person: define them once with who, giving the traits people know them by \
(era, job, famous look), then bring them on as an actor: they get their name written \
under them. Define each person once, with the same ID all video long. Never stand an \
anonymous figure in for a named person.
- Years, dates, a period or an order of events: a timeline (have an actor point at \
it, or circle it).
- A specific, named, real thing whose real look matters to the story (a famous \
portrait, a named building, a manuscript, a named machine, a famous event): a photo from \
the archives, taped to the page, or full to fill the frame for a moment. Mark it in ink: \
circle a detail, point at it, write a label next to it. Photos are rare seasoning: the \
drawings carry the video. Everyday and generic things (a brain, a hand, a glove, a \
machine in general, an experiment, a room, food) are always drawn, never a photo. Stay \
within the photo budget given with the length; none is fine.
These are where a viewer's eye goes; use them whenever the narration names one.

The beat language. Each line starts with the number of the narration word on which it \
happens. A shot line opens each shot; beat lines follow it:
  N shot "what the viewer sees, in one sentence" [wide|push|drift|close] [cut] [in SETTING]
  N actor WHO [PLACE] [POSE] [FACE] [talk] [small|large] [as ID]
  N draw PROP [PLACE] [small|large] ["label of 1 to 3 words"] [as ID]
  N write "1 to 6 words" [PLACE] [big] [COLOUR] [as ID]
  N flag COUNTRY [PLACE] [small|large] [as ID]          (COUNTRY: two-letter code, like ro or it)
  N map COUNTRY [COUNTRY...] [PLACE] [large] [COLOUR] [as ID]   (those countries in colour, labelled)
  N timeline "label" "label"... [PLACE] [COLOUR] [as ID]        (2 to 7 moments, in order, left to right)
  N who ID "Full Name" TRAIT...                          (defines a person; then: N actor ID ...)
  N photo "search words" [PLACE] [small|large|full] [as ID]   (a real picture from Wikimedia Commons)
  N do ACTOR POSE|ACTION [TARGET] [FACE]
  N face ACTOR FACE
  N move ACTOR|PROP PLACE|TARGET [GAIT]   (to a thing: walks up to it, or lands on it)
  N arrow FROM TO [COLOUR]
  N circle|underline|cross|tick TARGET [COLOUR]
  N shake|bounce|pulse|spin|grow|shrink TARGET
  N erase TARGET|all
  N look TARGET|all          (the camera moves in on something, or back out)
PLACE: left center right top bottom top_left top_right bottom_left bottom_right
POSE (held until the next one): stand point hold push pull carry drop shrug think facepalm cheer wave \
surprised sad crossed hips present scared. point, hold, push, pull and carry take a TARGET, and \
the actor walks up to it by itself; carry picks a prop up, and it goes wherever the actor \
moves until drop (or any other pose) lets it fall.
ACTION (a recorded movement, played once, about 1 to 3 seconds): {actions}. kick and throw \
take a TARGET, which flies off.
GAIT (how a move gets there): walk (default) run march zombie
TRAIT (for who; give 2 to 5): hair {hair}; hair colour {colours}; face {facial}; head {hats}; \
eyes {eyes}; clothes {outfits}; skin {skins}. A monk: tonsure robe. A pope or bishop: mitre robe. \
A Roman emperor: laurel toga. A king or queen: crown. A scientist: lab_coat.
Photo search words: 2 to 5 plain words that would be in the title of a museum or Wikimedia \
file, the name first ("Pope Gregory XIII portrait", "Hagia Sophia", "Gutenberg Bible", \
"Easter table manuscript"); nothing abstract ("confusion", "time passing").
FACE: {faces}
COLOUR: warm (flow, emphasis), bad (problems, myths), good (answers, truth), gold, green
TARGET and FROM/TO: an actor's name, a prop's name, an ID given with `as`, or a PLACE.
SETTING: where the shot happens, drawn behind it in the cartoon look. Give one on the first \
shot; later shots stay there until a shot names another. Change it only when the story \
moves (about 2 to 5 settings in a Short, one or two per chapter of a long video). Use the \
setting library's names when they fit; for anywhere else (a Roman forum, a laboratory) \
invent a snake_case name and describe it in new_settings. Prefer a concrete place where \
the people or things of the shot would really be. idea (an abstract colour field) is only \
for a single shot of pure explanation, at most one shot in five; the shot after it names \
a real place again.
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
play people in the story ({cast}). Prefer hands-on poses (hold, push, pull, carry), \
actions and motions over pointing: point at most once per shot. An action is a strong \
beat: use one where the narration has energy (a jump for a surprise, a stumble for a \
mistake, a dance for a win), not in every shot: about one every 20 to 40 seconds, and \
two or three in a Short. Have actors walk or run between places with move.
- Write at most one word or number per shot, and only when it must be remembered.
- Camera: wide is the default; push slowly moves in, for a key moment; drift for calm \
stretches; close frames small things tightly. Use look for a reveal.
- Editing: by default the camera glides to the next shot; add cut to a shot for a hard \
cut instead. Mix both: cut on a surprise, a change of place or a punchline, glide when \
the story flows on. A map, a flag or a timeline often deserves its own short shot.
- Vary the patterns: no two shots in a row with the same beats.
- Props are single objects (a coin, a fridge, a human eye), never scenes. Reuse a library \
prop by its exact name only when it is exactly the object this moment needs (the same \
thing, era and look); otherwise invent a specific snake_case name (roman_denarius, not \
coin), use it in a draw line and describe it in new_props for the illustrator.
- {format_rules}

A short example, for a different video:
0 shot "The narrator drops toast; it lands butter side down" in kitchen
0 actor narrator left deadpan talk
2 draw toast center
3 do narrator carry toast
5 move narrator center
6 do narrator drop
7 do narrator stumble
7 write "butter side down" top bad as myth
9 do narrator facepalm
12 shot "A table is exactly the wrong height" push
12 draw kitchen_table center large
14 arrow kitchen_table toast warm
16 circle kitchen_table
18 do narrator point kitchen_table surprised
21 shot "Murphy, the engineer behind the law" cut in air_base
21 who murphy "Edward Murphy" short brown_hair uniform
22 actor murphy right crossed
24 flag us top_left small
26 timeline "1949" "1952" "today" top
29 do murphy point timeline
"""

BEAT_SECONDS = {"long": 2.2, "short": 1.6}    # a beat line this often: lively without crowding

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
    actions = ", ".join(f"{name} ({clip['what']})" for name, clip in ink.moves().items() if name in beats.ACTIONS)
    return (INK_PLAN.format(faces=" ".join(art.EXPRESSIONS), cast=cast, actions=actions,
                            format_rules=INK_FORMAT_RULES[fmt], hair=" ".join(ink.HAIR),
                            colours=" ".join(ink.HAIR_COLOURS), facial=" ".join(ink.FACIAL),
                            hats=" ".join(ink.HEADWEAR), eyes=" ".join(ink.EYEWEAR), outfits=" ".join(ink.OUTFITS),
                            skins=" ".join(ink.SKINS))
            + f"\n<cast>\n{CAST}\n</cast>\n\n<prop_library>\n{props}\n</prop_library>"
            + f"\n\n<setting_library>\n{places_mod.listing()}\n</setting_library>")


PHOTO_EVERY = {"ink": 40, "film": 30}      # at most one archive photo per this many seconds of narration


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
        rep.info(f"{cut} archive photos over the budget (one per {PHOTO_EVERY[style]} s of narration) were left "
                 f"out; {'the drawings' if style == 'ink' else 'cards and type'} carry those moments")


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
                  f"<length>{seconds:.0f} seconds: aim for about {max(4, round(seconds / BEAT_SECONDS[fmt]))} beat "
                  f"lines in all, including about {max(1, round(seconds / 25))} ACTION lines and "
                  f"{max(1, round(seconds / 30))} move lines for actors; at most {photo_budget('ink', seconds)} "
                  f"photo lines</length>\n\n"
                  f"<narration>\n{numbered_words(part)}\n</narration>")
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
        part[0]["start_word"] = offset          # every chapter starts its own shot...
        part[0]["page"] = n > 0                 # ...on a new row of the page
        shots.extend(part)
        descriptions.update({p.name: p.description for p in result.new_props})
        settings.update({beats._name(p.name): p.description for p in result.new_settings})
    _report_photo_cut(cut, "ink", rep)
    shots, dropped = beats.clean(shots, len(words))
    if dropped:
        rep.warn(f"{len(dropped)} planned lines could not be used and were left out, such as: "
                 + "; ".join(dropped[:3]))
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
    rep.info(f"Liveliness {score['score']}/100 ({score['verdict']})"
             + "".join(f"; {x['label'].lower()} {x['points']}" for x in score["lines"]))


# --- planning a film (the evidence wall) -----------------------------------------------------

class FilmPlan(BaseModel):
    beats: str = Field(description="The storyboard: shot lines and beat lines, one per line")


FILM_PLAN = """\
You storyboard a narrated documentary for a faceless YouTube channel, in the style of \
an evidence wall. One camera travels over one big wall where things get pinned as the \
story is told: archive photos, index cards, typeset pages, maps, big type and numbers. \
Every hit (a print landing, a stamp, a highlighter swipe, a number rolling up) lands on \
the word the narrator says. Code lays out the wall and moves the camera: you decide \
what appears, and on which word.

The beat language. Each line starts with the number of the narration word on which it \
happens. A shot line opens each shot (a cluster of things about one idea); beat lines \
follow it:
  N shot "what this cluster shows, in one sentence" [cut]
  N photo "search words" [label "NAME BAR"] [as ID]   a real picture from Wikimedia Commons, pinned
  N flag COUNTRY [label "TEXT"] [as ID]               (COUNTRY: two-letter code, like fr or us)
  N map COUNTRY [COUNTRY...] [label "TEXT"] [as ID]   a map print of those countries and their neighbours
  N pin COUNTRY "LABEL" [on MAP_ID]                   a pin on a map where something happens
  N route COUNTRY COUNTRY [on MAP_ID]                  a journey drawn on a map
  N big "1 TO 4 WORDS" [bar] [as ID]                  big type: a title, a year, a name, a verdict
  N count "NUMBER" "CAPTION" [as ID]                  a number rolling up ("$50,000", "35", "12%")
  N card "TITLE" "line" "line"... [as ID]             an index card, typed: a rule, a list, a comparison \
(up to 4 lines)
  N doc "SOURCE" "DATE LINE" "the exact words" [as ID]  a typeset page quoting a real document
  N stamp "WORD" on ID                                a verdict slammed onto a thing
  N highlight ID ["phrase"]                           a highlighter on a phrase of a card or a page \
(on big type: a yellow block behind it)
  N circle|underline|cross ID                         pen marks
  N string ID ID                                      red string: this leads to that
  N look ID                                           the camera goes back to something already on the wall
  N wide                                              the camera pulls back to the whole cluster

How to do it well:
- One hero per sentence: each sentence of narration gets one new thing or one mark, on the \
word it is about (the name as it is said, the year, the verdict word, the number). \
Something new every 2 to 3 seconds; never more than 4 seconds without a beat.
- A new shot every 2 to 4 sentences, when the story moves to a new idea, person or time. \
2 to 6 things per shot. The camera follows a red string from one shot to the next; add \
cut to a shot line for a hard jump instead (a surprise, a turn).
- A specific, named, real person, place, building, document, machine or event whose real \
look matters: a photo, with 2 to 5 plain words that would be in a museum or Wikimedia \
file title, the name first ("Pope Gregory XIII portrait", "Hagia Sophia", "Gutenberg \
Bible"); give people a label with their name. Photos are the strongest hits on the wall, \
so keep them for the moments that need a real face or place: big type, counts and cards \
carry the rest. Never a photo of a generic or everyday thing (a brain, a hand, a glove, \
an experiment, a lab, food) or anything abstract ("confusion", "time passing"): use a \
card or big type. Stay within the photo budget given with the length; none is fine.
- Numbers the viewer should feel (money, counts, distances, durations): count. Years and \
titles: big. One big with bar per shot at most.
- Rules, steps, lists, comparisons, two dates side by side: cards. Keep card lines short \
(under 30 characters).
- doc only quotes words a real document really said (a letter, a newspaper, a law, a \
decree), word for word, from the narration or the script: it is shown as a recreation. \
Never invent or paraphrase a quote; when unsure, use a card.
- Places and journeys: a map of the countries involved, then pins on the word each place \
is said, and route for a journey.
- stamp is for a verdict (FAILED, BANNED, SKIPPED, SOLD): about one every 15 seconds at \
most. highlight picks out the phrase that matters on a card or a page. string ties two \
things when one leads to the other.
- Show a phrase one way: never type on screen words the narrator is saying in full; type \
only names, numbers, dates and short labels.
- Nothing invented that looks real: drawn things are cards and type; real-looking things \
are real photos.
- IDs: snake_case, given with `as`; refer only to things already made.
- {format_rules}

A short example, for a different video:
0 shot "The first flight, on the date it happened"
0 big "DECEMBER 17, 1903" bar as date
4 photo "Wright Flyer first flight 1903" label "KITTY HAWK" as flight
9 count "12" "SECONDS" as secs
12 count "120" "FEET" as feet
14 string secs feet
17 shot "The rival with government money" cut
18 photo "Samuel Langley portrait" label "SAMUEL LANGLEY" as langley
22 count "$50,000" "WAR DEPARTMENT" as money
26 photo "Langley Aerodrome Potomac" as crash
30 stamp "FAILED" on crash
33 doc "THE NEW YORK TIMES" "9 OCTOBER 1903" "one million to ten million years" as nyt
36 highlight nyt "ten million years"
"""

FILM_SECONDS = {"long": 3.2, "short": 2.4}       # a beat line this often

FILM_FORMAT_RULES = {
    "short": "The frame is vertical (9:16) and captions cover the lower third; the camera frames one thing at a "
             "time. A Short is 4 to 9 shots.",
    "long": "The frame is horizontal (16:9) and this is one chapter of a longer video. A new shot every 3 to 5 "
            "sentences; something new every 3 to 4 seconds. Open the chapter with big type or a photo that sets up "
            "its subject.",
}


def plan_film(storyboard_id: int, words: list[dict], fmt: str, title: str, headings: dict, rep: Reporter) -> None:
    """Plan a film storyboard: shots of beat lines on the evidence wall, one call per chapter."""
    system = FILM_PLAN.format(format_rules=FILM_FORMAT_RULES[fmt])
    chunks = _chunks(words) if fmt == "long" else [(0, words, None)]
    rep.info(f"Planning the wall for {words[-1]['e']:.0f} seconds of narration"
             + (f", chapter by chapter ({len(chunks)})" if len(chunks) > 1 else ""))

    def run(chunk: tuple[int, list[dict], str | None]) -> tuple[int, int, FilmPlan]:
        offset, part, key = chunk
        chapter = headings.get(key, "") if key else ""
        chapter_line = f"<chapter>{chapter}</chapter>\n\n" if chapter else ""
        seconds = part[-1]["e"] - part[0]["s"]
        prompt = (f"<video_title>{title}</video_title>\n\n{chapter_line}"
                  f"<length>{seconds:.0f} seconds: aim for about {max(4, round(seconds / FILM_SECONDS[fmt]))} "
                  f"beat lines in all, at most {photo_budget('film', seconds)} photo lines</length>\n\n"
                  f"<narration>\n{numbered_words(part)}\n</narration>")
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
    shots, dropped = film.clean(shots, len(words))
    if dropped:
        rep.warn(f"{len(dropped)} planned lines could not be used and were left out, such as: "
                 + "; ".join(dropped[:3]))
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

DRAW = f"""\
You illustrate one prop for an animated explainer channel, as SVG. Aim for a good editorial \
or picture-book illustration: the viewer recognises this exact object at a glance and it \
looks crafted, not like an icon or clip art.
- viewBox="0 0 512 512"; the object centred and filling about 80 percent of it; a \
three-quarter view when that shows its form better than a flat front view. No background, \
ground, shadow on the ground or scenery: the object alone.
- Accurate: true proportions, and the specific parts that make it this object (its era, \
material and details as described). Draw what the description says, not a generic version: \
its pose and orientation (lying flat, open, tilted) and its real colours (the nearest \
colours below; skin and beige for flesh, rubber, paper and wood that is pale).
- Volume: each main part has its base colour, then a shadow shape (on the side away from \
a light at the top left) and a light shape (on the side towards it), as separate shapes \
with no outline, drawn on top of the base. A few thin white highlight strokes on shiny \
parts. Texture where the material calls for it (wood grain, stitching, rivets) as thin lines.
- Lines: the silhouette and each main part outlined in {art.INK}, stroke-width {art.STROKE}; \
inner details in {art.INK} at stroke-width {art.DETAIL_STROKE}; stroke-linecap="round" and \
stroke-linejoin="round". Shadow and light shapes have no stroke.
- Colours, only these: ink {art.INK}, white #ffffff, and {SHADE_LIST}.
- About 40 to 150 shapes. Allowed elements: path, rect, circle, ellipse, line, polyline, \
polygon, g, with opacity if useful. No text or letters, gradients, filters, masks, images, \
styles, scripts or links.
- A face on an object only if asked.
- It must also read as a line drawing: the channel's ink style keeps only the outlined \
shapes as black lines over a pale wash of the colours. So every part that matters has its \
own outline, and the silhouette is clear."""


class Look(BaseModel):
    matches: bool = Field(description="A viewer would name it as the object described")
    quality: int = Field(ge=1, le=10, description="As an illustration: 1 crude, 5 plain icon, 8 crafted, 10 superb")
    fix: str = Field(description="The most important things to change, in one or two sentences; empty if none")


LOOK = """\
You art-direct illustrations for an animated explainer channel. You see one drawn prop and \
the brief it was drawn from. Judge it as a viewer would on a phone: is it clearly the object \
in the brief, with the details that matter, and is it a crafted illustration (proportions, \
volume, clean shapes) rather than a crude icon? Name concrete fixes (a missing part, wrong \
proportions, a confusing shape, a flat look)."""

GOOD_ENOUGH = 7       # below this, or not the object, the prop is drawn once more with the critique


def look_at_prop(svg: str, name: str, description: str) -> Look:
    """The quick tier looks at the rendered drawing; a failure counts as a pass (the drawing is valid)."""
    image = art.rasterize(svg, 512)
    flat = Image.new("RGB", image.size, art.PAPER)
    flat.paste(image, mask=image.getchannel("A"))
    buffer = io.BytesIO()
    flat.save(buffer, "PNG")
    content = [{"type": "text", "text": f"<brief>{name.replace('_', ' ')}: {description}</brief>"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.standard_b64encode(buffer.getvalue()).decode()}}]
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
        svg = llm.ask("props", DRAW, prompt + note, Drawing, effort=effort).svg
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
        note = (f"\n\nAn art director looked at your last drawing ({review.quality}/10"
                f"{'' if review.matches else ', not recognisable as the object'}): {review.fix} Draw it again, better.")
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
    slow = "" if config.wikimedia_contact() else (": Commons allows 10 searches a minute without "
                                                  "WIKIMEDIA_CONTACT in .env")
    rep.info(f"Finding {len(todo)} archive pictures on Wikimedia Commons (public domain, CC0 and CC BY only)"
             + (f". About {minutes} min{slow}" if minutes else ""))
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
                    rep.info(f"Wikimedia Commons asks to slow down: waiting {seconds:.0f} s, then \"{query}\" again")
                    sleep(seconds)
                    continue
                later = todo[n - 1:]
            except RuntimeError as err:
                rep.warn(f"{query}: {err}")
            break
        if later:
            break
        if found is None:
            missing.append(query)
        else:
            rep.info(f"\"{query}\": {found.title} ({found.license})")
        rep.progress(n / len(todo), f"Found {n - len(missing)} of {len(todo)} pictures")
    if missing:
        rep.warn(f"No usable picture for: {', '.join(missing)}. They show as paper cards with the search; "
                 "edit the search words in the beats and render again.")
    if later:
        rep.warn(f"Wikimedia Commons is still limiting requests, so {len(later)} pictures were not searched: "
                 f"{', '.join(later)}. They show as paper cards for now; render again in a few minutes and only "
                 "these are searched.")


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
        todo = [(a.name, a.description) for a in db.scalars(select(StudioAsset).where(StudioAsset.name.in_(names)))
                if a.svg is None or (a.origin != "you" and not art.current_style(a.svg))]
        old = sum(1 for a in db.scalars(select(StudioAsset).where(StudioAsset.name.in_([n for n, _ in todo])))
                  if a.svg is not None)
    if not todo:
        rep.info("Every prop is already in the library")
        return
    rep.info(f"Drawing {len(todo)} props as illustrations: {', '.join(n for n, _ in todo)}"
             + (f" ({old} of them were flat drawings in the old style, redrawn; your own are never touched)"
                if old else ""))
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
