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
from PIL import Image

from .. import captions as captions_mod
from ..settings import Settings, hex_to_rgb, resolve_font
from . import animate, art, beats, ink

FPS = 24
ART_FPS = 12
BOIL_FPS = 8
DRAW_TIME = {"actor": 0.7, "prop": 0.8, "arrow": 0.45, "circle": 0.55, "underline": 0.35, "cross": 0.35,
             "tick": 0.3}
ERASE_TIME = 0.3
POSE_TIME = 0.45
JUMP_TIME = 0.7
MOVE_SPEED = 0.5             # of the frame width per second, walking
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


def boil_fields(width: int, height: int, count: int = 3, seed: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    """Smooth random displacement maps: drawn lines wobble a little differently in each."""
    rng = np.random.default_rng(seed)
    amp = 2.2 * min(width, height) / 1080
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
        self.paper = Image.fromarray(paper(self.W, self.H), "RGB").convert("RGBA")
        self.boil = boil_fields(self.W, self.H)
        self._cache: OrderedDict = OrderedDict()
        self._sized: OrderedDict = OrderedDict()      # sprites resized for the current zoom
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
        duration = PAGE_TIME if self.shots[i].get("page") else PAN_TIME
        arrive = self.arrivals[i] if i < len(self.arrivals) else self.starts[i]
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

    def _slot(self, shot: int, slot: str) -> tuple[float, float]:
        slots = SLOTS[self.format]
        fx, fy = slots.get(slot or "center", slots["center"])
        ox, oy = self.origins[shot]
        return ox + fx * self.W, oy + fy * self.H

    def _build(self) -> None:
        alive: dict[str, Thing] = {}       # what is still on the page from earlier shots, by id
        order = 0
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
                    kind = {"actor": "actor", "draw": "prop", "write": "text"}[line.verb]
                    thing = Thing(line.id, kind, i, t, line, order, spans=[(i, t)])
                    order += 1
                    if kind == "text":
                        thing.color = ink.COLORS.get(line.color or "ink", ink.INK)
                    if kind == "actor" and line.expression:
                        thing.faces.append((t, line.expression))
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
                    target.requests.append((t, "move", line.slot, line.target, i))
            self._layout(i, [x for x in created if x.kind in ("actor", "prop", "text")], carried)
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
            cx, cy = self._slot(shot, x.line.slot or ("left" if x.kind == "actor" else "center"))
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
        placed = animate.separate(items, self.W * animate.MARGIN, self.W * (1 - animate.MARGIN),
                                  self.H * lay.stage_top, self.H * lay.stage_bottom, gap=24, pairs=together)
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
                x.shot = shot
                x.spans.append((shot, begin))
            else:
                x.scale = s
                x.box = (cx + ox, cy + oy, w, h)
                if x.kind == "actor":
                    x.home = (cx + ox, cy + oy + h / 2)
                    x.facing.append((x.born, self._face_centre(shot, x.home[0])))
                    first = x.line.pose if x.line.pose and x.line.pose not in ("hold", "push", "pull") else "stand"
                    pose = ink.Pose.named(first)
                    x.poses.append((x.born, pose, pose, first))

    def _face_centre(self, shot: int, x: float) -> str:
        return "right" if x <= self.origins[shot][0] + self.W / 2 + 4 else "left"

    def _prop_svg(self, thing: Thing) -> str:
        return self.props.get(thing.line.who) or art.placeholder_svg(thing.line.who)

    def _resolve_actor(self, actor: Thing, live: dict[str, Thing], shot: int) -> None:
        """Turn the actor's requests in this shot into walks and poses."""
        for t, kind, value, target_name, at_shot in sorted(actor.requests, key=lambda r: r[0]):
            if at_shot != shot:
                continue
            t = max(t, self._busy_until(actor))
            if kind == "move":
                here = self.position(actor, t)
                goal = live.get(target_name) if target_name else None
                if goal is not None and not value:      # walk up to it and stop beside it
                    x0, _y0, x1, _y1 = self.body_box(goal, t)
                    side = 1 if (x0 + x1) / 2 >= here[0] else -1
                    there = ((x0 if side > 0 else x1) - side * actor.box[2] * 0.7, here[1])
                else:
                    cx, cy = self._slot(shot, value)
                    there = (cx, cy + actor.box[3] / 2)
                self._walk(actor, t, here, there)
                continue
            target = live.get(target_name) if target_name else None
            begin = t
            if target is not None and value in ("hold", "push", "pull"):
                here = self.position(actor, t)
                side = 1 if target.box[0] >= here[0] else -1
                arm = 1.7 * ink.ARM * self._unit(actor)
                body = self.body_box(target, t)
                edge = body[0] if side > 0 else body[2]
                stand = edge - side * arm * 0.8
                if abs(stand - here[0]) > arm * 0.25:
                    begin = t + self._walk(actor, t, here, (stand, here[1]))
            if target is not None:
                side = "right" if target.box[0] >= self.position(actor, begin)[0] else "left"
                actor.facing.append((begin, side))
            if value == "keep":
                continue
            goal = ink.Pose.named(value)
            if target is not None and value in ink.REACH:
                goal = self._reach(actor, goal, value, target, begin)
            before = self.pose(actor, begin)
            actor.poses.append((begin, before, goal, value))
            if value == "jump":       # a jump lands back in the pose before it
                actor.poses.append((begin + JUMP_TIME, goal, before, "land"))
        actor.facing.sort(key=lambda f: f[0])

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
                there = self._slot(shot, slot)
            duration = max(0.4, math.hypot(there[0] - here[0], there[1] - here[1]) / (self.W * MOVE_SPEED * 1.6))
            thing.moves.append((max(t, self._busy_until(thing)), duration, here, there))
            here = there

    def centre(self, thing: Thing, t: float) -> tuple[float, float]:
        """Where a prop or a word is at time t (they can slide)."""
        x, y = thing.box[0], thing.box[1]
        for start, duration, a, b in thing.moves:
            if t >= start + duration:
                x, y = b
            elif t >= start:
                k = ink.ease((t - start) / duration)
                return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        return x, y

    def _busy_until(self, actor: Thing) -> float:
        return max((t + d for t, d, _a, _b in actor.moves), default=0.0)

    def _walk(self, actor: Thing, t: float, here: Feet, there: Feet) -> float:
        duration = max(0.5, math.hypot(there[0] - here[0], there[1] - here[1]) / (self.W * MOVE_SPEED))
        actor.moves.append((t, duration, here, there))
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
            if thing is not None and thing.kind in ("actor", "prop", "text"):
                box = self.body_box(thing, mark.born + 0.5)
                return box, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            return None, self._slot(mark.shot, name)

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

    def walking(self, actor: Thing, t: float) -> float | None:
        """Steps taken so far, while the actor is walking."""
        for start, duration, a, b in actor.moves:
            if start <= t < start + duration:
                distance = math.hypot(b[0] - a[0], b[1] - a[1]) * ink.ease((t - start) / duration)
                return distance / (ink.LEG * self._unit(actor) * 0.9)
        return None

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
                if not first and not outside and last_t is not None and t - last_t < 1.6:
                    continue
                view = self._frame(i, t, lo, hi)
                if last and abs(view[0] - last[0]) < self.W * 0.08 and abs(view[1] - last[1]) < self.H * 0.08 \
                        and abs(view[2] - last[2]) < 0.08:
                    continue
                keys.append((begin if first else max(self.starts[i], t - 0.25), duration if first else 1.0, view, i))
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
            if x.kind in ("actor", "prop", "text") and abs(x.born - t) < 0.06 and self._shot_of(x, t) == shot:
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
            if x.kind not in ("actor", "prop", "text") or x.born > ahead or self._shot_of(x, ahead) != shot:
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
            if len(self._cache) > 700:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return image

    def _progress(self, thing: Thing, t: float) -> float:
        """How much of it is drawn: rises from 0 to 1 when it appears, falls back when erased."""
        if thing.kind == "text":
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
        pq = round(p, 2)
        if thing.kind == "prop":
            _, _, w, h = thing.box
            cx, cy = self.centre(thing, t)
            label_h = self.sizes["text"]["small"] * 1.1 * thing.scale if thing.line.text else 0
            box = max(w, h - label_h) * zq
            svg = self._prop_svg(thing)

            def make() -> Image.Image:
                image = ink.prop_image(svg, int(box), p)
                if not thing.line.text or p <= 0.6:
                    return image
                size = int(self.sizes["text"]["small"] * thing.scale * zq * 0.85)
                label = ink.render(ink.text_svg([thing.line.text], size, ink.INK, (p - 0.6) / 0.4))
                out = Image.new("RGBA", (max(image.width, label.width), image.height + label.height), (0, 0, 0, 0))
                out.alpha_composite(image, ((out.width - image.width) // 2, 0))
                out.alpha_composite(label, ((out.width - label.width) // 2, image.height))
                return out
            image = self._cached(("p", thing.id, thing.order, pq, zq), make)
            return image, (cx - image.width / zq / 2, cy - h / 2)
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
        k = self._unit(thing) * zq
        pose = self.pose(thing, t)
        step = self.walking(thing, t)
        if step is not None:
            pose = ink.walk_pose(pose, step)
        facing = "right"
        for at, side in thing.facing:
            if at <= t:
                facing = side
        expression = "neutral"
        for at, face in thing.faces:
            if at <= t:
                expression = face
        mouth = 0
        if thing.line.talk and self.mouths is not None and p >= 1:
            mouth = int(self.mouths[min(int(t * FPS), len(self.mouths) - 1)])
        blink = ((t + thing.order * 1.3) % 3.9) < 0.12
        key = ("a", thing.id, pose.key(), expression, mouth, blink, facing, pq, zq, thing.line.who)
        image = self._cached(key, lambda: ink.render(ink.figure_svg(
            thing.line.who, pose, expression, mouth, blink, facing, p, k)))
        fx, fy = self.position(thing, t)
        lift = 0.0
        for start, _a, _b, name in thing.poses:
            if name == "jump" and start <= t < start + JUMP_TIME:
                q = (t - start) / JUMP_TIME
                lift = 4 * q * (1 - q) * thing.box[3] * 0.3
        if step is not None:
            lift += abs(math.sin(step * math.pi)) * 6 * self.H / 1080
        ax, ay = ink.figure_anchor(k)
        return image, (fx - ax / zq, fy - lift - ay / zq)

    def _resized(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        key = (id(image), size)
        out = self._sized.get(key)
        if out is None or out[0] is not image:
            out = (image, image.resize(size, Image.BILINEAR))
            self._sized[key] = out
            if len(self._sized) > 300:
                self._sized.popitem(last=False)
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
        key = (tq, round(cx, 1), round(cy, 1), round(zoom, 4))
        if self._last and self._last[0] == key:
            return self._last[1]
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        view = (cx - self.W / 2 / zoom, cy - self.H / 2 / zoom, cx + self.W / 2 / zoom, cy + self.H / 2 / zoom)
        zq = max(0.5, round(zoom * 10) / 10)
        rank = {"prop": 0, "text": 0, "actor": 1, "arrow": 2, "mark": 2}
        for thing in sorted(self.things, key=lambda x: (rank[x.kind], x.order)):
            if tq < thing.born or (thing.gone is not None and tq > thing.gone + ERASE_TIME):
                continue
            if thing.kind not in ("arrow", "mark"):
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
            factor = zoom / zq * scale
            if abs(factor - 1) > 0.004:
                image = self._resized(image, (max(1, round(image.width * factor)),
                                              max(1, round(image.height * factor))))
            if angle:
                image = image.rotate(angle, resample=Image.BILINEAR, expand=True)
            sx = (centre_x + dx - cx) * zoom + self.W / 2 - image.width / 2
            sy = (centre_y + dy - cy) * zoom + self.H / 2 - image.height / 2
            animate._composite_clipped(layer, image, round(sx), round(sy))
        for at, number, heading in self.chapters:
            if at <= t < at + BANNER:
                self._banner(layer, number, heading, tq - at)
        box = layer.getbbox()
        if box is None:
            out = np.asarray(self.paper)
        else:
            # boil only where there is ink: warp that part of the layer by this moment's noise field
            mx, my = self.boil[int(tq * BOIL_FPS) % len(self.boil)]
            pad = 8
            x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
            x1, y1 = min(self.W, box[2] + pad), min(self.H, box[3] + pad)
            warped = np.zeros((self.H, self.W, 4), np.uint8)
            warped[y0:y1, x0:x1] = cv2.remap(np.asarray(layer), mx[y0:y1, x0:x1], my[y0:y1, x0:x1],
                                             interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                                             borderValue=(0, 0, 0, 0))
            out = np.asarray(Image.alpha_composite(self.paper, Image.fromarray(warped, "RGBA")))
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

    def bgr(self, t: float) -> np.ndarray:
        import cv2

        frame = cv2.cvtColor(self._compose(t), cv2.COLOR_RGBA2BGR)
        if self.captions is not None:
            self.captions.draw(frame, t)
        return frame


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
