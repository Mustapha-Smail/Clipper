"""The ink look, drawn by code: brush lines on paper that draw themselves on.

Everything here returns SVG (or an RGBA picture made from it) for one thing at
one moment: a brush stroke cut at how far the pen has got, a stick figure in a
pose whose arms reach for a point, a library prop redrawn as ink lines with a
watercolour wash, handwriting that writes itself left to right. `stage.py`
places these on the page and moves the camera; the hand-drawn wobble ("boil")
is added there, once per frame, to the whole layer of ink.

Library props are drawn once in the flat house style; `ink_prop` turns any of
them into line art and a wash, so the ink style needs no new drawings.
"""

import io
import json
import math
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageColor, ImageFont

from .. import config
from . import art

INK = "#1d1c1a"
PAPER = "#f7f3ea"
COLORS = {"warm": "#e9853a", "bad": "#d64534", "good": "#3d7cc9", "gold": "#e2ad24", "green": "#4c9a57", "ink": INK}
FONT = config.FONTS / "PatrickHand-Regular.ttf"
SVGNS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVGNS)


def render(svg: str) -> Image.Image:
    """SVG (with its own width and height in pixels) to an RGBA picture."""
    import resvg_py

    png = resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True, font_files=[str(FONT)])
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def svg_doc(body: str, width: float, height: float, view: tuple[float, float, float, float]) -> str:
    w, h = max(2, round(width)), max(2, round(height))
    vx, vy, vw, vh = view
    return (f'<svg xmlns="{SVGNS}" width="{w}" height="{h}" viewBox="{vx:.1f} {vy:.1f} {vw:.1f} {vh:.1f}">'
            f'{body}</svg>')


# --- easing -------------------------------------------------------------------------------

def spring(p: float, damping: float = 0.45, frequency: float = 2.2) -> float:
    """A damped spring from 0 to 1 over p in [0, 1]: it overshoots a little, then settles."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    w = 2 * math.pi * frequency
    return 1 - math.exp(-damping * w * p) * (math.cos(w * math.sqrt(1 - damping ** 2) * p)
                                             + damping / math.sqrt(1 - damping ** 2)
                                             * math.sin(w * math.sqrt(1 - damping ** 2) * p))


def ease(p: float) -> float:
    """Ease in and out, for camera moves and walks."""
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def ease_out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


# --- brush strokes ----------------------------------------------------------------------------

Points = list[tuple[float, float]]


def length(points: Points) -> float:
    pts = np.asarray(points, float)
    return float(np.hypot(*np.diff(pts, axis=0).T).sum()) if len(pts) > 1 else 0.0


def ribbon(points: Points, width: float, progress: float = 1.0, taper: float = 0.22, thin: float = 0.4) -> str:
    """A brush stroke along a polyline, as a filled SVG path: thin at both ends, full in the middle.

    `progress` draws only the first part of the stroke, as if the pen were still moving.
    """
    pts = np.asarray(points, float)
    if len(pts) < 2 or progress <= 0:
        return ""
    seg = np.hypot(*np.diff(pts, axis=0).T)
    keep = np.concatenate([[True], seg > 1e-6])
    pts = pts[keep]
    if len(pts) < 2:
        return ""
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if progress < 1:
        reach = total * progress
        if reach < 0.5:
            return ""
        k = int(np.searchsorted(cum, reach))
        k = max(1, min(k, len(pts) - 1))
        frac = (reach - cum[k - 1]) / max(seg[k - 1], 1e-6)
        end = pts[k - 1] + (pts[k] - pts[k - 1]) * frac
        pts = np.vstack([pts[:k], end])
        cum = np.concatenate([cum[:k], [reach]])
    s = cum / total
    if taper > 0:
        w = width * np.clip(np.minimum(s / taper, (1 - s) / taper), thin, 1.0)
    else:
        w = np.full(len(s), width)
    tangent = np.gradient(pts, axis=0)
    norm = np.hypot(tangent[:, 0], tangent[:, 1])[:, None]
    tangent = tangent / np.maximum(norm, 1e-6)
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
    left = pts + normal * (w[:, None] / 2)
    right = pts - normal * (w[:, None] / 2)

    def xy(p) -> str:
        return f"{p[0]:.1f} {p[1]:.1f}"

    r_end, r_start = w[-1] / 2, w[0] / 2
    d = ["M" + xy(left[0])] + ["L" + xy(p) for p in left[1:]]
    d.append(f"A{r_end:.1f} {r_end:.1f} 0 0 1 {xy(right[-1])}")
    d += ["L" + xy(p) for p in right[-2::-1]]
    d.append(f"A{r_start:.1f} {r_start:.1f} 0 0 1 {xy(left[0])}Z")
    return "".join(d)


def strokes_svg(strokes: list[tuple[Points, float, str]], progress: float = 1.0, taper: float = 0.22) -> str:
    """Several strokes drawn one after another by the same pen, as SVG paths."""
    lengths = [length(p) for p, _, _ in strokes]
    total = sum(lengths) or 1.0
    reach = total * progress
    out = []
    for (points, width, color), size in zip(strokes, lengths, strict=True):
        if reach <= 0:
            break
        part = 1.0 if reach >= size else reach / max(size, 1e-6)
        d = ribbon(points, width, part, taper)
        if d:
            out.append(f'<path d="{d}" fill="{color}"/>')
        reach -= size
    return "".join(out)


def quad(a, c, b, n: int = 24) -> Points:
    t = np.linspace(0, 1, n)[:, None]
    pts = (1 - t) ** 2 * np.asarray(a) + 2 * (1 - t) * t * np.asarray(c) + t ** 2 * np.asarray(b)
    return [tuple(p) for p in pts]


def loop(cx: float, cy: float, rx: float, ry: float, start: float = -100, turns: float = 1.0, wobble: float = 0.0,
         seed: int = 0, n: int = 64) -> Points:
    """An ellipse drawn by hand: from `start` degrees, `turns` times round, with a little wobble."""
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0, 2 * math.pi, 3)
    a = np.radians(start) + np.linspace(0, 2 * math.pi * turns, max(8, int(n * turns)))
    k = 1 + wobble * (0.5 * np.sin(2 * a + phase[0]) + 0.3 * np.sin(3 * a + phase[1]))
    k = k * (1 + 0.04 * wobble * (a - a[0]))        # the pen drifts outward as it goes round
    return list(zip(cx + rx * k * np.cos(a), cy + ry * k * np.sin(a), strict=True))


# --- marks: arrows, circles, crosses ---------------------------------------------------------

def arrow_strokes(a, b, width: float, color: str, bend: float = 0.18) -> list[tuple[Points, float, str]]:
    ax, ay = a
    bx, by = b
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = bx - ax, by - ay
    control = (mx - dy * bend, my + dx * bend)
    shaft = quad(a, control, b, 30)
    tx, ty = shaft[-1][0] - shaft[-4][0], shaft[-1][1] - shaft[-4][1]
    angle = math.atan2(ty, tx)
    head = max(18.0, width * 3.4)
    tip = shaft[-1]
    left = (tip[0] - head * math.cos(angle - 0.5), tip[1] - head * math.sin(angle - 0.5))
    right = (tip[0] - head * math.cos(angle + 0.5), tip[1] - head * math.sin(angle + 0.5))
    return [(shaft, width, color), ([left, tip], width, color), ([tip, right], width, color)]


def mark_strokes(kind: str, box: tuple[float, float, float, float], width: float, color: str,
                 seed: int = 0) -> list[tuple[Points, float, str]]:
    """A circle round, a line under, a cross over or a tick by a box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if kind == "circle":
        return [(loop(cx, cy, w / 2 + max(18, w * 0.12), h / 2 + max(18, h * 0.14), -120, 1.12, 0.05, seed),
                 width, color)]
    if kind == "underline":
        y = y1 + max(10, h * 0.06)
        return [(quad((x0 + w * 0.04, y + 4), (cx, y - 8), (x1 - w * 0.02, y + 2), 20), width, color)]
    if kind == "cross":
        m = min(w, h) * 0.08
        return [([(x0 + m, y0 + m), (x1 - m, y1 - m)], width * 1.2, color),
                ([(x1 - m, y0 + m), (x0 + m, y1 - m)], width * 1.2, color)]
    if kind == "tick":
        s = min(w, h) * 0.5
        ox, oy = x1 - s * 0.2, y0 + s * 0.15
        return [([(ox - s * 0.45, oy + s * 0.25), (ox - s * 0.15, oy + s * 0.55), (ox + s * 0.45, oy - s * 0.35)],
                 width * 1.2, color)]
    return []


def strokes_bounds(strokes: list[tuple[Points, float, str]]) -> tuple[float, float, float, float]:
    pts = np.asarray([p for points, _, _ in strokes for p in points])
    pad = max(w for _, w, _ in strokes) * 2 + 4
    return (float(pts[:, 0].min() - pad), float(pts[:, 1].min() - pad),
            float(pts[:, 0].max() + pad), float(pts[:, 1].max() + pad))


# --- the cast, in ink ---------------------------------------------------------------------

ARM, LEG = 74.0, 80.0
TORSO, NECK, HEAD_R = 128.0, 30.0, 60.0
BOX = (-320.0, -330.0, 640.0, 500.0)      # the drawing's view box; the hip is at (0, 0)
FEET_Y = 2 * LEG + 2                       # the ground, below the hip

# (lean, head tilt, back arm (upper, lower), front arm, back leg (upper, lower), front leg)
# Angles in degrees from straight down; positive turns toward the way the figure faces.
POSE_ANGLES = {
    "stand": (0, 0, (-30, -14), (30, 14), (-9, -4), (9, 4)),
    "point": (0, 0, (-30, -14), (86, 92), (-9, -4), (9, 4)),
    "hold": (4, 0, (62, 96), (74, 90), (-9, -4), (9, 4)),
    "push": (20, -6, (78, 100), (86, 96), (-34, -30), (24, 4)),
    "pull": (-16, 6, (72, 76), (80, 80), (-8, -16), (32, 22)),
    "shrug": (0, 8, (-52, -150), (52, 150), (-9, -4), (9, 4)),
    "think": (0, 10, (-30, -14), (24, 168), (-9, -4), (9, 4)),
    "facepalm": (6, 16, (-30, -14), (12, 174), (-9, -4), (9, 4)),
    "cheer": (0, -8, (-128, -150), (128, 150), (-14, -8), (14, 8)),
    "wave": (0, 4, (-30, -14), (120, 160), (-9, -4), (9, 4)),
    "surprised": (-8, -6, (-70, -132), (70, 132), (-12, -6), (12, 6)),
    "sad": (8, 18, (-10, -4), (10, 4), (-6, -3), (6, 3)),
    "crossed": (-3, 4, (-26, 68), (26, -68), (-9, -4), (9, 4)),         # arms folded: an X across the chest
    "hips": (0, -4, (-60, 25), (60, -25), (-12, -6), (12, 6)),           # hands on hips
    "present": (-4, -6, (-30, -14), (62, 88), (-9, -4), (9, 4)),          # "ta-da", palm out
    "scared": (-12, 10, (110, 160), (120, 168), (-34, 20), (30, -24)),    # crouched, arms up in front
    "carry": (-5, 0, (64, 104), (72, 100), (-9, -4), (9, 4)),             # holding something at the chest
    "drop": (0, 6, (-30, -14), (30, 14), (-9, -4), (9, 4)),               # lets go
}
REACH = {"point": ("front",), "hold": ("back", "front"), "push": ("back", "front"), "pull": ("back", "front")}


@dataclass
class Pose:
    lean: float
    tilt: float
    back_arm: tuple[float, float]
    front_arm: tuple[float, float]
    back_leg: tuple[float, float]
    front_leg: tuple[float, float]

    @classmethod
    def named(cls, name: str) -> "Pose":
        a = POSE_ANGLES.get(name, POSE_ANGLES["stand"])
        return cls(a[0], a[1], a[2], a[3], a[4], a[5])

    def blend(self, other: "Pose", k: float) -> "Pose":
        def mix(x, y):
            if isinstance(x, tuple):
                return tuple(mix(i, j) for i, j in zip(x, y, strict=True))
            return x + (y - x) * k
        return Pose(*(mix(getattr(self, f), getattr(other, f)) for f in
                      ("lean", "tilt", "back_arm", "front_arm", "back_leg", "front_leg")))

    def key(self) -> tuple:
        flat = (self.lean, self.tilt, *self.back_arm, *self.front_arm, *self.back_leg, *self.front_leg)
        return tuple(round(v) for v in flat)


def _dir(deg: float) -> np.ndarray:
    r = math.radians(deg)
    return np.array([math.sin(r), math.cos(r)])


def joints(pose: Pose) -> dict[str, np.ndarray]:
    hip = np.array([0.0, 0.0])
    up = _dir(180 - pose.lean)                    # the torso leans forward for positive lean
    shoulder = hip + up * TORSO
    neck = hip + up * (TORSO + NECK * 0.4)
    head = neck + _dir(180 - pose.lean - pose.tilt) * (HEAD_R + NECK * 0.15)
    out = {"hip": hip, "shoulder": shoulder, "neck": neck, "head": head}
    for side, (u, low) in (("back_arm", pose.back_arm), ("front_arm", pose.front_arm)):
        elbow = shoulder + ARM * _dir(u)
        out[side] = np.array([shoulder, elbow, elbow + ARM * _dir(low)])
    for side, (u, low), dx in (("back_leg", pose.back_leg, -6), ("front_leg", pose.front_leg, 6)):
        top = hip + np.array([dx, 0.0])
        knee = top + LEG * _dir(u)
        out[side] = np.array([top, knee, knee + LEG * _dir(low)])
    return out


def reach(pose: Pose, side: str, target: tuple[float, float]) -> Pose:
    """The pose with one arm bent to put its hand on `target` (in the drawing's units), as far as it can."""
    shoulder = joints(pose)["shoulder"]
    d = np.asarray(target, float) - shoulder
    dist = float(np.hypot(*d))
    dist = min(max(dist, 12.0), 2 * ARM - 1.0)
    aim = math.degrees(math.atan2(d[0], d[1]))
    bend = math.degrees(math.acos(min(1.0, dist / (2 * ARM))))
    upper = aim - bend if aim >= 0 else aim + bend      # elbows bend downward
    lower = aim + bend if aim >= 0 else aim - bend
    angles = (upper, lower)
    return Pose(pose.lean, pose.tilt, angles if side == "back_arm" else pose.back_arm,
                angles if side == "front_arm" else pose.front_arm, pose.back_leg, pose.front_leg)


# --- moves from motion capture -----------------------------------------------------------------

MOVES_FILE = config.ASSETS / "moves.json"
GAITS = ("walk", "run", "march", "zombie")


@lru_cache(maxsize=1)
def moves() -> dict:
    """Recorded moves (CMU motion capture, see tools/mocap_import.py): name -> clip."""
    data = json.loads(MOVES_FILE.read_text())
    out = {}
    for name, clip in data["moves"].items():
        frames = np.asarray(clip["frames"], dtype=np.float32)
        out[name] = {"frames": frames, "loop": clip["loop"], "what": clip["what"], "view": clip["view"],
                     "seconds": len(frames) / data["fps"], "stride": float(frames[-1, 10] - frames[0, 10])}
    return out


MOVE_FPS = 24


def _frame_pose(f: np.ndarray) -> Pose:
    return Pose(float(f[0]), float(f[1]), (float(f[2]), float(f[3])), (float(f[4]), float(f[5])),
                (float(f[6]), float(f[7])), (float(f[8]), float(f[9])))


def move_frame(name: str, seconds: float) -> tuple[Pose, float, float]:
    """A recorded move `seconds` in: the pose, how far the body has travelled, and how high it is off the ground."""
    clip = moves()[name]
    frames = clip["frames"]
    k = min(seconds * MOVE_FPS, len(frames) - 1.001)
    i = int(k)
    f = frames[i] + (frames[min(i + 1, len(frames) - 1)] - frames[i]) * (k - i)
    return _frame_pose(f), float(f[10] - frames[0, 10]), float(f[11])


def gait_frame(name: str, distance: float) -> tuple[Pose, float]:
    """A walk (or run, march, shuffle) after `distance` drawing units: the pose and the height off the ground."""
    clip = moves()[name]
    frames = clip["frames"]
    phase = (distance / max(clip["stride"], 1.0)) % 1.0
    k = phase * (len(frames) - 1)
    i = int(k)
    f = frames[i] + (frames[min(i + 1, len(frames) - 1)] - frames[i]) * (k - i)
    return _frame_pose(f), float(f[11])


@lru_cache(maxsize=32)
def move_cues(name: str) -> tuple[tuple[float, str], ...]:
    """When a recorded move makes a sound, from the recording itself: take-offs, landings, the swing of a kick."""
    clip = moves()[name]
    frames = clip["frames"]
    air = frames[:, 11]
    cues = []
    for i in range(1, len(frames)):
        if air[i - 1] <= 3 < air[i] and name in ("jump", "cartwheel"):
            cues.append((i / MOVE_FPS, "swish"))
        if air[i - 1] > 3 >= air[i]:
            cues.append((i / MOVE_FPS, "thud" if name in ("jump", "cartwheel", "stumble") else "step"))
    if name in ("kick", "throw"):
        limbs = frames[:, [8, 6]] if name == "kick" else frames[:, [4, 2]]
        speed = np.abs(np.diff(limbs, axis=0)).max(axis=1)
        cues.append((float(np.argmax(speed)) / MOVE_FPS, "swish"))
    return tuple(sorted(cues))


def walk_pose(base: Pose, phase: float) -> Pose:
    """Legs and arms swinging, for a figure on the move. `phase` counts steps. (Without recorded moves.)"""
    s = math.sin(phase * math.pi)
    return Pose(base.lean + 4, base.tilt, (-14 - 16 * s, -10 - 10 * s), (14 + 16 * s, 8 + 14 * s),
                (-26 * s, -26 * s - 8 * abs(s)), (26 * s, 26 * s - 8 * abs(s)))


def lowest(pose: Pose) -> float:
    """How far below the hip the lowest point of the figure is (feet, hands or head): it rests on the ground."""
    j = joints(pose)
    points = [j["back_leg"][-1][1], j["front_leg"][-1][1], j["back_arm"][-1][1], j["front_arm"][-1][1],
              j["head"][1] + HEAD_R]
    return max(points) + 4


def figure_svg(variant: str, pose: Pose, expression: str = "neutral", mouth: int = 0, blink: bool = False,
               facing: str = "right", progress: float = 1.0, scale_px: float = 1.0) -> str:
    """The figure in ink inside BOX (hip at 0, 0). `progress` < 1 draws it on: head, body, legs, arms, face."""
    j = joints(pose)
    width = 11.0
    head = j["head"]
    parts = []

    def stage(lo: float, hi: float) -> float:
        return min(1.0, max(0.0, (progress - lo) / (hi - lo)))

    p_head, p_body, p_legs, p_arms, p_face = stage(0, 0.3), stage(0.25, 0.42), stage(0.4, 0.62), \
        stage(0.6, 0.85), stage(0.82, 1.0)
    # body, legs and arms first: the head is drawn over the neck
    if p_body:
        parts.append(f'<path d="{ribbon([j["hip"], j["neck"]], width, p_body, 0.15, 0.7)}" fill="{INK}"/>')
    for side in ("back_leg", "front_leg"):
        if p_legs:
            parts.append(f'<path d="{ribbon(j[side], width, p_legs, 0.12, 0.6)}" fill="{INK}"/>')
    hands = []
    for side in ("back_arm", "front_arm"):
        if p_arms:
            parts.append(f'<path d="{ribbon(j[side], width * 0.95, p_arms, 0.12, 0.6)}" fill="{INK}"/>')
            if p_arms >= 1:
                hands.append(j[side][-1])
    for hx, hy in hands:
        parts.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="8" fill="{INK}"/>')
    if p_head:
        if p_head >= 1:
            parts.append(f'<circle cx="{head[0]:.1f}" cy="{head[1]:.1f}" r="{HEAD_R:.0f}" fill="#ffffff"/>')
        outline = loop(head[0], head[1], HEAD_R, HEAD_R, -100, 1.04, 0.02, seed=3)
        parts.append(f'<path d="{ribbon(outline, width, p_head, 0.08, 0.6)}" fill="{INK}"/>')
    if p_face:
        k = HEAD_R / 70
        face = _face(variant, expression, mouth, blink)
        parts.append(f'<g opacity="{p_face:.2f}" transform="translate({head[0]:.1f} {head[1]:.1f}) scale({k:.3f}) '
                     f'rotate({pose.lean + pose.tilt:.1f}) translate(-150 -110)">{face}</g>')
    body = "".join(parts)
    if facing == "left":
        body = f'<g transform="scale(-1 1)">{body}</g>'
    vx, vy, vw, vh = BOX
    return svg_doc(body, vw * scale_px, vh * scale_px, (vx, vy, vw, vh))


def _face(variant: str, expression: str, mouth: int, blink: bool) -> str:
    """Eyes, brows, mouth and costume in art.py's head frame (head at 150, 110, radius 70)."""
    s = f'stroke="{INK}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    out = []
    if blink or expression == "deadpan":
        out.append(f'<path d="M116 102 L134 102 M166 102 L184 102" {s}/>')
    else:
        r = 11 if expression == "surprised" else 8
        out.append(f'<circle cx="125" cy="100" r="{r}" fill="{INK}"/><circle cx="175" cy="100" r="{r}" fill="{INK}"/>')
    brows = art.BROWS.get(expression, "")
    if brows:
        out.append(f'<path d="{brows}" {s.replace("9", "7", 1)}/>')
    if mouth or expression == "surprised":
        h = {0: 10, 1: 12, 2: 20}[mouth] if expression != "surprised" else 14 + 4 * mouth
        out.append(f'<ellipse cx="150" cy="150" rx="{20 if expression != "happy" else 26}" ry="{h}" fill="{INK}"/>')
    else:
        out.append(f'<path d="{art.MOUTHS.get(expression) or art.MOUTHS["neutral"]}" {s}/>')
    costume = art._costume(variant).replace(art.INK, INK)
    for name, colour in art.PALETTE.items():     # costume colours become washes
        if name in ("yellow", "red", "blue"):
            costume = costume.replace(colour, _wash(colour))
    out.append(costume)
    return "".join(out)


FIGURE_FEET = (0.0, FEET_Y)


def figure_anchor(scale_px: float) -> tuple[float, float]:
    """Where the feet are in the figure's picture, in pixels."""
    vx, vy, _, _ = BOX
    return (FIGURE_FEET[0] - vx) * scale_px, (FIGURE_FEET[1] - vy) * scale_px


# --- props, redrawn in ink ---------------------------------------------------------------------

DRAWABLE = ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon")


def _rgb(colour: str) -> tuple[int, int, int] | None:
    try:
        return ImageColor.getrgb(colour)[:3]
    except (ValueError, TypeError):
        return None


def _luma(rgb: tuple[int, int, int]) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def _wash(colour: str) -> str:
    """A colour as a watercolour wash: lighter and a little warmer."""
    rgb = _rgb(colour)
    if rgb is None:
        return colour
    paper = _rgb(PAPER)
    mixed = [round(c * 0.62 + p * 0.38) for c, p in zip(rgb, paper, strict=True)]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _num(value: str | None, default: float = 0.0) -> float:
    try:
        return float(re.sub(r"[a-z%]+$", "", (value or "").strip()) or default)
    except ValueError:
        return default


def _shape_length(el: ET.Element, tag: str) -> float:
    if tag == "path":
        import svgelements

        try:
            return float(svgelements.Path(el.get("d", "")).length(error=1e-2))
        except Exception:  # noqa: BLE001 - a path svgelements cannot measure just draws at once
            return 0.0
    if tag == "rect":
        w, h = _num(el.get("width")), _num(el.get("height"))
        r = min(_num(el.get("rx"), _num(el.get("ry"))), w / 2, h / 2)
        return 2 * (w + h) - (8 - 2 * math.pi) * r
    if tag == "circle":
        return 2 * math.pi * _num(el.get("r"))
    if tag == "ellipse":
        a, b = _num(el.get("rx")), _num(el.get("ry"))
        return math.pi * (3 * (a + b) - math.sqrt(max(0.0, (3 * a + b) * (a + 3 * b))))
    if tag == "line":
        return math.hypot(_num(el.get("x2")) - _num(el.get("x1")), _num(el.get("y2")) - _num(el.get("y1")))
    numbers = [float(x) for x in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", el.get("points", ""))]
    pts = list(zip(numbers[::2], numbers[1::2], strict=False))
    if tag == "polygon" and pts:
        pts.append(pts[0])
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:], strict=False))


@dataclass
class InkProp:
    lines: ET.Element              # the drawing as line art, every shape with explicit fill and stroke
    wash: str                      # the colours alone, as SVG
    shapes: list[tuple[ET.Element, float, bool, bool]]   # (element, stroke length, has stroke, has fill)
    view: tuple[float, float, float, float]
    content: tuple[float, float, float, float]   # where the drawing actually is, as fractions of the view


@lru_cache(maxsize=256)
def ink_prop(svg: str) -> InkProp:
    """Split a library prop into ink line art (which draws itself on) and a colour wash."""
    root = ET.fromstring(svg)
    view = tuple(float(v) for v in re.split(r"[\s,]+", root.get("viewBox", "0 0 512 512").strip())[:4])
    wash_root = ET.fromstring(svg)
    shapes = []

    def walk(el: ET.Element, wash_el: ET.Element, fill: str, stroke: str, sw: float) -> None:
        fill = el.get("fill", fill)
        stroke = el.get("stroke", stroke)
        sw = _num(el.get("stroke-width"), sw) if el.get("stroke-width") else sw
        tag = el.tag.split("}")[-1]
        if tag in DRAWABLE:
            f_rgb = _rgb(fill) if fill != "none" else None
            s_rgb = _rgb(stroke) if stroke != "none" else None
            has_fill = f_rgb is not None
            if f_rgb is None:
                line_fill, wash_fill = "none", "none"
            elif _luma(f_rgb) < 0.3:
                line_fill, wash_fill = INK, "none"
            elif _luma(f_rgb) > 0.93:
                line_fill, wash_fill = PAPER, "none"
            else:
                line_fill, wash_fill = PAPER, _wash(fill)
            light_stroke = s_rgb is not None and _luma(s_rgb) > 0.85
            has_stroke = s_rgb is not None and not light_stroke
            el.set("fill", line_fill)
            el.set("stroke", INK if has_stroke else "none")
            el.set("stroke-width", f"{max(2.0, sw * 0.62):.1f}")
            el.set("stroke-linecap", "round")
            el.set("stroke-linejoin", "round")
            wash_el.set("fill", wash_fill)
            wash_el.set("stroke", "none")
            if light_stroke:      # white highlights stay, in the wash
                wash_el.set("stroke", "#ffffff")
                wash_el.set("stroke-width", f"{sw * 0.8:.1f}")
                wash_el.set("stroke-linecap", "round")
            shapes.append((el, _shape_length(el, tag) if has_stroke else 0.0, has_stroke, has_fill))
        for child, wash_child in zip(list(el), list(wash_el), strict=True):
            walk(child, wash_child, fill, stroke, sw)

    walk(root, wash_root, "#000000", "none", 1.0)
    for r in (root, wash_root):
        for key in ("width", "height"):
            r.attrib.pop(key, None)
    return InkProp(root, ET.tostring(wash_root, encoding="unicode"), shapes, view, _content(svg))


def _content(svg: str) -> tuple[float, float, float, float]:
    """The part of the view box the drawing covers, from a small render."""
    try:
        image = art.rasterize(svg, 96)
    except Exception:  # noqa: BLE001 - an unreadable drawing counts as filling its box
        return (0.0, 0.0, 1.0, 1.0)
    box = image.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
    if not box:
        return (0.0, 0.0, 1.0, 1.0)
    w, h = image.size
    return (box[0] / w, box[1] / h, box[2] / w, box[3] / h)


def prop_lines_svg(prop: InkProp, progress: float, width: int, height: int) -> str:
    """The line art with the pen `progress` of the way through, sized width x height pixels."""
    with _tree_lock:     # the cached tree is changed for a moment, then put back
        return _prop_lines_svg(prop, progress, width, height)


_tree_lock = threading.Lock()


def _prop_lines_svg(prop: InkProp, progress: float, width: int, height: int) -> str:
    total = sum(n for _, n, _, _ in prop.shapes) or 1.0
    reach = total * progress
    done = 0.0
    saved = []
    for el, n, has_stroke, has_fill in prop.shapes:
        saved.append((el, el.get("stroke-dasharray"), el.get("fill-opacity")))
        if progress >= 1:
            continue
        if has_stroke:
            seen = reach - done
            if seen <= 0:
                el.set("stroke-dasharray", f"0 {n + 2:.1f}")
            elif seen < n:
                el.set("stroke-dasharray", f"{seen:.1f} {n + 2:.1f}")
        if has_fill and reach < done + n + (0 if has_stroke else 1):
            el.set("fill-opacity", "0")
        done += n
    root = prop.lines
    root.set("width", str(width))
    root.set("height", str(height))
    text = ET.tostring(root, encoding="unicode")
    for el, dash, opacity in saved:     # the tree is shared and cached: put it back
        for key, value in (("stroke-dasharray", dash), ("fill-opacity", opacity)):
            if value is None:
                el.attrib.pop(key, None)
            else:
                el.set(key, value)
    root.attrib.pop("width", None)
    root.attrib.pop("height", None)
    return text


@lru_cache(maxsize=256)
def _wash_image(svg: str, width: int, height: int) -> np.ndarray:
    prop = ink_prop(svg)
    doc = prop.wash.replace("<svg ", f'<svg width="{width}" height="{height}" ', 1)
    image = np.asarray(render(doc), dtype=np.float32) / 255
    # watercolour: uneven pigment
    rng = np.random.default_rng(len(svg))
    grain = rng.normal(0, 1, (max(2, height // 24), max(2, width // 24))).astype(np.float32)
    grain = np.asarray(Image.fromarray(grain).resize((width, height), Image.BICUBIC))
    image[..., 3] *= np.clip(0.88 + 0.07 * grain, 0.7, 1.0)
    return image


def prop_image(svg: str, box: int, progress: float) -> Image.Image:
    """A library prop in ink, `box` pixels on its longer side, drawn `progress` of the way."""
    prop = ink_prop(svg)
    _, _, vw, vh = prop.view
    k = box / max(vw, vh)
    width, height = max(4, round(vw * k)), max(4, round(vh * k))
    lines = np.asarray(render(prop_lines_svg(prop, progress, width, height)), dtype=np.float32) / 255
    show = min(1.0, max(0.0, (progress - 0.55) / 0.45))
    if show <= 0:
        return Image.fromarray((lines * 255).astype(np.uint8), "RGBA")
    wash = _wash_image(svg, width, height)
    pad = max(4, box // 60)
    out = np.zeros((height + pad, width + pad, 4), np.float32)
    out[:height, :width] = lines
    # the wash sits a little off the lines, as if painted after the ink dried
    w = np.zeros_like(out)
    w[pad:, pad // 2:width + pad // 2] = wash[:height + pad - pad, :]
    wa = w[..., 3:4] * show
    la = out[..., 3:4]
    rgb = out[..., :3] * (1 - wa * la) + out[..., :3] * w[..., :3] * (wa * la)    # multiply where both
    rgb = np.where(la > 0, rgb, w[..., :3])
    alpha = np.maximum(la, wa * 0.92)
    result = np.concatenate([rgb, alpha], axis=2)
    return Image.fromarray(np.clip(result * 255, 0, 255).astype(np.uint8), "RGBA")


# --- handwriting -----------------------------------------------------------------------------

@lru_cache(maxsize=16)
def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def wrap(text: str, size: int, max_width: float) -> list[str]:
    f = font(size)
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and f.getlength(trial) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return [*lines, line] if line else lines


def text_size(lines: list[str], size: int) -> tuple[int, int]:
    f = font(size)
    width = max((f.getlength(x) for x in lines), default=0)
    return int(width + size * 0.4), int(size * 1.08 * len(lines) + size * 0.35)


def text_svg(lines: list[str], size: int, color: str, progress: float, underline: str | None = None) -> str:
    """Handwritten lines, written on from left to right (`progress`), optionally underlined after."""
    f = font(size)
    width, height = text_size(lines, size)
    extra = int(size * 0.35) if underline else 0
    ascent, _ = f.getmetrics()
    widths = [f.getlength(x) for x in lines]
    total = sum(widths) or 1.0
    write = min(1.0, progress / (0.8 if underline else 1.0))
    reach = total * write
    clips, texts = [], []
    line_h = size * 1.08
    for i, (line, w) in enumerate(zip(lines, widths, strict=True)):
        shown = max(0.0, min(w, reach))
        reach -= w
        x = (width - w) / 2
        y = size * 0.15 + i * line_h
        clips.append(f'<rect x="{x - 4:.1f}" y="{y - 4:.1f}" width="{shown + 6:.1f}" height="{line_h + 8:.1f}"/>')
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        texts.append(f'<text x="{x:.1f}" y="{y + ascent * 0.98:.1f}" font-family="Patrick Hand" font-size="{size}" '
                     f'fill="{color}">{safe}</text>')
    body = f'<clipPath id="w">{"".join(clips)}</clipPath><g clip-path="url(#w)">{"".join(texts)}</g>'
    if underline and progress > 0.8:
        y = height - extra * 0.2
        stroke = quad((width * 0.08, y + 2), (width * 0.5, y - size * 0.12), (width * 0.94, y), 20)
        body += f'<path d="{ribbon(stroke, max(5, size * 0.09), (progress - 0.8) / 0.2)}" fill="{underline}"/>'
    return svg_doc(body, width, height + extra, (0, 0, width, height + extra))
