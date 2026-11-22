"""The ink stage: an ink storyboard filmed on one long sheet of paper.

Every shot is a spot on the page, left to right; a shot marked `page` starts a
new row, for a new chapter. The camera glides from spot to spot, so the video
is one continuous drawing rather than a run of slides, and the cast walk along
with it. Within a shot, things draw themselves on at their word, actors change
pose with a little spring, walk up to what they act on and reach for it, and
marks (circles, arrows, crosses) are drawn over what they point at.

Drawn things change 12 times a second (animation "on twos", which reads as
hand-made); the camera moves at the full 24. The whole layer of ink is warped
by one of three noise fields that take turns 8 times a second: the "boil" of
hand-drawn animation, at the cost of one remap per frame.
"""

import math
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import captions as captions_mod
from ..settings import Settings, hex_to_rgb, resolve_font
from . import animate, art, beats, cartoon, composition, data_graphics, geo, ink, looks, photos

FPS = 24
ART_FPS = 12
# The hand-drawn wobble of the lines: (pixels at 1080p, changes per second). Subtle by default:
# a strong boil on every line, all the time, is tiring to watch.
WOBBLE = {"off": (0.0, 1), "subtle": (0.7, 4), "strong": (2.2, 8)}
BODIES = ("actor", "prop", "text", "flag", "map", "timeline", "photo", "data")  # placed stage objects
DRAW_TIME = {"actor": 0.7, "prop": 0.8, "arrow": 0.45, "circle": 0.55, "underline": 0.35, "cross": 0.35,
             "tick": 0.3, "flag": 0.9, "map": 1.5, "timeline": 1.3, "tag": 0.6, "photo": 0.6,
             "data": 0.45}
TAG_TIME = 3.2               # how long a person's name stays under them when they first appear
ERASE_TIME = 0.3
POSE_TIME = 0.45
MOVE_SPEED = 0.5             # of the frame width per second, walking
GAIT_SPEED = {"walk": 1.0, "run": 2.1, "march": 0.9, "zombie": 0.45}     # times MOVE_SPEED
LIFT_TIME = 0.25             # picking something up
FALL_TIME = 0.4              # something dropped falls to the ground
PAN_TIME = 0.9
PAGE_TIME = 1.2
LOOK_TIME = 0.6
BANNER = 3.4

SIZES = {   # pixels at zoom 1
    "short": {"actor": {"small": 440, "medium": 600, "large": 760}, "prop": {"small": 300, "medium": 440, "large": 600},
              "text": {"small": 70, "medium": 96, "large": 128}},
    "long": {"actor": {"small": 340, "medium": 450, "large": 580}, "prop": {"small": 240, "medium": 350, "large": 480},
             "text": {"small": 60, "medium": 80, "large": 108}},
}
SLOTS = {   # where things go within a shot, as fractions of the frame; closer together than the flat style's
    "short": animate.LAYOUTS["short"].slots,
    "long": {"top_left": (0.27, 0.25), "top": (0.5, 0.22), "top_right": (0.73, 0.25),
             "left": (0.28, 0.6), "center": (0.5, 0.58), "right": (0.72, 0.6),
             "bottom_left": (0.28, 0.86), "bottom": (0.5, 0.86), "bottom_right": (0.72, 0.86)},
}
ZOOM = {"wide": (1.0, 1.15), "push": (1.0, 1.12), "drift": (1.0, 1.12), "close": (1.05, 1.5)}
MARK_COLOR = {"circle": "warm", "underline": "warm", "cross": "bad", "tick": "good", "arrow": "warm"}
Feet = tuple[float, float]


@dataclass
class Thing:
    """Something on the page: an actor, a prop, writing, an arrow or a mark."""
    id: str
    kind: str                     # actor, prop, text, arrow, mark
    shot: int
    born: float
    line: beats.Line
    order: int
    gone: float | None = None     # when it starts to be erased
    spans: list[tuple[int, float]] = field(default_factory=list)   # (shot, from when): actors move between shots
    box: tuple[float, float, float, float] = (0, 0, 0, 0)    # page centre x, y and w, h (after layout)
    scale: float = 1.0
    motions: list[tuple[float, str]] = field(default_factory=list)
    # actors
    home: Feet = (0.0, 0.0)                                     # where the feet are when it appears
    # (t, "pose" or "move", pose or place, target, shot), turned into poses and moves shot by shot
    requests: list[tuple[float, str, str, str, int]] = field(default_factory=list)
    poses: list[tuple[float, ink.Pose, ink.Pose, str]] = field(default_factory=list)  # (t, from, to, name)
    moves: list[tuple[float, float, Feet, Feet]] = field(default_factory=list)       # (t, duration, from, to)
    faces: list[tuple[float, str]] = field(default_factory=list)
    facing: list[tuple[float, str]] = field(default_factory=list)
    gaits: list[tuple[float, float, str]] = field(default_factory=list)   # (start, end, walk|run|march|zombie)
    acts: list[tuple[float, str]] = field(default_factory=list)           # recorded moves: (start, name)
    # props: held by an actor from start to end (None: still held)
    held: list[list] = field(default_factory=list)
    look: dict = field(default_factory=dict)                     # someone in particular (`who`)
    owner: "Thing | None" = None                                # a name tag's person
    # writing, arrows and marks
    strokes: list = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    size_px: int = 0
    color: str = ink.INK


def paper(width: int, height: int, seed: int = 11) -> np.ndarray:
    """Off-white paper with a faint grain and soft corners."""
    rng = np.random.default_rng(seed)
    base = np.array(hex_to_rgb(ink.PAPER), np.float32)
    grain = rng.normal(0, 1.6, (height, width, 1)).astype(np.float32)
    mottle = rng.normal(0, 1, (height // 40 + 2, width // 40 + 2)).astype(np.float32)
    mottle = np.asarray(Image.fromarray(mottle).resize((width, height), Image.BICUBIC))[..., None] * 0.9
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    r = np.hypot((x - width / 2) / (width / 2), (y - height / 2) / (height / 2))
    vignette = (1 - 0.06 * np.clip(r - 0.55, 0, 1) ** 1.5)[..., None]
    return np.clip((base + grain + mottle) * vignette, 0, 255).astype(np.uint8)


def boil_fields(width: int, height: int, count: int = 3, seed: int = 5,
                strength: float = 2.2) -> list[tuple[np.ndarray, np.ndarray]]:
    """Smooth random displacement maps: drawn lines wobble a little differently in each."""
    rng = np.random.default_rng(seed)
    amp = strength * min(width, height) / 1080
    cell = max(24, int(min(width, height) / 22))
    gy, gx = np.mgrid[0:height, 0:width].astype(np.float32)
    out = []
    for _ in range(count):
        maps = []
        for _axis in range(2):
            low = rng.normal(0, 1, (height // cell + 3, width // cell + 3)).astype(np.float32)
            big = np.asarray(Image.fromarray(low).resize((width + 2 * cell, height + 2 * cell), Image.BICUBIC))
            maps.append(big[cell:cell + height, cell:cell + width] * amp)
        out.append((gx + maps[0], gy + maps[1]))
    return out


class InkComposer:
    """Draws any frame of an ink storyboard."""

    def __init__(self, board: dict, props: dict[str, str | None], settings: Settings, mouths: np.ndarray | None):
        self.board = board
        self.format = board["format"]
        self.layout = animate.LAYOUTS[self.format]
        self.W, self.H = self.layout.width, self.layout.height
        self.sizes = SIZES[self.format]
        self.props = props
        self.photos: dict[str, dict] = board.get("photos") or {}     # search -> {path, width, height}
        self.data_cards: dict[str, dict] = board.get("data_cards") or {}
        self.mouths = mouths
        self.words = board["words"]
        self.duration = board["end"] - board["start"]
        self.scenes = board["scenes"] or [beats.shot(0, "")]
        self.shots = self.scenes
        self.starts = [0.0] + [self.word_time(s["start_word"]) for s in self.shots[1:]]
        self.font_path = resolve_font(settings)
        self.captions = None
        if self.layout.captions and settings.captions_enabled and self.words:
            highlight = hex_to_rgb(settings.highlight_color)
            self.captions = captions_mod.CaptionRenderer(captions_mod.group_words(self.words, 0.0, self.duration),
                                                         self.font_path, highlight)
        self.compositions = [composition.geometry(self.format, shot.get("composition"),
                                                   captions=self.captions is not None)
                             for shot in self.shots]
        self.chapters: list[tuple[float, int, str]] = []
        if self.format == "long":
            seen = None
            for w in self.words:
                key = w.get("section")
                if key and key != seen:
                    if seen is not None:
                        self.chapters.append((w["s"], len(self.chapters) + 2, board.get("headings", {}).get(key, "")))
                    seen = key
        self.origins = self._origins()
        self.arrivals: list[float] = []      # when each shot first has something to show
        self.things: list[Thing] = []
        self.looks: list[tuple[float, str | None, int]] = []
        self._build()
        self.keyframes = self._camera_plan()
        self.look = looks.get(board.get("look"))
        self.paper = Image.fromarray(looks.background(self.look, self.W, self.H, paper), "RGB").convert("RGBA")
        strength, self.boil_fps = WOBBLE.get(getattr(settings, "ink_wobble", "subtle"), WOBBLE["subtle"])
        strength *= self.look.jitter
        self.boil = boil_fields(self.W, self.H, strength=strength) if strength else []
        # Drawings are kept to be reused, up to a memory budget (not a count: a map or a
        # photo weighs as much as a hundred props). Previews in the API keep less.
        self._cache: OrderedDict = OrderedDict()
        self._cache_bytes = 0
        self.cache_limit = 250 * 1024 * 1024
        self._sized: OrderedDict = OrderedDict()      # sprites resized for the current zoom
        self._sized_bytes = 0
        # cartoon: one drawn place per shot, a little larger than the frame so the camera can move across it
        self.places: list[str] = list(board.get("places") or [])
        self.place_svgs: dict[str, str] = dict(board.get("place_svgs") or {})
        self._place_size = cartoon.picture_size(self.W, self.H)
        self._place_pictures: OrderedDict = OrderedDict()      # a few places drawn, the latest kept
        self._place_ground: dict[int, float | None] = {}
        self._place_origin: dict[int, float] = {}
        self._dressed: OrderedDict = OrderedDict()    # sprites dressed for the look (cut out, recoloured)
        self._dressed_bytes = 0
        self._last: tuple | None = None

    # --- time --------------------------------------------------------------------------

    def word_time(self, index: int | None) -> float:
        if index is None or not self.words:
            return 0.0
        return self.words[max(0, min(index, len(self.words) - 1))]["s"]

    def shot_at(self, t: float) -> int:
        index = 0
        for i, start in enumerate(self.starts):
            if t >= start:
                index = i
        return index

    def shot_end(self, i: int) -> float:
        return self.starts[i + 1] if i + 1 < len(self.starts) else self.duration

    def pan_start(self, i: int) -> tuple[float, float]:
        """When the camera starts moving to shot i, and for how long.

        It leaves when the new shot has something to show (cutting on the
        action), so a shot whose first drawing comes a few words in never
        opens on blank paper: the shot before stays on screen until then.
        """
        if i == 0:
            return 0.0, 0.0
        arrive = self.arrivals[i] if i < len(self.arrivals) else self.starts[i]
        if self.shots[i].get("cut"):        # a hard cut: the picture changes at once
            return arrive, 0.0
        duration = PAGE_TIME if self.shots[i].get("page") else PAN_TIME
        return max(0.0, arrive - duration * 0.35), duration

    # --- building the page ---------------------------------------------------------------

    def _origins(self) -> list[tuple[float, float]]:
        """Where each shot sits on the page: left to right, a new row for each `page` shot."""
        out, col, row = [], 0, 0
        for i, sh in enumerate(self.shots):
            if i and sh.get("page"):
                row += 1
                col = 0
            elif i:
                col += 1
            jitter = (0.06 if col % 2 else -0.04) * self.H
            out.append((col * self.W * 1.12, row * self.H * 1.35 + jitter))
        return out

    def _slot(self, shot: int, slot: str, lane: str = "") -> tuple[float, float]:
        ox, oy = self.origins[shot]
        geo = self.compositions[shot]
        if not geo.single:
            x, y = composition.slot(self.format, slot, geo.viewport(lane or "a"))
            return ox + x, oy + y
        slots = SLOTS[self.format]
        fx, fy = slots.get(slot or "center", slots["center"])
        return ox + fx * self.W, oy + fy * self.H

    def _build(self) -> None:
        alive: dict[str, Thing] = {}       # what is still on the page from earlier shots, by id
        order = 0
        # A person keeps the look of their first definition all video long (chapters are
        # planned apart, so one may be defined again), and their name is written only once.
        self.personas: dict[str, tuple[str, dict]] = {}
        for sh in self.shots:
            for x in beats.parsed(sh):
                if x.verb == "who" and x.who not in self.personas:
                    self.personas[x.who] = (x.text, ink.look_of(x.extra))
        named: set[str] = set()
        for i, sh in enumerate(self.shots):
            start = self.starts[i]
            lines = beats.parsed(sh)
            live: dict[str, Thing] = {}
            carried: list[Thing] = []
            # what earlier shots left on the page and this one mentions comes along with the camera
            mentioned = {x.who for x in lines} | {x.target for x in lines}
            for name, thing in alive.items():
                if name in mentioned and thing.gone is None and not (
                        thing.kind != "actor" and any(x.verb in beats.CREATES and x.id == name for x in lines)):
                    declared = next((x for x in lines if x.verb == "actor" and x.id == name), None)
                    if declared and declared.slot:
                        thing.line.slot = declared.slot
                    live[name] = thing
                    carried.append(thing)
            showing = [max(start, self.word_time(x.word)) for x in lines
                       if x.verb in beats.CREATES or x.who in {c.id for c in carried}]
            self.arrivals.append(min(showing, default=start))
            created: list[Thing] = []
            for line in lines:
                t = max(start, self.word_time(line.word))
                if line.verb == "actor" and line.id in live:
                    if line.pose or line.expression:
                        live[line.id].requests.append((t, "pose", line.pose or "keep", "", i))
                        if line.expression:
                            live[line.id].faces.append((t, line.expression))
                    continue
                if line.verb in beats.CREATES:
                    kind = {"actor": "actor", "draw": "prop", "write": "text"}.get(line.verb, line.verb)
                    thing = Thing(line.id, kind, i, t, line, order, spans=[(i, t)])
                    order += 1
                    if kind in ("text", "timeline"):
                        thing.color = ink.COLORS.get(line.color or "ink", ink.INK)
                    if kind == "actor" and line.expression:
                        thing.faces.append((t, line.expression))
                    if kind == "actor" and line.who in self.personas:
                        name, thing.look = self.personas[line.who]
                    if kind == "actor" and line.who in self.personas and line.who not in named:
                        named.add(line.who)
                        label = beats.Line(word=line.word, verb="tag", text=name, lane=line.lane)
                        tag = Thing(f"tag:{order}", "tag", i, t + 0.6, label,
                                    order + 200_000, gone=t + 0.6 + TAG_TIME, spans=[(i, t)], owner=thing)
                        self.things.append(tag)
                    alive[thing.id] = thing
                    live[thing.id] = thing
                    created.append(thing)
                    self.things.append(thing)
                    continue
                if line.verb in ("arrow", *beats.MARKS):
                    thing = Thing(f"{line.verb}:{order}", "arrow" if line.verb == "arrow" else "mark", i, t, line,
                                  order + 100_000, spans=[(i, t)])
                    order += 1
                    thing.color = ink.COLORS[line.color or MARK_COLOR[line.verb]]
                    self.things.append(thing)
                    live[thing.id] = thing
                    created.append(thing)
                    continue
                if line.verb == "erase":
                    target = live.get(line.who)
                    gone = [x for x in live.values() if x.kind != "actor"] if line.who == "all" else \
                        ([target] if target else [])
                    names = {x.id for x in gone}
                    gone += [x for x in live.values() if x.kind in ("arrow", "mark")
                             and ({x.line.who, x.line.target} & names)]
                    for x in gone:
                        if x.gone is None:
                            x.gone = t
                        alive.pop(x.id, None)
                    continue
                if line.verb == "look":
                    self.looks.append((t, None if line.who == "all" else line.who, i))
                    continue
                target = live.get(line.who)
                if target is None:
                    continue
                if line.verb in beats.MOTIONS:
                    target.motions.append((t, line.verb))
                elif target.kind != "actor":
                    if line.verb == "move":      # a prop or a word slides to another place, or onto something
                        target.requests.append((t, "move", line.slot, line.target, i))
                    continue
                elif line.verb == "face":
                    target.faces.append((t, line.expression or "neutral"))
                elif line.verb == "do":
                    if line.pose:
                        target.requests.append((t, "pose", line.pose, line.target, i))
                    if line.expression:
                        target.faces.append((t, line.expression))
                elif line.verb == "move":
                    target.requests.append((t, f"move:{line.pose or 'walk'}", line.slot, line.target, i))
            self._layout(i, [x for x in created if x.kind in BODIES], carried)
            for x in carried + created:
                if x.kind == "actor":
                    self._resolve_actor(x, live, i)
                elif x.requests:
                    self._resolve_slides(x, i, live)
            for x in created:
                if x.kind in ("arrow", "mark"):
                    self._resolve_mark(x, live)

    def _layout(self, shot: int, created: list[Thing], carried: list[Thing]) -> None:
        """Sizes and places the shot's actors, props and writing, apart from each other."""
        lay = self.layout
        ox, oy = self.origins[shot]
        items, todo = [], []
        for x in carried + created:
            size = x.line.size or "medium"
            cx, cy = self._slot(shot, x.line.slot or ("left" if x.kind == "actor" else "center"), x.line.lane)
            if x in carried:
                w, h = x.box[2], x.box[3]
            elif x.kind == "actor":
                h = self.sizes["actor"][size] * (0.8 if x.line.who == "kid" else 1.0)
                w = h * 0.42
            elif x.kind == "prop":
                box = self.sizes["prop"][size]
                _, _, vw, vh = ink.ink_prop(self._prop_svg(x)).view
                w, h = (box, box * vh / vw) if vw >= vh else (box * vw / vh, box)
                if x.line.text:
                    h += self.sizes["text"]["small"] * 1.1
            elif x.kind == "flag":
                w = self.sizes["prop"][size] * 1.1
                h = w * 0.75 * 1.45 + w * 0.08
            elif x.kind == "map":
                w = self.sizes["prop"][size] * (1.9 if self.format == "long" else 2.0)
                w = min(w, self.W * 0.9)
                h = w * 0.75
            elif x.kind == "photo":
                found = self.photos.get(x.line.text) or {}
                aspect = (found.get("width") or 4) / (found.get("height") or 3)
                stage_h = self.H * (lay.stage_bottom - lay.stage_top)
                if size == "full":
                    w, h = self.W, self.H
                else:
                    pw = {"small": 0.24, "medium": 0.36, "large": 0.5}[size] * self.W * (1.6 if self.format == "short"
                                                                                          else 1.0)
                    ph = pw / aspect
                    if ph > stage_h * 0.78:                 # tall pictures shrink to fit the stage
                        ph = stage_h * 0.78
                        pw = ph * aspect
                    x.size_px = int(pw)
                    w, h = photos.outer(pw, ph, False)
            elif x.kind == "timeline":
                scale = {"small": 0.7, "medium": 1.0, "large": 1.15}[size]
                w = self.W * (0.62 if self.format == "long" else 0.86) * scale
                h = max(22, w / 22) * 3.6
            elif x.kind == "data":
                scale = {"small": 0.68, "medium": 0.84, "large": 1.0}[size]
                w = self.W * (0.42 if self.format == "long" else 0.78) * scale
                h = w * 0.58
            else:
                px = self.sizes["text"][size] * (1.45 if x.line.big else 1.0)
                x.size_px = int(px)
                x.lines = ink.wrap(x.line.text, int(px), self.W * (0.8 if self.format == "short" else 0.5))
                w, h = ink.text_size(x.lines, int(px))
                if x.line.big:
                    h += px * 0.35
            items.append([cx - ox, cy - oy, 1.0, w, h])
            todo.append(x)
        end = self.shot_end(shot)

        def alive(x: Thing) -> tuple[float, float]:
            return (self.starts[shot] if x in carried else x.born,
                    end if x.gone is None else x.gone + ERASE_TIME)

        spans = [alive(x) for x in todo]
        together = {(a, b) for a in range(len(todo)) for b in range(a + 1, len(todo))
                    if spans[a][0] < spans[b][1] and spans[b][0] < spans[a][1]}
        if self.compositions[shot].single:
            placed = animate.separate(items, self.W * animate.MARGIN, self.W * (1 - animate.MARGIN),
                                      self.H * lay.stage_top, self.H * lay.stage_bottom, gap=24, pairs=together)
        else:
            geo = self.compositions[shot]
            placed = [(item[0], item[1], item[2]) for item in items]
            by_lane: dict[str, list[int]] = {}
            for n, thing in enumerate(todo):
                by_lane.setdefault(thing.line.lane or "a", []).append(n)
            for lane, indices in by_lane.items():
                view = geo.viewport(lane)
                local = [items[n] for n in indices]
                local_pairs = {(a, b) for a, ia in enumerate(indices) for b, ib in enumerate(indices)
                               if (min(ia, ib), max(ia, ib)) in together}
                margin = min(view.width, view.height) * animate.MARGIN
                values = animate.separate(local, view.x0 - ox + margin, view.x1 - ox - margin,
                                          view.y0 - oy + margin, view.y1 - oy - margin,
                                          gap=24, pairs=local_pairs)
                for n, value in zip(indices, values, strict=True):
                    placed[n] = value
        for x, (cx, cy, s), item in zip(todo, placed, items, strict=True):
            w, h = item[3] * s, item[4] * s
            if x in carried:
                # it comes over from the shot before while the camera moves: actors walk, things glide
                begin, duration = self.pan_start(shot)
                if x.kind == "actor":
                    here, there = self.position(x, begin), (cx + ox, cy + oy + h / 2)
                    x.facing.append((begin, "right" if there[0] >= here[0] else "left"))
                    x.facing.append((begin + duration, self._face_centre(shot, there[0])))
                else:
                    here, there = self.centre(x, begin), (cx + ox, cy + oy)
                x.moves.append((begin, duration, here, there))
                if x.kind == "actor":
                    x.gaits.append((begin, begin + duration, "walk"))
                x.shot = shot
                x.spans.append((shot, begin))
            elif x.kind == "photo" and x.line.size == "full":
                x.box = (ox + self.W / 2, oy + self.H / 2, self.W, self.H)
            else:
                x.scale = s
                x.box = (cx + ox, cy + oy, w, h)
                if x.kind == "actor":
                    x.home = (cx + ox, cy + oy + h / 2)
                    x.facing.append((x.born, self._face_centre(shot, x.home[0])))
                    first = x.line.pose if x.line.pose in ink.POSE_ANGLES and x.line.pose not in beats.TARGETED \
                        else "stand"
                    pose = ink.Pose.named(first)
                    x.poses.append((x.born, pose, pose, first))

    def _face_centre(self, shot: int, x: float) -> str:
        return "right" if x <= self.origins[shot][0] + self.W / 2 + 4 else "left"

    def _prop_svg(self, thing: Thing) -> str:
        return self.props.get(thing.line.who) or art.placeholder_svg(thing.line.who)

    def _resolve_actor(self, actor: Thing, live: dict[str, Thing], shot: int) -> None:
        """Turn the actor's requests in this shot into walks, poses and recorded moves."""
        for t, kind, value, target_name, at_shot in sorted(actor.requests, key=lambda r: r[0]):
            if at_shot != shot:
                continue
            t = max(t, self._busy_until(actor))
            if kind.startswith("move"):
                gait = kind.partition(":")[2] or "walk"
                here = self.position(actor, t)
                goal = live.get(target_name) if target_name else None
                if goal is not None and not value:      # walk up to it and stop beside it
                    x0, _y0, x1, _y1 = self.body_box(goal, t)
                    side = 1 if (x0 + x1) / 2 >= here[0] else -1
                    there = ((x0 if side > 0 else x1) - side * actor.box[2] * 0.7, here[1])
                else:
                    cx, cy = self._slot(shot, value, actor.line.lane)
                    there = (cx, cy + actor.box[3] / 2)
                self._walk(actor, t, here, there, gait)
                continue
            target = live.get(target_name) if target_name else None
            begin = t
            if target is not None and value in ("hold", "push", "pull", "carry", "kick"):
                here = self.position(actor, t)
                side = 1 if target.box[0] >= here[0] else -1
                arm = 1.7 * ink.ARM * self._unit(actor)
                body = self.body_box(target, t)
                edge = body[0] if side > 0 else body[2]
                stand = edge - side * arm * (0.8 if value != "kick" else 0.9)
                if abs(stand - here[0]) > arm * 0.25:
                    begin = t + self._walk(actor, t, here, (stand, here[1]))
            if target is not None:
                side = "right" if target.box[0] >= self.position(actor, begin)[0] else "left"
                actor.facing.append((begin, side))
            if value == "keep":
                continue
            if value == "drop" or value not in ("carry",):
                self._let_go(actor, begin)
            if value in beats.ACTIONS:
                self._act(actor, begin, value, target)
                continue
            goal = ink.Pose.named(value)
            if target is not None and value in ink.REACH:
                goal = self._reach(actor, goal, value, target, begin)
            before = self.pose(actor, begin)
            actor.poses.append((begin, before, goal, value))
            if value == "carry" and target is not None and target.kind == "prop":
                target.held.append([begin + POSE_TIME * 0.5, None, actor])
        actor.facing.sort(key=lambda f: f[0])

    def _act(self, actor: Thing, begin: float, name: str, target: "Thing | None") -> None:
        """A recorded move: played once, then the actor is back in the pose before, where the move left them."""
        clip = ink.moves()[name]
        actor.acts.append((begin, name))
        here = self.position(actor, begin)
        sign = 1 if self._facing(actor, begin) == "right" else -1
        travel = clip["stride"] * self._unit(actor) * sign
        actor.moves.append((begin + clip["seconds"], 0.0, here, (here[0] + travel, here[1])))
        if target is not None and target.kind != "actor" and name in ("kick", "throw"):
            # what is kicked or thrown flies off the way the actor faces
            hit = begin + clip["seconds"] * 0.45
            start = self.centre(target, hit)
            target.moves.append((hit, 0.55, start, (start[0] + sign * self.W * 0.3, start[1] - self.H * 0.04)))

    def _let_go(self, actor: Thing, t: float) -> None:
        """Whatever the actor is carrying falls to the ground at t."""
        for thing in self.things:
            for hold in thing.held:
                if hold[2] is actor and hold[1] is None and hold[0] < t:
                    hold[1] = t

    def _facing(self, actor: Thing, t: float) -> str:
        facing = "right"
        for at, side in sorted(actor.facing, key=lambda f: f[0]):
            if at <= t:
                facing = side
        return facing

    def _resolve_slides(self, thing: Thing, shot: int, live: dict[str, Thing]) -> None:
        """A prop or a word moved to another place glides there; moved to something, it lands on top of it."""
        here = (thing.box[0], thing.box[1])
        for t, _kind, slot, target_name, at_shot in sorted(thing.requests, key=lambda r: r[0]):
            if at_shot != shot:
                continue
            target = live.get(target_name) if target_name else None
            if target is not None:
                x0, y0, x1, _y1 = self.body_box(target, t)
                there = ((x0 + x1) / 2, y0 - thing.box[3] * 0.32)
            else:
                there = self._slot(shot, slot, thing.line.lane)
            duration = max(0.4, math.hypot(there[0] - here[0], there[1] - here[1]) / (self.W * MOVE_SPEED * 1.6))
            thing.moves.append((max(t, self._busy_until(thing)), duration, here, there))
            here = there

    def centre(self, thing: Thing, t: float) -> tuple[float, float]:
        """Where a prop or a word is at time t: it can slide, be carried, and fall when dropped."""
        x, y = self._resting(thing, t)
        for start, end, actor in thing.held:
            if t < start:
                break
            moment = min(t, end) if end is not None else t
            hands = self._hands(actor, moment)
            sign = 1 if self._facing(actor, moment) == "right" else -1
            held = (hands[0] + sign * thing.box[2] * 0.18, hands[1] - thing.box[3] * 0.05)   # in front, at the chest
            if t < start + LIFT_TIME:                   # picked up: rises into the hands
                k = ink.ease((t - start) / LIFT_TIME)
                before = self._resting(thing, start)
                return before[0] + (held[0] - before[0]) * k, before[1] + (held[1] - before[1]) * k
            if end is None or t < end:
                return held
            ground = self.position(actor, end)[1] - thing.box[3] / 2
            if t < end + FALL_TIME:                     # dropped: falls, faster and faster
                k = ((t - end) / FALL_TIME) ** 2
                return held[0], held[1] + (ground - held[1]) * k
            x, y = held[0], ground
        return x, y

    def _resting(self, thing: Thing, t: float) -> tuple[float, float]:
        x, y = thing.box[0], thing.box[1]
        for start, duration, a, b in thing.moves:
            if t >= start + duration:
                x, y = b
            elif t >= start:
                k = ink.ease((t - start) / duration)
                return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        return x, y

    def _hands(self, actor: Thing, t: float) -> tuple[float, float]:
        """The middle of the actor's two hands, on the page."""
        pose, facing, k, hip = self.figure(actor, t)
        j = ink.joints(pose)
        sign = 1 if facing == "right" else -1
        mx = (j["back_arm"][-1][0] + j["front_arm"][-1][0]) / 2
        my = (j["back_arm"][-1][1] + j["front_arm"][-1][1]) / 2
        return hip[0] + sign * mx * k, hip[1] + my * k

    def _busy_until(self, actor: Thing) -> float:
        moving = max((t + d for t, d, _a, _b in actor.moves), default=0.0)
        acting = max((t + ink.moves()[name]["seconds"] for t, name in actor.acts), default=0.0)
        return max(moving, acting)

    def _walk(self, actor: Thing, t: float, here: Feet, there: Feet, gait: str = "walk") -> float:
        speed = self.W * MOVE_SPEED * GAIT_SPEED.get(gait, 1.0)
        duration = max(0.5, math.hypot(there[0] - here[0], there[1] - here[1]) / speed)
        actor.moves.append((t, duration, here, there))
        actor.gaits.append((t, t + duration, gait))
        if abs(there[0] - here[0]) > 4:
            actor.facing.append((t, "right" if there[0] > here[0] else "left"))
        return duration

    def _unit(self, actor: Thing) -> float:
        """Pixels per unit of the figure drawing, at zoom 1."""
        return actor.box[3] / ink.BOX[3] * 1.12

    def _reach(self, actor: Thing, pose: ink.Pose, name: str, target: Thing, t: float) -> ink.Pose:
        k = self._unit(actor)
        fx, fy = self.position(actor, t)
        facing_right = target.box[0] >= fx
        hip = (fx, fy - ink.FEET_Y * k)
        x0, y0, x1, y1 = self.body_box(target, t)
        if name == "point":
            tx, ty = (x0 + x1) / 2, (y0 + y1) / 2
        else:
            tx = x0 + (x1 - x0) * 0.08 if facing_right else x1 - (x1 - x0) * 0.08
            ty = min(max(hip[1] - ink.TORSO * k * 0.75, y0 + (y1 - y0) * 0.25), y1 - (y1 - y0) * 0.2)
        local = ((tx - hip[0]) / k * (1 if facing_right else -1), (ty - hip[1]) / k)
        out = pose
        for side in ink.REACH[name]:
            aim = local if side == "front" else (local[0] - 8, local[1] + 28)
            out = ink.reach(out, f"{side}_arm", aim)
        return out

    def _resolve_mark(self, mark: Thing, live: dict[str, Thing]) -> None:
        width = max(6.0, self.H * (0.0085 if self.format == "long" else 0.0062))

        def place(name: str):
            thing = live.get(name)
            if thing is not None and thing.kind in BODIES:
                box = self.body_box(thing, mark.born + 0.5)
                return box, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            return None, self._slot(mark.shot, name, mark.line.lane)

        if mark.kind == "arrow":
            box_a, a = place(mark.line.who)
            box_b, b = place(mark.line.target)
            a, b = _edge(box_a, a, b, 18), _edge(box_b, b, a, 24)
            bend = 0.16 if mark.order % 2 else -0.16
            mark.strokes = ink.arrow_strokes(a, b, width, mark.color, bend)
        else:
            box, _ = place(mark.line.who)
            if box is not None:
                mark.strokes = ink.mark_strokes(mark.line.verb, box, width, mark.color, seed=mark.order)

    # --- where things are at a given time --------------------------------------------------

    def position(self, actor: Thing, t: float) -> Feet:
        """An actor's feet, on the page."""
        x, y = actor.home
        for start, duration, a, b in sorted(actor.moves, key=lambda m: m[0]):
            if t >= start + duration:
                x, y = b
            elif t >= start:
                k = ink.ease((t - start) / duration)
                return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
            else:
                break
        return x, y

    def walking(self, actor: Thing, t: float) -> tuple[float, str] | None:
        """While the actor is walking: how far it has gone (in figure units) and how (walk, run...)."""
        for start, duration, a, b in actor.moves:
            if duration and start <= t < start + duration:
                distance = math.hypot(b[0] - a[0], b[1] - a[1]) * ink.ease((t - start) / duration)
                gait = next((g for s, e, g in actor.gaits if s <= t < e), "walk")
                return distance / self._unit(actor), gait
        return None

    def acting(self, actor: Thing, t: float) -> tuple[float, str] | None:
        for start, name in actor.acts:
            if start <= t < start + ink.moves()[name]["seconds"]:
                return start, name
        return None

    def holding(self, actor: Thing, t: float) -> bool:
        return any(h[2] is actor and h[0] <= t and (h[1] is None or t < h[1])
                   for x in self.things for h in x.held)

    def figure(self, actor: Thing, t: float) -> tuple[ink.Pose, str, float, tuple[float, float]]:
        """The actor at time t: pose, facing, pixels per figure unit, and where its hip is on the page.

        Walks and runs come from recorded strides matched to the distance
        covered; recorded moves blend in and out of the pose; the lowest point
        of the body (feet, or hands in a cartwheel) rests on the ground unless
        the move is in the air.
        """
        pose = self.pose(actor, t)
        air = dx = 0.0
        walk = self.walking(actor, t)
        if walk is not None:
            stride, air = ink.gait_frame(walk[1], walk[0])
            if self.holding(actor, t):        # carrying: legs walk, arms keep holding
                stride = ink.Pose(stride.lean * 0.5, stride.tilt, pose.back_arm, pose.front_arm,
                                  stride.back_leg, stride.front_leg)
            pose = stride
        else:
            act = self.acting(actor, t)
            if act is not None:
                start, name = act
                length = ink.moves()[name]["seconds"]
                recorded, dx, air = ink.move_frame(name, t - start)
                weight = max(0.0, min(1.0, (t - start) / 0.15, (start + length - t) / 0.2))
                pose = pose.blend(recorded, weight)
                air *= weight
        facing = self._facing(actor, t)
        k = self._unit(actor)
        fx, fy = self.position(actor, t)
        sign = 1 if facing == "right" else -1
        return pose, facing, k, (fx + dx * k * sign, fy - (ink.lowest(pose) + air) * k)

    def pose(self, actor: Thing, t: float) -> ink.Pose:
        current = ink.Pose.named("stand")
        for start, a, b, name in actor.poses:
            if t < start:
                break
            current = a.blend(b, ink.spring(min(1.0, (t - start) / POSE_TIME), 0.5, 1.6))
            if name == "wave":
                wave = math.sin((t - start) * 2 * math.pi * 2.2) * 22
                current = ink.Pose(current.lean, current.tilt, current.back_arm,
                                   (current.front_arm[0], current.front_arm[1] + wave),
                                   current.back_leg, current.front_leg)
        return current

    def world_box(self, thing: Thing, t: float) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) on the page."""
        _, _, w, h = thing.box
        if thing.kind == "actor":
            fx, fy = self.position(thing, t)
            return fx - w / 2, fy - h, fx + w / 2, fy
        cx, cy = self.centre(thing, t)
        return cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2

    def body_box(self, thing: Thing, t: float) -> tuple[float, float, float, float]:
        """The part of the page the drawing itself covers: what hands reach for and circles go round."""
        x0, y0, x1, y1 = self.world_box(thing, t)
        if thing.kind == "photo" and thing.line.size != "full":     # the picture, inside its border
            mx, my = (x1 - x0) * 0.1, (y1 - y0) * 0.12
            return x0 + mx, y0 + my, x1 - mx, y1 - my
        if thing.kind != "prop":
            return x0, y0, x1, y1
        label_h = self.sizes["text"]["small"] * 1.1 * thing.scale if thing.line.text else 0
        side = max(x1 - x0, y1 - y0 - label_h)
        left = (x0 + x1) / 2 - side / 2
        fx0, fy0, fx1, fy1 = ink.ink_prop(self._prop_svg(thing)).content
        _, _, vw, vh = ink.ink_prop(self._prop_svg(thing)).view
        k = side / max(vw, vh)
        w, h = vw * k, vh * k
        left = (x0 + x1) / 2 - w / 2
        return left + fx0 * w, y0 + fy0 * h, left + fx1 * w, y0 + fy1 * h

    # --- the camera ----------------------------------------------------------------------

    def _camera_plan(self) -> list[tuple[float, float, tuple, tuple, int]]:
        """(time, duration, from, to, shot) camera moves, in order; a view is (centre x, centre y, zoom).

        The camera frames what is on stage: when something appears, leaves or
        walks off, it eases to keep the action filling the frame (within the
        zoom range of the shot's camera), unless the plan says where to look.
        """
        keys = []
        for i, sh in enumerate(self.shots):
            begin, duration = self.pan_start(i)
            if not self.compositions[i].single:
                ox, oy = self.origins[i]
                # A comparison is one designed frame: both lanes and the
                # divider remain visible instead of becoming camera targets.
                keys.append((begin, duration, (ox + self.W / 2, oy + self.H / 2, 1.0), i))
                continue
            lo, hi = ZOOM.get(sh["camera"], ZOOM["wide"])
            end = self.shot_end(i)
            looks = sorted((t, name) for t, name, shot in self.looks if shot == i)
            moments = {self.starts[i]}
            for x in self.things:
                if any(shot == i for shot, _ in x.spans):
                    moments.add(x.born)
                    if x.gone is not None:
                        moments.add(x.gone + ERASE_TIME)
                    moments.update(t + d for t, d, _a, _b in x.moves if t + d < end)
            moments = sorted(m for m in moments if self.starts[i] <= m < end - 0.4)
            last = None
            last_t = None
            looking = False
            for t in moments:
                due = [x for x in looks if x[0] <= t]
                if due:
                    looking = due[-1][1] is not None
                if looking:
                    continue
                first = t == self.starts[i]
                # a calm camera: at most one reframing every 1.6 s, unless something would be drawn out of view
                outside = last is not None and self._drawn_outside(last, i, t)
                if not first and not outside and last_t is not None and t - last_t < 3.0:
                    continue
                view = self._frame(i, t, lo, hi)
                if last and abs(view[0] - last[0]) < self.W * 0.12 and abs(view[1] - last[1]) < self.H * 0.12 \
                        and abs(view[2] - last[2]) < 0.12:
                    continue
                keys.append((begin if first else max(self.starts[i], t - 0.25), duration if first else 1.3, view, i))
                last, last_t = view, t
            for t, name in looks:
                if name is None:
                    keys.append((t, LOOK_TIME, self._frame(i, t, lo, hi), i))
                    continue
                thing = next((x for x in self.things if x.id == name and x.shot == i), None)
                if thing is None:
                    continue
                x0, y0, x1, y1 = self.body_box(thing, t + 0.5)
                zoom = min(1.7, max(1.15, min(0.55 * self.H / max(y1 - y0, 1), 0.5 * self.W / max(x1 - x0, 1))))
                keys.append((t, LOOK_TIME, ((x0 + x1) / 2, (y0 + y1) / 2 + self._caption_lift(zoom), zoom), i))
        keys.sort(key=lambda k: k[0])
        plan = []
        state = keys[0][2] if keys else (self.W / 2, self.H / 2, 1.0)
        for t, duration, target, shot in keys:
            plan.append((t, duration, state, target, shot))
            state = target
        return plan

    def _drawn_outside(self, view: tuple[float, float, float], shot: int, t: float) -> bool:
        """Whether something that appears at t would be drawn (partly) outside the view."""
        cx, cy, zoom = view
        lay = self.layout
        top = cy + (lay.stage_top * self.H - self.H / 2) / zoom
        bottom = cy + (lay.stage_bottom * self.H - self.H / 2) / zoom
        left, right = cx - self.W / 2 / zoom, cx + self.W / 2 / zoom
        for x in self.things:
            if x.kind in BODIES and abs(x.born - t) < 0.06 and self._shot_of(x, t) == shot:
                x0, y0, x1, y1 = self.world_box(x, t + 0.6)
                if x0 < left or x1 > right or y0 < top or y1 > bottom:
                    return True
        return False

    @staticmethod
    def _shot_of(thing: Thing, t: float) -> int:
        """The shot a thing belongs to at time t (actors walk from shot to shot)."""
        current = thing.spans[0][0] if thing.spans else thing.shot
        for shot, since in thing.spans:
            if t >= since:
                current = shot
        return current

    def _caption_lift(self, zoom: float) -> float:
        """On a Short, the middle of the stage sits above the captions: shift the view down to match."""
        lay = self.layout
        middle = (lay.stage_top + lay.stage_bottom) / 2
        return (0.5 - middle) * self.H / zoom

    def _frame(self, shot: int, t: float, lo: float, hi: float) -> tuple[float, float, float]:
        """A view that holds everything on stage at time t, with room around it."""
        ox, oy = self.origins[shot]
        boxes = []
        # look a little ahead, so the camera makes room before things arrive instead of chasing them
        ahead = min(t + 2.0, self.shot_end(shot) - 0.05)
        for x in self.things:
            if x.kind not in BODIES or x.born > ahead or self._shot_of(x, ahead) != shot:
                continue
            if x.gone is not None and x.gone <= t + 0.3:
                continue
            boxes.append(self.world_box(x, ahead))
        if not boxes:
            return ox + self.W / 2, oy + self.H / 2 + self._caption_lift(1.0), 1.0
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        pad_x, pad_y = self.W * 0.09, self.H * 0.08
        stage_h = self.H * (self.layout.stage_bottom - self.layout.stage_top)
        zoom = min(self.W / (x1 - x0 + 2 * pad_x), stage_h / (y1 - y0 + 2 * pad_y), hi)
        zoom = max(lo, zoom)
        cx = min(max((x0 + x1) / 2, ox + self.W * 0.3), ox + self.W * 0.7)
        cy = (y0 + y1) / 2 + self._caption_lift(zoom)
        return cx, cy, zoom

    def camera(self, t: float) -> tuple[float, float, float]:
        cx, cy, zoom = self.W / 2, self.H / 2, 1.0
        for start, duration, a, b, _shot in self.keyframes:
            if t < start:
                break
            k = 1.0 if duration <= 0 else ink.ease((t - start) / duration)
            cx, cy, zoom = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)
        shot = self.shot_at(t)
        if not self.compositions[shot].single:
            return cx, cy, zoom
        p = min(1.0, max(0.0, (t - self.starts[shot]) / max(0.5, self.shot_end(shot) - self.starts[shot])))
        if self.shots[shot]["camera"] == "push":
            zoom *= 1 + 0.07 * ink.ease(p)
        elif self.shots[shot]["camera"] == "drift":
            cx += (p - 0.5) * 0.05 * self.W
        return cx, cy, zoom

    # --- drawing -------------------------------------------------------------------------

    def _cached(self, key: tuple, make):
        image = self._cache.get(key)
        if image is None:
            image = make()
            self._cache[key] = image
            self._cache_bytes += _weight(image)
            while self._cache_bytes > self.cache_limit and len(self._cache) > 1:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= _weight(old)
        else:
            self._cache.move_to_end(key)
        return image

    def _progress(self, thing: Thing, t: float) -> float:
        """How much of it is drawn: rises from 0 to 1 when it appears, falls back when erased."""
        if thing.kind in ("text", "tag"):
            need = min(1.2, max(0.35, 0.045 * len(thing.line.text)))
        else:
            need = DRAW_TIME.get(thing.line.verb if thing.kind in ("arrow", "mark") else thing.kind, 0.5)
        p = min(1.0, max(0.0, (t - thing.born) / need))
        if thing.gone is not None and t >= thing.gone:
            p = min(p, max(0.0, 1 - (t - thing.gone) / ERASE_TIME))
        return p

    def _motion(self, thing: Thing, t: float) -> tuple[float, float, float, float]:
        """(dx, dy, scale, angle) from shakes, bounces and the like."""
        dx = dy = angle = 0.0
        scale = 1.0
        unit = self.H / 1080
        for start, kind in thing.motions:
            a = t - start
            if a < 0:
                continue
            if kind == "shake" and a < 0.7:
                dx += 12 * unit * math.sin(a * 2 * math.pi * 9) * (1 - a / 0.7)
            elif kind == "bounce" and a < 1.3:
                dy -= abs(math.sin(a * 2 * math.pi * 1.5)) * 34 * unit * (1 - a / 1.3)
            elif kind == "pulse" and a < 1.6:
                scale *= 1 + 0.08 * math.sin(a * 2 * math.pi * 1.25) * (1 - a / 1.6)
            elif kind == "spin":
                angle -= 360 * ink.ease_out(min(1.0, a / 1.6))
            elif kind == "grow":
                scale *= 1 + 0.32 * ink.spring(min(1.0, a / 0.7), 0.4, 1.4)
            elif kind == "shrink":
                scale *= 1 - 0.3 * ink.spring(min(1.0, a / 0.7), 0.4, 1.4)
        return dx, dy, scale, angle

    def _sprite(self, thing: Thing, t: float, zq: float) -> tuple[Image.Image, tuple[float, float]] | None:
        """The thing's picture at time t, drawn for zoom zq, and the page point of its top left corner."""
        p = self._progress(thing, t)
        if p <= 0:
            return None
        if self.look.pop and thing.kind in looks.POPPED:
            p = 1.0      # cut-out pieces are whole from the start: they pop on instead
        pq = round(p, 2)
        if thing.kind == "prop":
            _, _, w, h = thing.box
            cx, cy = self.centre(thing, t)
            label_h = self.sizes["text"]["small"] * 1.1 * thing.scale if thing.line.text else 0
            box = max(w, h - label_h) * zq
            svg = self._prop_svg(thing)

            def make() -> Image.Image:
                image = looks.flat_prop(svg, int(box)) if self.look.flat_props else ink.prop_image(svg, int(box), p)
                if not thing.line.text or p <= 0.6:
                    return image
                size = int(self.sizes["text"]["small"] * thing.scale * zq * 0.85)
                label = ink.render(ink.text_svg([thing.line.text], size, ink.INK, (p - 0.6) / 0.4))
                if self.look.cartoon:            # a label reads over a busy place only on a card
                    label = looks.cutout(label, max(5, round(self.H / 110 * zq)))
                out = Image.new("RGBA", (max(image.width, label.width), image.height + label.height), (0, 0, 0, 0))
                out.alpha_composite(image, ((out.width - image.width) // 2, 0))
                out.alpha_composite(label, ((out.width - label.width) // 2, image.height))
                return out
            image = self._cached(("p", thing.id, thing.order, pq, zq), make)
            return image, (cx - image.width / zq / 2, cy - h / 2)
        if thing.kind == "photo":
            cx, cy = self.centre(thing, t)
            _, _, w, h = thing.box
            found = self.photos.get(thing.line.text) or {}
            full = thing.line.size == "full"
            if full:
                pw, ph = w, h
            else:
                pw = thing.size_px * thing.scale
                aspect = (found.get("width") or 4) / (found.get("height") or 3)
                ph = min(pw / aspect, h)
            image = photos.photo_image(found.get("path"), thing.line.text, int(pw * zq), int(ph * zq), p,
                                       t - thing.born, thing.order, full)
            return image, (cx - image.width / zq / 2, cy - image.height / zq / 2)
        if thing.kind == "data":
            card = self.data_cards.get(thing.line.who)
            if card is None:
                return None
            cx, cy = self.centre(thing, t)
            _, _, w, h = thing.box
            image = self._cached(("data", thing.id, pq, zq), lambda: data_graphics.render(
                card, max(1, round(w * zq)), max(1, round(h * zq)), progress=p,
                font_path=str(self.font_path)))
            return image, (cx - image.width / zq / 2, cy - image.height / zq / 2)
        if thing.kind in ("flag", "map", "timeline"):
            cx, cy = self.centre(thing, t)
            _, _, w, h = thing.box
            width = max(40, int(w * zq))
            if thing.kind == "flag":
                image = self._cached(("f", thing.id, thing.order, pq, zq),
                                     lambda: geo.flag_image(thing.line.who, int(width / 1.1), p))
            elif thing.kind == "map":
                first = {thing.line.extra[0]: thing.line.color} if thing.line.color else None
                image = self._cached(("map", thing.id, thing.order, pq, zq),
                                     lambda: geo.map_image(thing.line.extra, width, p, first))
            else:
                image = self._cached(("tl", thing.id, thing.order, pq, zq),
                                     lambda: geo.timeline_image(thing.line.extra, width, p, thing.color))
            return image, (cx - image.width / zq / 2, cy - image.height / zq / 2)
        if thing.kind == "tag":
            owner = thing.owner
            fx, fy = self.position(owner, t)
            size = int(self.sizes["text"]["small"] * 0.8 * zq)
            image = self._cached(("tag", thing.id, pq, zq), lambda: ink.render(
                ink.text_svg([thing.line.text], size, ink.INK, p, underline=ink.COLORS["warm"])))
            return image, (fx - image.width / zq / 2, fy + 6)
        if thing.kind == "text":
            cx, cy = self.centre(thing, t)
            size = int(thing.size_px * thing.scale * zq)
            underline = (ink.INK if thing.color != ink.INK else ink.COLORS["warm"]) if thing.line.big else None
            image = self._cached(("t", thing.id, thing.order, pq, zq), lambda: ink.render(
                ink.text_svg(thing.lines, size, thing.color, p, underline)))
            return image, (cx - image.width / zq / 2, cy - image.height / zq / 2)
        if thing.kind in ("arrow", "mark"):
            if not thing.strokes:
                return None
            x0, y0, x1, y1 = ink.strokes_bounds(thing.strokes)
            image = self._cached(("m", thing.id, pq, zq), lambda: ink.render(ink.svg_doc(
                ink.strokes_svg(thing.strokes, p), (x1 - x0) * zq, (y1 - y0) * zq, (x0, y0, x1 - x0, y1 - y0))))
            return image, (x0, y0)
        # an actor
        pose, facing, k, hip = self.figure(thing, t)
        expression = "neutral"
        for at, face in thing.faces:
            if at <= t:
                expression = face
        mouth = 0
        if thing.line.talk and self.mouths is not None and p >= 1:
            mouth = int(self.mouths[min(int(t * FPS), len(self.mouths) - 1)])
        blink = ((t + thing.order * 1.3) % 3.9) < 0.12
        key = ("a", thing.id, pose.key(), expression, mouth, blink, facing, pq, zq, thing.line.who)
        variant = thing.line.who if thing.line.who in art.CHARACTERS else "person"
        if self.look.cartoon:
            image = self._cached(key, lambda: ink.render(cartoon.figure_svg(
                variant, pose, expression, mouth, blink, facing, k * zq, look=thing.look or None)))
        else:
            image = self._cached(key, lambda: ink.render(ink.figure_svg(
                variant, pose, expression, mouth, blink, facing, p, k * zq, look=thing.look or None)))
        return image, (hip[0] + ink.BOX[0] * k, hip[1] + ink.BOX[1] * k)

    def _resized(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        key = (id(image), size)
        out = self._sized.get(key)
        if out is None or out[0] is not image:
            out = (image, image.resize(size, Image.BILINEAR))
            self._sized[key] = out
            self._sized_bytes += _weight(out[1])
            while self._sized_bytes > self.cache_limit // 4 and len(self._sized) > 1:
                _, (_, old) = self._sized.popitem(last=False)
                self._sized_bytes -= _weight(old)
        return out[1]

    # --- cartoon places ---------------------------------------------------------------------

    def _place_name(self, shot: int) -> str:
        return self.places[shot] if shot < len(self.places) else (self.places[-1] if self.places else "idea")

    def _ground(self, shot: int) -> float | None:
        """Where the feet of the shot's actors are on the page, to put the place's floor under them."""
        if shot not in self._place_ground:
            t = self.starts[shot] + 0.4
            feet = [self.position(x, t)[1] for x in self.things if x.kind == "actor" and x.born <= t + 1.5
                    and (x.gone is None or x.gone > t)]
            self._place_ground[shot] = max(feet) if feet else None
        return self._place_ground[shot]

    def _crop(self, shot: int, cx: float, cy: float, zoom: float) -> np.ndarray:
        name = self._place_name(shot)
        pw, ph = self._place_size
        picture = self._place_pictures.get(name)
        if picture is None:
            svg = self.place_svgs.get(name)
            picture = np.asarray(cartoon.place_image(name if svg or name in cartoon.PLACES else "idea",
                                                     self.W, self.H, svg).convert("RGBA"))
            self._place_pictures[name] = picture
            while len(self._place_pictures) > 3:
                self._place_pictures.popitem(last=False)
        else:
            self._place_pictures.move_to_end(name)
        run = shot
        while run > 0 and self._place_name(run - 1) == name:
            run -= 1
        if run not in self._place_origin:
            self._place_origin[run] = self.camera(self.starts[run] + 0.01)[0]
        dx = (cx - self._place_origin[run]) * zoom * 0.3                  # parallax: the place moves slower
        x0 = int(min(max((pw - self.W) / 2 + dx, 0), pw - self.W))
        ground = self._ground(shot)
        screen = (ground - cy) * zoom + self.H / 2 if ground is not None else self.H * 0.8
        floor_at = min(max(screen - self.H * 0.07, self.H * 0.45), self.H * 0.85)
        y0 = int(min(max(ph * cartoon.FLOOR - floor_at, 0), ph - self.H))
        return picture[y0:y0 + self.H, x0:x0 + self.W]

    def _place(self, tq: float, t: float, cx: float, cy: float, zoom: float) -> Image.Image:
        """The drawn place behind this moment; a short dissolve when a gliding shot changes place."""
        shot = self.shot_at(t)
        out = self._crop(shot, cx, cy, zoom)
        age = t - self.starts[shot]
        if shot > 0 and age < 0.45 and not self.shots[shot].get("cut") and \
                self._place_name(shot - 1) != self._place_name(shot):
            before = self._crop(shot - 1, cx, cy, zoom)
            k = ink.ease(age / 0.45)
            out = (before.astype(np.float32) * (1 - k) + out.astype(np.float32) * k).astype(np.uint8)
        return Image.fromarray(np.ascontiguousarray(out), "RGBA")

    def _dress(self, kind: str, image: Image.Image, zq: float) -> Image.Image:
        """The sprite as the look wants it: cut out of card (paper), or in white lines (blueprint)."""
        if self.look.line_profile in ("uniform", "rough") and kind not in looks.PASTED:
            how = self.look.line_profile
        elif self.look.recolour and kind not in looks.PASTED:
            how = "bp"
        elif self.look.cartoon and kind in ("text", "tag", "timeline"):
            how = "card"                 # words on a card, so they read over a busy place
        elif self.look.cartoon and kind == "map":
            how = "board"                # a map on a board: its seas must not show the place through
        elif self.look.shadow and kind not in looks.NO_EDGE:
            how = "cut"
        else:
            return image
        key = id(image)
        out = self._dressed.get(key)
        if out is None or out[0] is not image:
            if how in ("uniform", "rough"):
                dressed = looks.profiled_sprite(image, self.look)
            elif how == "bp":
                dressed = looks.blueprint_sprite(image)
            elif how == "card":
                dressed = looks.cutout(image, max(6, round(self.H / 80 * zq)))
            elif how == "board":
                dressed = looks.board(image, max(6, round(self.H / 120 * zq)))
            else:
                dressed = looks.cutout(image, max(2, round(self.H / 270 * zq)))
            out = (image, dressed)
            self._dressed[key] = out
            self._dressed_bytes += _weight(dressed) + _weight(image)
            while self._dressed_bytes > self.cache_limit // 4 and len(self._dressed) > 1:
                _, (first, old) = self._dressed.popitem(last=False)
                self._dressed_bytes -= _weight(old) + _weight(first)
        else:
            self._dressed.move_to_end(key)
        return out[1]

    def frame_rgb(self, t: float) -> np.ndarray:
        """The picture at time t, without captions, as an RGB array."""
        import cv2

        return cv2.cvtColor(self._compose(t), cv2.COLOR_RGBA2RGB)

    def _compose(self, t: float) -> np.ndarray:
        """The picture at time t as RGBA: paper, then the boiling layer of ink."""
        import cv2

        tq = math.floor(t * ART_FPS + 1e-6) / ART_FPS
        cx, cy, zoom = self.camera(t)
        active_shot = self.shot_at(t)
        active_geo = self.compositions[active_shot]
        key = (tq, round(cx, 1), round(cy, 1), round(zoom, 4))
        if self._last and self._last[0] == key:
            return self._last[1]
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        # a blueprint recolours its drawings; photos, flags and maps are pasted on a layer of their own
        pasted = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0)) if self.look.recolour else None
        view = (cx - self.W / 2 / zoom, cy - self.H / 2 / zoom, cx + self.W / 2 / zoom, cy + self.H / 2 / zoom)
        zq = max(0.5, round(zoom * 10) / 10)
        rank = {"photo": -1, "prop": 0, "text": 0, "flag": 0, "map": 0, "timeline": 0, "data": 0,
                "actor": 1, "arrow": 2,
                "mark": 2, "tag": 2}
        for thing in sorted(self.things, key=lambda x: (rank[x.kind], x.order)):
            if tq < thing.born or (thing.gone is not None and tq > thing.gone + ERASE_TIME):
                continue
            if thing.kind not in ("arrow", "mark", "tag"):
                x0, y0, x1, y1 = self.world_box(thing, tq)
                pad = max(x1 - x0, y1 - y0)
                if x1 + pad < view[0] or x0 - pad > view[2] or y1 + pad < view[1] or y0 - pad > view[3]:
                    continue
            made = self._sprite(thing, tq, zq)
            if made is None:
                continue
            image, (ax, ay) = made
            dx, dy, scale, angle = self._motion(thing, tq)
            centre_x, centre_y = ax + image.width / zq / 2, ay + image.height / zq / 2
            if self.look.pop and thing.kind in looks.POPPED:
                scale *= looks.pop_scale(tq - thing.born, None if thing.gone is None else tq - thing.gone, ERASE_TIME)
                if scale <= 0.01:
                    continue
            image = self._dress(thing.kind, image, zq)      # keeps the centre: any margin is added all round
            factor = zoom / zq * scale
            if abs(factor - 1) > 0.004:
                image = self._resized(image, (max(1, round(image.width * factor)),
                                              max(1, round(image.height * factor))))
            if angle:
                image = image.rotate(angle, resample=Image.BILINEAR, expand=True)
            sx = (centre_x + dx - cx) * zoom + self.W / 2 - image.width / 2
            sy = (centre_y + dy - cy) * zoom + self.H / 2 - image.height / 2
            target = pasted if pasted is not None and thing.kind in looks.PASTED else layer
            lane = thing.line.lane or (thing.owner.line.lane if thing.owner is not None else "")
            bounds = None
            if not active_geo.single and lane and self._shot_of(thing, tq) == active_shot:
                lane_view = active_geo.viewport(lane)
                bounds = (lane_view.x0, lane_view.y0, lane_view.x1, lane_view.y1)
            animate._composite_clipped(target, image, round(sx), round(sy), bounds)
        for at, number, heading in self.chapters:
            if at <= t < at + BANNER:
                self._banner(layer, number, heading, tq - at)
        ground = self._place(tq, t, cx, cy, zoom) if self.look.cartoon else self.paper
        if pasted is not None and pasted.getbbox():
            ground = Image.alpha_composite(ground, pasted)
        if self.look.shadow:
            under = looks.shadow(layer, self.H)
            if under is not None:
                ground = Image.alpha_composite(ground, under)
        box = layer.getbbox()
        if box is None:
            out = np.asarray(ground)
        elif not self.boil:
            out = np.asarray(Image.alpha_composite(ground, layer))
        else:
            # boil only where there is ink: warp that part of the layer by this moment's noise field
            mx, my = self.boil[int(tq * self.boil_fps) % len(self.boil)]
            pad = 8
            x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
            x1, y1 = min(self.W, box[2] + pad), min(self.H, box[3] + pad)
            warped = np.zeros((self.H, self.W, 4), np.uint8)
            warped[y0:y1, x0:x1] = cv2.remap(np.asarray(layer), mx[y0:y1, x0:x1], my[y0:y1, x0:x1],
                                             interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                             borderValue=(0, 0, 0, 0))
            out = np.asarray(Image.alpha_composite(ground, Image.fromarray(warped, "RGBA")))
        if not active_geo.single and active_geo.divider != "none":
            divided = Image.fromarray(out, "RGBA")
            draw = ImageDraw.Draw(divided)
            width = 8 if active_geo.divider == "line" else 4
            if self.format == "long":
                x = round((active_geo.lanes[0].x1 + active_geo.lanes[1].x0) / 2)
                draw.line((x, 0, x, round(active_geo.caption_top)), fill=ink.INK, width=width)
                centre = (x, active_geo.caption_top / 2)
            else:
                y = round((active_geo.lanes[0].y1 + active_geo.lanes[1].y0) / 2)
                draw.line((0, y, self.W, y), fill=ink.INK, width=width)
                centre = (self.W / 2, y)
            if active_geo.divider == "vs":
                font = ImageFont.truetype(str(self.font_path), max(28, round(self.H * 0.035)))
                pad = round(self.H * 0.012)
                box = draw.textbbox(centre, "VS", font=font, anchor="mm")
                draw.rounded_rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad),
                                       radius=pad, fill=self.paper.getpixel((0, 0)))
                draw.text(centre, "VS", font=font, fill=ink.INK, anchor="mm")
            out = np.asarray(divided)
        self._last = (key, out)
        return out

    def _banner(self, layer: Image.Image, number: int, heading: str, age: float) -> None:
        """'Chapter 2' and its heading, handwritten at the top left, then rubbed out."""
        p = min(1.0, age / 0.9)
        if age > BANNER - ERASE_TIME:
            p = max(0.0, (BANNER - age) / ERASE_TIME)
        if p <= 0:
            return
        pq = round(p, 2)
        small = self._cached(("bn", number, pq), lambda: ink.render(
            ink.text_svg([f"Chapter {number}"], 34, ink.COLORS["warm"], min(1.0, p * 2))))
        big = self._cached(("bh", heading, pq), lambda: ink.render(
            ink.text_svg([heading], 52, ink.INK, p, underline=ink.INK)))
        animate._composite_clipped(layer, small, 40, 20)
        animate._composite_clipped(layer, big, 40, 20 + small.height - 6)

    def frame(self, t: float) -> Image.Image:
        return Image.fromarray(self.frame_rgb(t), "RGB").convert("RGBA")

    # --- sound ---------------------------------------------------------------------------

    def sound_events(self) -> list[tuple[float, str]]:
        """(time, kind) for every sound the picture makes: the pen, the camera, steps, landings, slides."""
        events: list[tuple[float, str]] = []
        for i in range(1, len(self.shots)):
            if not self.shots[i].get("cut"):
                events.append((self.pan_start(i)[0], "pan"))
        draws = {"actor": "scribble", "prop": "scribble", "text": "scribble", "arrow": "scribble",
                 "circle": "scribble", "underline": "scribble", "cross": "cross", "tick": "tick", "photo": "glide"}
        for x in self.things:
            verb = x.line.verb if x.kind in ("arrow", "mark") else x.kind
            events.append((x.born, draws.get(verb, "scribble")))
            if x.gone is not None:
                events.append((x.gone, "erase"))
            for t, motion in x.motions:
                events.append((t, {"spin": "swish"}.get(motion, motion)))
            for start, end, _actor in x.held:
                events.append((start, "lift"))
                if end is not None:
                    events.append((end + FALL_TIME, "thud"))
            if x.kind != "actor":
                for start, duration, a, b in x.moves:
                    if duration and math.hypot(b[0] - a[0], b[1] - a[1]) > 40:
                        events.append((start, "glide"))
                continue
            for start, _a, _b, name in x.poses:
                if name in ("push", "pull"):
                    events.append((start + 0.1, "creak"))
            for start, name in x.acts:
                events += [(start + at, kind) for at, kind in ink.move_cues(name)]
            for start, end, gait in x.gaits:          # a step for every footfall of the recorded stride
                half = ink.moves()[gait]["stride"] / 2
                last = 0
                t = start
                while t < end:
                    walk = self.walking(x, t)
                    if walk is not None and int(walk[0] / max(half, 1)) > last:
                        last = int(walk[0] / max(half, 1))
                        events.append((t, "run_step" if gait == "run" else "step"))
                    t += 1 / 48
        kept: list[tuple[float, str]] = []
        for t, kind in sorted((t, kind) for t, kind in events if 0 <= t < self.duration):
            if kept and kept[-1][1] == kind and t - kept[-1][0] < 0.15:
                continue                          # things drawn together share one scratch of the pen
            kept.append((t, kind))
        return kept

    def bgr(self, t: float, observer: animate.visual_qa.FrameObserver | None = None) -> np.ndarray:
        import cv2

        composed = self._compose(t)
        if observer is not None:
            observer.observe(t, composed[..., :3])       # intentionally before captions
        frame = cv2.cvtColor(composed, cv2.COLOR_RGBA2BGR)
        if self.captions is not None:
            self.captions.draw(frame, t)
        return frame


def _weight(image) -> int:
    """Bytes a cached picture takes."""
    return image.width * image.height * 4 if isinstance(image, Image.Image) else 1024


def _edge(box, centre: tuple[float, float], toward: tuple[float, float], gap: float) -> tuple[float, float]:
    """Where a line from `centre` to `toward` leaves the box, plus a gap."""
    if box is None:
        return centre
    x0, y0, x1, y1 = box
    dx, dy = toward[0] - centre[0], toward[1] - centre[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return centre
    hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
    k = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    d = math.hypot(dx, dy)
    return centre[0] + dx * k + dx / d * gap, centre[1] + dy * k + dy / d * gap
