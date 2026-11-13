"""The cartoon look (trial): colour characters on the ink rig, and drawn places behind them.

Characters use the same skeleton, poses and recorded moves as the ink figures
(`ink.joints`), drawn as filled shapes with an outline instead of brush lines.
Places are drawn by code in SVG for any frame shape: the floor line is given,
the furniture keeps clear of the middle where the actors stand.
"""

import io
import math
import random
from functools import lru_cache

import numpy as np
from PIL import Image

from . import art, ink

OUT = "#2b2220"                     # outline
OUTLINE = 5.0
SKIN = "#f2c9a5"
SHOE = "#4a3428"

# Default clothes per cast member (shirt, trousers), when no look is given.
CLOTHES = {
    "narrator": ("#f2b134", "#3b5a8c"), "person": ("#3fa59a", "#5b4a3e"), "scientist": ("#ffffff", "#4c5260"),
    "worker": ("#f08a24", "#3b5a8c"), "royal": ("#7d4fb0", "#3a2f4f"), "fancy": ("#2f3138", "#2f3138"),
    "kid": ("#e4572e", "#3b5a8c"), "chef": ("#ffffff", "#4c5260"), "woman": ("#5bb36a", "#4a7fc0"),
    "elder": ("#8a6a4e", "#5b4a3e"),
}
DEFAULT_HAIR = {"narrator": ["short", "brown_hair"], "person": ["side_part", "black_hair"]}
LONG = {"robe": 0.0, "dress": 0.25, "toga": 0.2, "lab_coat": 0.55}    # how far up the legs the hem stops (0 = feet)


def _shade(colour: str, k: float) -> str:
    """Darker (k < 1) or lighter (k > 1) version of a colour."""
    c = colour.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    if k <= 1:
        r, g, b = (round(v * k) for v in (r, g, b))
    else:
        r, g, b = (round(v + (255 - v) * (k - 1)) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def _limb(points, width: float, colour: str) -> str:
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
    return (f'<path d="{d}" fill="none" stroke="{OUT}" stroke-width="{width + 2 * OUTLINE:.1f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width:.1f}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def _poly(points, colour: str) -> str:
    d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points) + " Z"
    return (f'<path d="{d}" fill="{colour}" stroke="{OUT}" stroke-width="{OUTLINE}" stroke-linejoin="round"/>')


def _eyes(expression: str, blink: bool) -> str:
    s = f'stroke="{OUT}" stroke-width="6" stroke-linecap="round" fill="none"'
    if blink or expression == "deadpan":
        return f'<path d="M112 102 Q128 110 144 102 M156 102 Q172 110 188 102" {s}/>'
    ry = 22 if expression == "surprised" else 19
    out = ""
    for cx in (128, 172):
        out += (f'<ellipse cx="{cx}" cy="98" rx="16" ry="{ry}" fill="#ffffff" stroke="{OUT}" stroke-width="4"/>'
                f'<circle cx="{cx + 5}" cy="102" r="9" fill="{OUT}"/><circle cx="{cx + 8}" cy="98" r="3" fill="#fff"/>')
    return out


def _mouth(expression: str, mouth: int) -> str:
    s = f'stroke="{OUT}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    if mouth or expression == "surprised":
        h = {0: 10, 1: 13, 2: 21}[mouth] if expression != "surprised" else 14 + 4 * mouth
        return (f'<ellipse cx="150" cy="152" rx="{20 if expression != "happy" else 26}" ry="{h}" fill="#7a2a26" '
                f'stroke="{OUT}" stroke-width="5"/>'
                f'<ellipse cx="150" cy="{152 + h * 0.45:.1f}" rx="11" ry="{max(3, h * 0.35):.1f}" fill="#e0716b"/>')
    if expression == "happy":
        return (f'<path d="M118 138 Q150 182 182 138 Z" fill="#7a2a26" stroke="{OUT}" stroke-width="5" '
                f'stroke-linejoin="round"/>')
    return f'<path d="{art.MOUTHS.get(expression) or art.MOUTHS["neutral"]}" {s}/>'


def figure_svg(variant: str, pose: ink.Pose, expression: str = "neutral", mouth: int = 0, blink: bool = False,
               facing: str = "right", scale_px: float = 1.0, look: dict | None = None) -> str:
    """A colour cartoon character in ink.BOX (hip at 0, 0), on the same skeleton as the ink figure."""
    j = ink.joints(pose)
    look = dict(look or {})
    if not look and variant in DEFAULT_HAIR:
        look = ink.look_of(DEFAULT_HAIR[variant])
        own_costume = variant == "narrator"           # glasses
    else:
        own_costume = not look
    shirt, trousers = CLOTHES.get(variant, CLOTHES["person"])
    outfit = look.get("outfit")
    if outfit:
        shirt = ink.OUTFITS[outfit]
        trousers = _shade(shirt, 0.7) if outfit in ("suit", "uniform", "armour") else trousers
    skin = ink.SKINS.get(look.get("skin", ""), SKIN)
    hip, neck = j["hip"], j["neck"]
    up = (neck - hip) / max(float(np.hypot(*(neck - hip))), 1e-6)
    side = np.array([-up[1], up[0]])
    parts = []

    # a soft shadow on the ground under the feet
    feet_y = max(j["back_leg"][-1][1], j["front_leg"][-1][1])
    parts.append(f'<ellipse cx="0" cy="{feet_y + 10:.1f}" rx="62" ry="12" fill="#000" opacity="0.16"/>')

    def foot(leg, colour):
        x, y = leg[-1]
        return (f'<ellipse cx="{x + 9:.1f}" cy="{y + 4:.1f}" rx="21" ry="12" fill="{colour}" '
                f'stroke="{OUT}" stroke-width="{OUTLINE}"/>')

    def hand(arm):
        x, y = arm[-1]
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="14" fill="{skin}" stroke="{OUT}" stroke-width="{OUTLINE}"/>'

    back_trousers = _shade(trousers, 0.82)
    parts.append(_limb(j["back_leg"], 30, back_trousers) + foot(j["back_leg"], _shade(SHOE, 0.8)))
    parts.append(_limb(j["back_arm"], 24, _shade(shirt, 0.85)) + hand(j["back_arm"]))
    parts.append(_limb(j["front_leg"], 30, trousers) + foot(j["front_leg"], SHOE))
    # the torso: wider at the shoulders, rounded by the outline's joins
    top = hip + up * (ink.TORSO + 6)
    torso = [hip + side * 30 - up * 6, top + side * 36, top - side * 36, hip - side * 30 - up * 6]
    parts.append(_poly(torso, shirt))
    if outfit in LONG:                              # a robe or dress over the legs
        knee = min(j["back_leg"][1][1], j["front_leg"][1][1])
        hem_y = feet_y - (feet_y - knee) * LONG[outfit] * 2 + 4
        hem = np.array([0.0, hem_y])
        wide = 54 if outfit != "lab_coat" else 44
        parts.append(_poly([top + side * 36, top - side * 36, hip - side * 40, hem + np.array([-wide, 0]),
                            hem + np.array([wide, 0]), hip + side * 40], shirt))
        if outfit in ("robe", "tunic"):
            belt = [hip + side * 40 + up * 4, hip - side * 40 + up * 4]
            parts.append(_limb(belt, 9, _shade(shirt, 0.6)))
    if outfit == "suit":
        parts.append(_poly([top + side * 14, top - side * 14, top - up * 70], "#ffffff"))
        parts.append(_poly([top - up * 8, top - up * 58 + side * 7, top - up * 74, top - up * 58 - side * 7],
                           art.PALETTE["red"]))
    if outfit == "cape":
        parts.insert(1, _poly([top + side * 30, top - side * 30, hip - side * 70 + up * -120,
                               hip + side * 70 + up * -120], ink.OUTFITS["cape"]))

    # the head, in art.py's head frame (centre 150, 110, radius 70)
    head = j["head"]
    k = ink.HEAD_R / 70
    frame = (f'transform="translate({head[0]:.1f} {head[1]:.1f}) scale({k:.3f}) '
             f'rotate({pose.lean + pose.tilt:.1f}) translate(-150 -110)"')
    hair_behind, hair_over = ink._hair_svg(look) if look else ("", "")
    face = [hair_behind.replace(ink.INK, OUT)]
    face.append(f'<rect x="132" y="150" width="36" height="50" fill="{skin}" stroke="{OUT}" stroke-width="5"/>')
    face.append(f'<circle cx="82" cy="116" r="15" fill="{skin}" stroke="{OUT}" stroke-width="5"/>')     # ear
    face.append(f'<circle cx="150" cy="110" r="70" fill="{skin}" stroke="{OUT}" stroke-width="6"/>')
    face.append('<circle cx="112" cy="134" r="13" fill="#e8806f" opacity="0.35"/>'
                '<circle cx="190" cy="134" r="13" fill="#e8806f" opacity="0.35"/>')
    face.append(_eyes(expression, blink))
    brows = art.BROWS.get(expression, "")
    if brows:
        face.append(f'<path d="{brows}" stroke="{OUT}" stroke-width="7" stroke-linecap="round" fill="none"/>')
    face.append(_mouth(expression, mouth))
    if look:
        face.append((ink._facial_svg(look) + hair_over + ink._eyes_svg(look) + ink._hat_svg(look))
                    .replace(ink.INK, OUT))
    if own_costume:
        face.append(art._costume(variant).replace(art.INK, OUT))
    parts.append(f'<g {frame}>{"".join(face)}</g>')
    parts.append(_limb(j["front_arm"], 24, shirt) + hand(j["front_arm"]))
    body = "".join(parts)
    if facing == "left":
        body = f'<g transform="scale(-1 1)">{body}</g>'
    vx, vy, vw, vh = ink.BOX
    return ink.svg_doc(body, vw * scale_px, vh * scale_px, (vx, vy, vw, vh))


# --- places --------------------------------------------------------------------------------------
# A place is drawn larger than the frame, so the camera can move across it. Drawn by code, it gets the
# picture's size (w, h), the floor line (pixels from the top) and the frame's size in the same pixels
# (fw, fh): things are laid out around the middle, where the camera rests, with the actors' area
# (the middle third of the frame) kept calm.

WIDE_VIEW = (2400, 1080)      # Claude's wide drawings (long videos)
TALL_VIEW = (1500, 1920)      # and tall ones (Shorts)
FLOOR = 0.66                  # where the floor meets the wall, as a share of the height, in every place
OVERSCAN = 1.45               # the picture is this many frame heights tall


def picture_size(width: int, height: int) -> tuple[int, int]:
    """The size a place is drawn at for frames of width x height."""
    vw, vh = WIDE_VIEW if width >= height else TALL_VIEW
    ph = int(height * OVERSCAN)
    return int(ph * vw / vh), ph


def _rect(x, y, w, h, fill, extra="") -> str:
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" {extra}/>'


def _outline(width: float = 4) -> str:
    return f'stroke="{OUT}" stroke-width="{width}" stroke-linejoin="round"'


def _line(x0, y0, x1, y1, colour, width) -> str:
    return f'<path d="M{x0:.1f} {y0:.1f} L{x1:.1f} {y1:.1f}" stroke="{colour}" stroke-width="{width:.1f}"/>'


def _window(x, y, w, h, curtains=None) -> str:
    out = _rect(x - 10, y - 10, w + 20, h + 20, "#7a5236", _outline())
    out += _rect(x, y, w, h, "#9fd3f0")
    out += (f'<ellipse cx="{x + w * 0.3:.1f}" cy="{y + h * 0.35:.1f}" rx="{w * 0.22:.1f}" ry="{h * 0.08:.1f}" '
            f'fill="#fff" opacity="0.8"/>')
    out += _rect(x + w / 2 - 5, y, 10, h, "#7a5236") + _rect(x, y + h / 2 - 5, w, 10, "#7a5236")
    if curtains:
        for cx, sgn in ((x - 14, 1), (x + w + 14, -1)):
            out += (f'<path d="M{cx:.1f} {y - 24:.1f} Q{cx + sgn * w * 0.3:.1f} {y + h * 0.5:.1f} {cx + sgn * 6:.1f} '
                    f'{y + h + 30:.1f} L{cx - sgn * 26:.1f} {y + h + 30:.1f} L{cx - sgn * 26:.1f} {y - 24:.1f} Z" '
                    f'fill="{curtains}" {_outline()}/>')
        out += _rect(x - 50, y - 34, w + 100, 12, "#5a3d28", _outline(3))
    return out


def _bookshelf(x, floor, bw, bh, seed) -> str:
    rng = random.Random(seed)
    by = floor - bh
    out = [_rect(x, by, bw, bh, "#8b5a36", _outline())]
    shelves = max(2, round(bh / 140))
    for k in range(shelves):
        sy = by + 18 + k * (bh - 30) / shelves
        sh = (bh - 30) / shelves - 14
        out.append(_rect(x + 14, sy, bw - 28, sh, "#5e3c24"))
        bx = x + 18
        while bx < x + bw - 40:
            bwid, bht = rng.uniform(16, 30), sh * rng.uniform(0.7, 0.98)
            col = rng.choice(["#c0503a", "#3b5a8c", "#4c9a57", "#e2ad24", "#7d4fb0", "#d9cbb0"])
            out.append(_rect(bx, sy + sh - bht, bwid, bht, col, _outline(2)))
            bx += bwid + 3
        out.append(_rect(x + 8, sy + sh, bw - 16, 10, "#7a4a2c", _outline(2)))
    return "".join(out)


def _plant(x, floor) -> str:
    leaves = "".join(f'<ellipse cx="{x + dx:.1f}" cy="{floor - 150 + dy:.1f}" rx="30" ry="62" '
                     f'transform="rotate({a} {x + dx:.1f} {floor - 150 + dy:.1f})" fill="#4c9a57" {_outline(3)}/>'
                     for dx, dy, a in ((-34, 10, -30), (34, 10, 30), (0, -20, 0)))
    return leaves + (f'<path d="M{x - 46:.1f} {floor - 90:.1f} L{x + 46:.1f} {floor - 90:.1f} '
                     f'L{x + 34:.1f} {floor:.1f} '
                     f'L{x - 34:.1f} {floor:.1f} Z" fill="#c0603a" {_outline()}/>')


def study(w: float, h: float, floor: float, fw: float, fh: float) -> str:
    cx, tall = w / 2, fh > fw
    out = [_rect(0, 0, w, floor, "#ead9b9")]
    out += [_rect(x, 0, 35, floor, "#e2cfa9") for x in np.arange(0, w, 70)]
    out.append(_rect(0, floor - 170, w, 170, "#b98a5e", _outline()))
    out += [_rect(x, floor - 150, 120, 120, "none", 'stroke="#a37750" stroke-width="4"') for x in np.arange(30, w, 160)]
    out.append(_rect(0, floor - 10, w, 18, "#7a5236", _outline()))
    out.append(_rect(0, floor + 8, w, h - floor, "#b9824f"))
    for i, y in enumerate(np.arange(floor + 8, h, 46)):
        out.append(_line(0, y, w, y, "#9a6a3e", 3))
        out += [_line(x, y, x, y + 46, "#9a6a3e", 3) for x in np.arange((i % 2) * 110, w, 220)]
    out.append(_window(cx - (0.36 if not tall else 0.33) * fw - 130, floor - 620, 260, 330, curtains="#c0503a"))
    out.append(_bookshelf(cx + (0.22 if not tall else 0.14) * fw, floor, 300, 560 if not tall else 640, 4))
    if not tall:
        px, py = cx - 0.12 * fw, floor - 560
        out.append(_rect(px, py, 160, 120, "#c9a36b", _outline()) + _rect(px + 14, py + 14, 132, 92, "#9fc6a0") +
                   f'<path d="M{px + 14:.1f} {py + 106:.1f} L{px + 60:.1f} {py + 50:.1f} L{px + 100:.1f} {py + 90:.1f} '
                   f'L{px + 146:.1f} {py + 40:.1f} L{px + 146:.1f} {py + 106:.1f} Z" fill="#5c8a63"/>')
    out.append(_plant(cx - 0.62 * fw, floor + 4) + _plant(cx + 0.66 * fw, floor + 4))
    out.append(_window(cx + 0.85 * fw, floor - 620, 260, 330, curtains="#c0503a"))
    out.append(_bookshelf(cx - 1.0 * fw, floor, 300, 560, 8))
    out.append(f'<ellipse cx="{cx:.1f}" cy="{floor + 130:.1f}" rx="{0.3 * min(fw, 1920):.1f}" ry="60" '
               f'fill="#a8463a" {_outline(3)}/><ellipse cx="{cx:.1f}" cy="{floor + 130:.1f}" '
               f'rx="{0.24 * min(fw, 1920):.1f}" ry="42" fill="none" stroke="#e2ad24" stroke-width="5"/>')
    return "".join(out)


def church(w: float, h: float, floor: float, fw: float, fh: float) -> str:
    cx = w / 2
    out = [_rect(0, 0, w, floor, "#d8cfbd")]
    for i, y in enumerate(np.arange(floor % 60, floor, 60)):
        out.append(_line(0, y, w, y, "#c4b9a4", 3))
        out += [_line(x, y, x, y + 60, "#c4b9a4", 3) for x in np.arange((i % 2) * 70, w, 140)]
    rng = random.Random(9)
    gap = 330
    first = cx - gap * math.ceil(cx / gap)
    columns = list(np.arange(first, w + gap, gap))
    for x in columns[:-1]:
        mid = x + gap / 2
        if abs(mid - cx) < gap * 0.6:
            continue                                   # the middle stays plain stone, behind the actors
        ww, wt, wb = 120, floor - 620, floor - 230
        arch = (f'M{mid - ww / 2:.1f} {wb:.1f} L{mid - ww / 2:.1f} {wt + ww * 0.6:.1f} Q{mid - ww / 2:.1f} {wt:.1f} '
                f'{mid:.1f} {wt - ww * 0.25:.1f} Q{mid + ww / 2:.1f} {wt:.1f} {mid + ww / 2:.1f} {wt + ww * 0.6:.1f} '
                f'L{mid + ww / 2:.1f} {wb:.1f} Z')
        out.append(f'<path d="{arch}" fill="#3b4f7a" stroke="#6b5e4a" stroke-width="16"/>')
        rows, cols = 6, 3
        for r in range(rows):
            for c in range(cols):
                col = rng.choice(["#c0503a", "#3b6fb6", "#e2ad24", "#4c9a57", "#7d4fb0", "#5aa0c8"])
                y0 = wt + ww * 0.55 + r * (wb - wt - ww * 0.55) / rows
                out.append(_rect(mid - ww / 2 + c * ww / cols + 3, y0 + 3, ww / cols - 6,
                                 (wb - wt - ww * 0.55) / rows - 6, col, 'opacity="0.85"'))
        out.append(f'<circle cx="{mid:.1f}" cy="{wt + ww * 0.25:.1f}" r="{ww * 0.22:.1f}" fill="#e2ad24" '
                   f'stroke="#6b5e4a" stroke-width="8"/><path d="{arch}" fill="none" stroke="{OUT}" stroke-width="4"/>')
    for x in columns:
        out.append(_rect(x - 26, 0, 52, floor, "#c9bea8", _outline()))
        out.append(_rect(x - 36, floor - 50, 72, 50, "#b8ac94", _outline()))
    out.append(_rect(0, floor, w, h - floor, "#cdbf9f"))
    vy = floor - 420
    for x in np.arange(cx - 40 * 120, cx + 40 * 120, 120):
        out.append(_line(cx + (x - cx) * 0.25, floor, x, floor + (floor - vy) * 3, "#a89878", 3))
    y, step = floor, 22.0
    while y < h:
        out.append(_line(0, y, w, y, "#a89878", 3))
        y += step
        step *= 1.25
    out.append(_line(0, floor, w, floor, OUT, 4))
    return "".join(out)


def _sky(w, h, top, bottom, name="sky") -> str:
    return (f'<defs><linearGradient id="{name}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient></defs>'
            f'<rect width="{w:.1f}" height="{h:.1f}" fill="url(#{name})"/>')


def _cloud(x, y, k) -> str:
    return "".join(f'<ellipse cx="{x + dx * k:.1f}" cy="{y + dy * k:.1f}" rx="{r * k:.1f}" ry="{r * k * 0.8:.1f}" '
                   f'fill="#ffffff"/>' for dx, dy, r in ((0, 0, 50), (50, -20, 60), (110, 0, 48), (55, 18, 60)))


def town(w: float, h: float, floor: float, fw: float, fh: float) -> str:
    rng = random.Random(12)
    cx = w / 2
    out = [_sky(w, floor, "#8ccbeb", "#d9f0f8")]
    out += [_cloud(rng.uniform(0, w), floor - rng.uniform(450, 640), rng.uniform(0.7, 1.2))
            for _ in range(int(w / 500))]
    x = -40.0
    while x < w:
        bw, bh = rng.uniform(160, 260), rng.uniform(330, 470)
        out.append(_rect(x, floor - bh, bw, bh, rng.choice(["#b9c7cf", "#c5c2d6", "#c9d3bf"])))
        x += bw - 10
    x = -20.0
    while x < w:
        bw, bh = rng.uniform(220, 300), rng.uniform(260, 360)
        if abs(x + bw / 2 - cx) > bw * 0.7:
            col = rng.choice(["#e4572e", "#f2b134", "#4a90d9", "#5bb36a", "#f4a7b9", "#e9d8b8"])
            out.append(_rect(x, floor - bh, bw, bh, col, _outline()))
            out.append(f'<path d="M{x - 14:.1f} {floor - bh:.1f} L{x + bw / 2:.1f} {floor - bh - 110:.1f} '
                       f'L{x + bw + 14:.1f} {floor - bh:.1f} Z" fill="#8b4a36" {_outline()}/>')
            for wx in (x + bw * 0.18, x + bw * 0.6):
                out.append(_rect(wx, floor - bh + 50, bw * 0.22, 70, "#fff7d6", _outline(3)))
            out.append(_rect(x + bw * 0.4, floor - 120, bw * 0.22, 120, "#5e3c24", _outline(3)))
        x += bw + 30
    out.append(_rect(0, floor, w, 30, "#9aa0a6", _outline()))
    out.append(_rect(0, floor + 30, w, h - floor, "#b9b4aa"))
    out += [_line(0, y, w, y, "#a49f94", 3) for y in np.arange(floor + 30, h, 60)]
    for lx in (cx + 0.36 * fw, cx - 0.9 * fw):
        out.append(_rect(lx, floor - 420, 14, 420, "#3b3f46", _outline(2)) +
                   f'<circle cx="{lx + 7:.1f}" cy="{floor - 430:.1f}" r="26" fill="#fff2b0" {_outline(3)}/>')
    return "".join(out)


def hills(w: float, h: float, floor: float, fw: float, fh: float) -> str:
    rng = random.Random(3)
    cx = w / 2
    out = [_sky(w, floor + 40, "#7cc3ea", "#e6f5f9")]
    out.append(f'<circle cx="{cx + 0.34 * fw:.1f}" cy="{floor - 520:.1f}" r="70" fill="#ffd95e"/>')
    out += [_cloud(rng.uniform(0, w), floor - rng.uniform(430, 620), rng.uniform(0.6, 1.0))
            for _ in range(int(w / 600))]

    def ridge(base, amp, colour, seed):
        r = random.Random(seed)
        pts = [(x, base - r.uniform(0.3, 1.0) * amp) for x in np.linspace(0, w, max(6, int(w / 300)))]
        d = f"M0 {h:.1f} L0 {pts[0][1]:.1f}"
        for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
            d += f" Q{x0:.1f} {y0:.1f} {(x0 + x1) / 2:.1f} {(y0 + y1) / 2:.1f}"
        return f'<path d="{d} L{w:.1f} {pts[-1][1]:.1f} L{w:.1f} {h:.1f} Z" fill="{colour}"/>'
    out.append(ridge(floor - 120, 160, "#9cc2b4", 1))
    out.append(ridge(floor - 30, 110, "#7fb36a", 2))
    out.append(_rect(0, floor, w, h - floor, "#6fa85a"))
    for _ in range(int(w / 260)):
        tx = rng.uniform(0, w)
        if abs(tx - cx) < 0.22 * min(fw, 1920):
            continue
        ty, k = floor + rng.uniform(-40, 10), rng.uniform(0.8, 1.2)
        out.append(_rect(tx - 12 * k, ty - 120 * k, 24 * k, 120 * k, "#7a5236", _outline(3)) +
                   f'<circle cx="{tx:.1f}" cy="{ty - 160 * k:.1f}" r="{70 * k:.1f}" fill="#4c9a57" {_outline(3)}/>'
                   f'<circle cx="{tx - 30 * k:.1f}" cy="{ty - 130 * k:.1f}" r="{45 * k:.1f}" fill="#4c9a57"/>')
    for _ in range(int(w / 50)):
        gx, gy = rng.uniform(0, w), rng.uniform(floor + 30, h)
        out.append(f'<path d="M{gx:.1f} {gy:.1f} l-6 -16 M{gx:.1f} {gy:.1f} l6 -18" stroke="#4f8a40" '
                   f'stroke-width="4" stroke-linecap="round"/>')
    return "".join(out)


def night(w: float, h: float, floor: float, fw: float, fh: float) -> str:
    rng = random.Random(7)
    cx = w / 2
    out = [_sky(w, h, "#0f1a3d", "#33437a")]
    for _ in range(int(w * floor / 9000)):
        out.append(f'<circle cx="{rng.uniform(0, w):.1f}" cy="{rng.uniform(0, floor):.1f}" '
                   f'r="{rng.uniform(1.2, 3.2):.1f}" fill="#fff8e0" opacity="{rng.uniform(0.5, 1):.2f}"/>')
    mx, my, mr = cx + 0.3 * fw, floor - 500, 110
    out.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{mr * 1.5:.1f}" fill="#fff2c4" opacity="0.12"/>'
               f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="{mr:.1f}" fill="#f6ecc8" {_outline(4)}/>')
    for dx, dy, r in ((-40, -30, 22), (30, 20, 16), (-10, 50, 12), (50, -40, 10)):
        out.append(f'<circle cx="{mx + dx:.1f}" cy="{my + dy:.1f}" r="{r}" fill="#e2d6aa"/>')
    x = 0.0
    while x < w:
        bw, bh = rng.uniform(120, 220), rng.uniform(140, 260)
        out.append(_rect(x, floor - bh, bw, bh + 4, "#1a2240"))
        out.append(f'<path d="M{x - 6:.1f} {floor - bh:.1f} L{x + bw / 2:.1f} {floor - bh - 70:.1f} '
                   f'L{x + bw + 6:.1f} {floor - bh:.1f} Z" fill="#1a2240"/>')
        if rng.random() < 0.6:
            out.append(_rect(x + bw * 0.4, floor - bh + 40, 26, 34, "#f2c45a"))
        x += bw
    out.append(_rect(0, floor, w, h - floor, "#26304f"))
    # a cobbled square in the moonlight: rows of stones, a lit path, lamp glows
    y, step = floor + 16, 26.0
    while y < h:
        out.append(_line(0, y, w, y, "#1f2843", 3))
        out += [_line(x, y, x, y + step, "#1f2843", 3) for x in np.arange(rng.uniform(0, 80), w, step * 2.6)]
        y += step
        step *= 1.12
    out.append(f'<path d="M{cx - 0.08 * fw:.1f} {floor:.1f} L{cx + 0.08 * fw:.1f} {floor:.1f} '
               f'L{cx + 0.45 * fw:.1f} {h:.1f} L{cx - 0.45 * fw:.1f} {h:.1f} Z" fill="#f6ecc8" opacity="0.07"/>')
    for lx in (cx - 0.42 * fw, cx + 0.45 * fw, cx - 1.1 * fw, cx + 1.1 * fw):
        out.append(f'<ellipse cx="{lx:.1f}" cy="{floor + 30:.1f}" rx="120" ry="26" fill="#f2c45a" opacity="0.18"/>'
                   + _rect(lx - 6, floor - 380, 12, 410, "#10162e", _outline(2))
                   + f'<circle cx="{lx:.1f}" cy="{floor - 390:.1f}" r="22" fill="#f2c45a" {_outline(3)}/>'
                   f'<circle cx="{lx:.1f}" cy="{floor - 390:.1f}" r="60" fill="#f2c45a" opacity="0.15"/>')
    return "".join(out)


def idea(w: float, h: float, floor: float, fw: float, fh: float) -> str:
    cx, cy = w / 2, floor - 420
    out = [_sky(w, h, "#ffcf6b", "#ff9c6b")]
    for i in range(16):
        a = math.radians(i * 22.5)
        far = max(w, h) * 1.5
        out.append(f'<path d="M{cx:.1f} {cy:.1f} L{cx + math.cos(a) * far:.1f} {cy + math.sin(a) * far:.1f} '
                   f'L{cx + math.cos(a + 0.2) * far:.1f} {cy + math.sin(a + 0.2) * far:.1f} Z" '
                   f'fill="#ffffff" opacity="0.10"/>')
    rng = random.Random(5)
    for _ in range(int(w / 200)):
        out.append(f'<circle cx="{rng.uniform(0, w):.1f}" cy="{rng.uniform(floor - 700, floor):.1f}" '
                   f'r="{rng.uniform(20, 70):.1f}" fill="#ffffff" opacity="0.14"/>')
    out.append(_rect(0, floor, w, h - floor, "#f08a5d", 'opacity="0.55"'))
    # a round stage under the actors, with rings, and a scatter of small shapes on the floor
    for k, (rx, op) in enumerate(((0.42, 0.18), (0.32, 0.22), (0.22, 0.28))):
        out.append(f'<ellipse cx="{cx:.1f}" cy="{floor + 90 + k * 6:.1f}" rx="{rx * min(fw, 1920):.1f}" '
                   f'ry="{70 - k * 14:.1f}" fill="#ffffff" opacity="{op}"/>')
    for _ in range(int(w * (h - floor) / 40000)):
        x, y = rng.uniform(0, w), rng.uniform(floor + 150, h)
        shape = rng.choice(["circle", "plus", "tri"])
        col = rng.choice(["#ffffff", "#ffd36e", "#e8604c"])
        if shape == "circle":
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{rng.uniform(6, 14):.1f}" fill="{col}" opacity="0.35"/>')
        elif shape == "plus":
            out.append(f'<path d="M{x - 12:.1f} {y:.1f} L{x + 12:.1f} {y:.1f} '
                       f'M{x:.1f} {y - 12:.1f} L{x:.1f} {y + 12:.1f}" '
                       f'stroke="{col}" stroke-width="5" stroke-linecap="round" opacity="0.35"/>')
        else:
            out.append(f'<path d="M{x:.1f} {y - 12:.1f} L{x + 12:.1f} {y + 9:.1f} L{x - 12:.1f} {y + 9:.1f} Z" '
                       f'fill="{col}" opacity="0.3"/>')
    return "".join(out)


PLACES = {"study": study, "church": church, "town": town, "hills": hills, "night": night, "idea": idea}
BUILT_IN = {
    "study": "A study or living room: a window with curtains, a bookshelf, a rug. Home, school, reading, planning.",
    "church": "A church or cathedral inside: stone, columns, stained glass. Religion, the Middle Ages, councils.",
    "town": "A town street with colourful houses and a lamp post. Everyday life, cities, people.",
    "hills": "Countryside: green hills, trees, the sun. Nature, travel, a homeland, farming.",
    "night": "A night sky over rooftops with a big moon. Astronomy, the moon, night, sleep.",
    "idea": "A warm abstract colour field with rays. Ideas, numbers, explanations, anything without a place.",
}


def place_image(name: str, width: int, height: int, svg: str | None = None) -> Image.Image:
    """A place for frames of width x height, at picture_size(): drawn by code, or from a stored drawing."""
    pw, ph = picture_size(width, height)
    if svg:
        inner = svg[svg.index(">", svg.index("<svg")) + 1: svg.rindex("</svg>")]
        view = re_view(svg) or (WIDE_VIEW if width >= height else TALL_VIEW)
        body = (f'<svg x="0" y="0" width="{pw}" height="{ph}" viewBox="0 0 {view[0]} {view[1]}" '
                f'preserveAspectRatio="xMidYMid slice">{inner}</svg>')
    else:
        body = PLACES.get(name, idea)(pw, ph, ph * FLOOR, width, height)
    return art.rasterize(ink.svg_doc(body, pw, ph, (0, 0, pw, ph)), ph).convert("RGB")


def re_view(svg: str) -> tuple[float, float] | None:
    import re

    m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', svg)
    return (float(m.group(1)), float(m.group(2))) if m else None


@lru_cache(maxsize=48)
def preview(name: str, tall: bool, size: int, svg: str | None = None) -> bytes:
    """What the camera sees of a place when it rests in the middle, as PNG `size` pixels wide."""
    fw, fh = (1080, 1920) if tall else (1920, 1080)
    picture = place_image(name, fw, fh, svg)
    x0 = (picture.width - fw) // 2
    y0 = int(picture.height * FLOOR - fh * (0.55 if tall else 0.75))
    view = picture.crop((x0, y0, x0 + fw, y0 + fh)).resize((size, round(size * fh / fw)), Image.LANCZOS)
    out = io.BytesIO()
    view.save(out, format="PNG", optimize=True)
    return out.getvalue()
