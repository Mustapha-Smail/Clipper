"""Places for the cartoon look: where each shot happens, and the drawings behind it.

The planner names a place on shot lines ("12 shot ... in scriptorium"); a shot
without one stays where the one before was. Six places are drawn by code
(`cartoon.PLACES`); any other is drawn once by Claude in the house style and
kept in `studio_places`, wide for long videos and tall for Shorts, each only
when a cartoon video first needs it. Storyboards planned before places existed
get them from a quick call (`assign`) the first time they are drawn as cartoons,
so changing the look never needs a new plan.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import llm
from ..db import session_scope
from ..db.models import Storyboard, StudioPlace, utcnow
from ..jobs import Reporter
from . import art, beats, cartoon
from .cartoon import OUT, TALL_VIEW, WIDE_VIEW

FALLBACK = "idea"
MAX_SVG = 90_000


def library() -> dict[str, str]:
    """Every place by name: the six drawn by code, then those in the database."""
    out = dict(cartoon.BUILT_IN)
    with session_scope() as db:
        out.update({p.name: p.description for p in db.scalars(select(StudioPlace).order_by(StudioPlace.name))})
    return out


def listing() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in library().items())


def resolved(shots: list[dict]) -> list[str]:
    """The place of every shot, carried forward; shots before any named place use the first one named."""
    first = next((sh["place"] for sh in shots if sh.get("place")), FALLBACK)
    return [beats.place_at(shots, i) or first for i in range(len(shots))]


# --- choosing places for an older storyboard -------------------------------------------------------

class PlaceRequest(BaseModel):
    name: str = Field(description="snake_case, e.g. roman_forum")
    description: str = Field(description="The place in one sentence, for an illustrator: what is there, which era")


class Assignment(BaseModel):
    places: list[str] = Field(description="One place name per shot, in order")
    new_places: list[PlaceRequest] = Field(description="Every place used that is not in the library")


ASSIGN = """\
You choose where each shot of an animated explainer happens, for its cartoon backgrounds. \
Give one place per shot. Keep a place for several shots in a row and change only when the \
story moves somewhere else: about 2 to 5 places in a Short, 4 to 10 in a long video. Use \
the library's places by their exact names when they fit; when the story clearly happens \
somewhere else (a Roman forum, a laboratory, a monastery), invent a snake_case name and \
describe it in new_places. Prefer a concrete place where the people or things of the shot \
would really be; idea (an abstract colour field) is only for a single shot of pure explanation, \
at most one shot in five."""


def assign(storyboard_id: int, rep: Reporter) -> None:
    """Give every shot of an older storyboard a place. About a tenth of a cent."""
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        shots = list(board.scenes)
        title = board.title
    if not shots or any(sh.get("place") for sh in shots):
        return
    notes = "\n".join(f"{i + 1}. {sh.get('note') or '(no note)'}" for i, sh in enumerate(shots))
    answer: Assignment = llm.ask("places_assign", ASSIGN + f"\n\n<place_library>\n{listing()}\n</place_library>",
                                 f"<video_title>{title}</video_title>\n\n<shots>\n{notes}\n</shots>", Assignment,
                                 effort="low")
    names = [beats._name(n) for n in answer.places]
    if not names:
        raise RuntimeError("No places came back for the shots")
    names += [names[-1]] * (len(shots) - len(names))
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        updated = []
        for i, sh in enumerate(board.scenes):
            same = i > 0 and names[i] == names[i - 1]
            updated.append({**sh, "place": "" if same else names[i]})
        board.scenes = updated
        _register(db, names, {beats._name(p.name): p.description for p in answer.new_places})
    rep.info(f"Places chosen for {len(shots)} shots: {', '.join(dict.fromkeys(names))}")


def _register(db, names, descriptions: dict[str, str]) -> None:
    """Add places the library does not have yet, to be drawn when a cartoon needs them."""
    for name in dict.fromkeys(names):
        if name and beats.NAME.match(name) and name not in cartoon.PLACES and db.get(StudioPlace, name) is None:
            db.add(StudioPlace(name=name, description=descriptions.get(name, name.replace("_", " "))))


def register(names: list[str], descriptions: dict[str, str]) -> None:
    with session_scope() as db:
        _register(db, names, descriptions)


# --- drawing ------------------------------------------------------------------------------------

class Drawing(BaseModel):
    svg: str = Field(description="One complete SVG document")


def _brief(tall: bool) -> str:
    vw, vh = TALL_VIEW if tall else WIDE_VIEW
    floor = round(vh * cartoon.FLOOR)
    # what the camera sees when it rests on the middle of the place
    seen_w = round(vh / cartoon.OVERSCAN * (9 / 16 if tall else 16 / 9))
    left, right = round(vw / 2 - seen_w / 2), round(vw / 2 + seen_w / 2)
    actors = (round(vw / 2 - seen_w / 6), round(vw / 2 + seen_w / 6))
    top = round(floor - (vh / cartoon.OVERSCAN) * (0.45 if tall else 0.62))
    return f"""\
You draw one background (a place) for a cartoon explainer channel, as SVG. House style:
- viewBox="0 0 {vw} {vh}" ({'a tall picture, for vertical Shorts' if tall else 'a wide picture'}). \
The floor or ground meets the wall or horizon at y={floor}; below it is floor or ground only, \
with low, flat details (a rug, tiles, a path, grass).
- The camera mostly shows x {left} to {right} and y {top} to {vh}: put the place's best details \
there, on the wall or horizon between y {top} and {floor}. Beyond it, continue the place more \
simply, for when the camera moves.
- Characters stand in front, in the middle (x {actors[0]} to {actors[1]}): nothing tall or busy \
there, just wall, sky or a calm background.
- Flat colours, soft and slightly muted so colourful characters stand out; every shape outlined \
in {OUT}, stroke-width 4, round joins. Depth from overlapping shapes and darker tones.
- Rich but tidy: 60 to 150 shapes. A clear sense of place and era.
- Allowed elements: path, rect, circle, ellipse, line, polyline, polygon, g. No text, no people, \
no gradients, filters, masks, images, styles, scripts or links."""


def draw(name: str, description: str, tall: bool, instruction: str = "") -> str:
    """Claude draws the place. About 3 to 5 cents."""
    prompt = f"Draw: {name.replace('_', ' ')}. {description}"
    if instruction:
        prompt += f"\nChange requested by the creator: {instruction}"
    error = ""
    for _ in range(3):
        svg = llm.ask("places", _brief(tall), prompt + (f"\n\nYour last drawing was rejected: {error}. Fix that."
                                                         if error else ""), Drawing, effort="medium").svg
        try:
            return check(svg, tall)
        except Exception as err:  # noqa: BLE001 - any rendering failure means: draw it again
            error = str(err)[:300]
    raise RuntimeError(f"Could not draw the place {name}: {error}")


def check(svg: str, tall: bool) -> str:
    svg = art.check_prop_svg(svg, limit=MAX_SVG)
    view = cartoon.re_view(svg)
    want = TALL_VIEW if tall else WIDE_VIEW
    if view is None or abs(view[0] / view[1] - want[0] / want[1]) > 0.08:
        raise ValueError(f'The viewBox must be "0 0 {want[0]} {want[1]}"')
    return svg


def needed(board: Storyboard) -> list[str]:
    return [n for n in dict.fromkeys(resolved(board.scenes)) if n not in cartoon.PLACES]


def prepare(storyboard_id: int, rep: Reporter) -> None:
    """Before a cartoon is drawn: places for every shot, and a drawing of each in the right shape."""
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        if board.style != "ink" or board.look != "cartoon" or not board.scenes:
            return
    assign(storyboard_id, rep)
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        tall = board.format == "short"
        names = needed(board)
        _register(db, names, {})
        db.flush()
        todo = [(p.name, p.description) for p in db.scalars(select(StudioPlace).where(StudioPlace.name.in_(names)))
                if (p.svg_tall if tall else p.svg) is None] if names else []
    if not todo:
        return
    rep.info(f"Drawing {len(todo)} new {'tall ' if tall else ''}places: {', '.join(n for n, _ in todo)}")
    failed = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(draw, name, desc, tall): name for name, desc in todo}
        for n, future in enumerate(futures, 1):
            name = futures[future]
            try:
                svg = future.result()
            except RuntimeError as err:
                failed.append(name)
                rep.warn(str(err))
                continue
            with session_scope() as db:
                place = db.get(StudioPlace, name)
                if tall:
                    place.svg_tall = svg
                else:
                    place.svg = svg
                place.updated_at = utcnow()
            rep.progress(n / len(todo), f"Drawn {n - len(failed)} of {len(todo)} places")
    if failed:
        rep.warn(f"{len(failed)} places could not be drawn and show the plain colour background: "
                 + ", ".join(failed))


def drawings(names: list[str], tall: bool) -> dict[str, str]:
    """The stored drawings of these places in the shape asked for (the other shape if that is all there is)."""
    with session_scope() as db:
        rows = db.scalars(select(StudioPlace).where(StudioPlace.name.in_(names))).all() if names else []
        return {p.name: (p.svg_tall or p.svg) if tall else (p.svg or p.svg_tall) for p in rows
                if p.svg or p.svg_tall}


def safe_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")[:40]
    if not beats.NAME.match(name):
        raise ValueError("A place name is lowercase letters, digits and underscores")
    return name
