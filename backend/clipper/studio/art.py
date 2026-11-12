"""The channel's look, drawn by code: palette, the stick-figure cast, backgrounds, props.

Everything is vector. Characters are SVG built here from a pose, an expression
and a mouth shape; props are SVG drawn once by Claude in the same house style
and kept in the database. resvg turns SVG into pixels, and the results are
cached, since a video reuses the same few drawings thousands of times.
"""

import io
import math
import re
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

INK = "#1b1b1b"
PAPER = "#fbf7ee"
PALETTE = {
    "ink": INK, "paper": PAPER, "white": "#ffffff", "yellow": "#ffd400", "blue": "#4a90d9",
    "light_blue": "#cfe8ff", "red": "#e4572e", "green": "#5bb36a", "orange": "#f29e4c",
    "purple": "#8e6cc6", "pink": "#f4a7b9", "brown": "#9a6b4f", "grey": "#9aa0a6", "light_grey": "#e3e1dc",
}
STROKE = 12          # outline width in a 512-unit drawing

POSES = ("stand", "point_left", "point_right", "shrug", "arms_up", "think", "wave", "facepalm")
EXPRESSIONS = ("neutral", "happy", "surprised", "worried", "smug", "deadpan", "angry", "sad")
CHARACTERS = {
    "narrator": "The narrator: glasses, the channel's regular",
    "person": "Anyone",
    "scientist": "Glasses and wild hair",
    "worker": "Yellow hard hat",
    "royal": "A crown",
    "fancy": "Top hat, for anyone from the 1800s",
    "kid": "Smaller, with a cap",
    "chef": "Chef's hat",
}
BACKGROUNDS = ("paper", "grid", "floor", "sky", "yellow", "blue")


# --- SVG to pixels -------------------------------------------------------------------

def rasterize(svg: str, height: int) -> Image.Image:
    """Render SVG to an RGBA image `height` pixels tall."""
    import resvg_py

    png = resvg_py.svg_to_bytes(svg_string=svg, height=max(8, int(height)), skip_system_fonts=True)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


SVG_FORBIDDEN = re.compile(r"<\s*(script|image|foreignObject|text|style|use)\b|href\s*=|on\w+\s*=|url\(", re.I)


def check_prop_svg(svg: str) -> str:
    """Raise ValueError unless the SVG is a self-contained drawing we can render. Returns it trimmed."""
    svg = svg.strip()
    start = svg.find("<svg")
    if start < 0 or not svg.rstrip().endswith("</svg>"):
        raise ValueError("Not an SVG document")
    svg = svg[start:]
    if SVG_FORBIDDEN.search(svg):
        raise ValueError("Uses scripts, text, styles, links or external images, which the house style forbids")
    if "viewBox" not in svg:
        raise ValueError("Missing viewBox")
    if len(svg) > 40_000:
        raise ValueError("Too complex: keep it under 40,000 characters")
    image = rasterize(svg, 128)
    if image.getbbox() is None:
        raise ValueError("Renders as an empty picture")
    return svg


def placeholder_svg(name: str) -> str:
    """Shown while a prop has not been drawn yet."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
            f'<rect x="56" y="56" width="400" height="400" rx="40" fill="{PALETTE["light_grey"]}" '
            f'stroke="{INK}" stroke-width="{STROKE}" stroke-dasharray="36 24"/>'
            f'<path d="M200 200 L312 312 M312 200 L200 312" stroke="{INK}" stroke-width="{STROKE}" '
            'stroke-linecap="round"/></svg>')


# --- the cast ------------------------------------------------------------------------------

# Arm polylines from the shoulder (150, 215), in a 300 x 520 drawing.
ARMS = {
    "stand": ([(150, 215), (108, 312)], [(150, 215), (192, 312)]),
    "point_left": ([(150, 215), (70, 200), (20, 178)], [(150, 215), (192, 312)]),
    "point_right": ([(150, 215), (108, 312)], [(150, 215), (230, 200), (280, 178)]),
    "shrug": ([(150, 215), (92, 255), (64, 205)], [(150, 215), (208, 255), (236, 205)]),
    "arms_up": ([(150, 215), (84, 172), (44, 104)], [(150, 215), (216, 172), (256, 104)]),
    "think": ([(150, 215), (108, 312)], [(150, 215), (212, 255), (178, 172)]),
    "wave": ([(150, 215), (108, 312)], [(150, 215), (222, 178), (240, 92)]),
    "facepalm": ([(150, 215), (108, 312)], [(150, 215), (205, 248), (168, 118)]),
}

BROWS = {
    "neutral": "", "happy": "M108 70 Q125 60 142 68 M158 68 Q175 60 192 70",
    "surprised": "M106 58 Q125 44 144 56 M156 56 Q175 44 194 58",
    "worried": "M110 76 L142 64 M158 64 L190 76", "smug": "M108 72 L142 72 M158 64 L192 70",
    "deadpan": "M108 80 L142 80 M158 80 L192 80", "angry": "M108 62 L142 78 M158 78 L192 62",
    "sad": "M110 74 L142 64 M158 64 L190 74",
}
MOUTHS = {   # closed mouth per expression
    "neutral": "M128 148 L172 148", "happy": "M120 140 Q150 172 180 140",
    "surprised": None, "worried": "M124 156 Q137 146 150 156 Q163 166 176 154",
    "smug": "M126 150 Q160 160 180 138", "deadpan": "M134 150 L166 150",
    "angry": "M124 160 Q150 140 176 160", "sad": "M124 162 Q150 142 176 162",
}


def _polyline(points) -> str:
    return " ".join(("M" if i == 0 else "L") + f"{x} {y}" for i, (x, y) in enumerate(points))


def character_svg(variant: str = "narrator", pose: str = "stand", expression: str = "neutral",
                  mouth: int = 0, blink: bool = False, facing: str = "right") -> str:
    """A stick figure. `mouth` is 0 (closed), 1 or 2 (open) for talking; `facing` mirrors it."""
    pose = pose if pose in ARMS else "stand"
    expression = expression if expression in BROWS else "neutral"
    s = f'stroke="{INK}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round" fill="none"'
    parts = []
    left, right = ARMS[pose]
    hand_on_face = pose in ("think", "facepalm")   # that arm is drawn over the head
    parts.append(f'<path d="M150 180 L150 340 M150 340 L108 472 M150 340 L192 472 '
                 f'{_polyline(left)} {"" if hand_on_face else _polyline(right)}" {s}/>')
    parts.append(f'<circle cx="{left[-1][0]}" cy="{left[-1][1]}" r="9" fill="{INK}"/>')
    if not hand_on_face:
        parts.append(f'<circle cx="{right[-1][0]}" cy="{right[-1][1]}" r="9" fill="{INK}"/>')
    parts.append(f'<circle cx="150" cy="110" r="70" fill="#ffffff" stroke="{INK}" stroke-width="10"/>')
    # Eyes
    if blink or expression == "deadpan":
        parts.append(f'<path d="M116 102 L134 102 M166 102 L184 102" {s}/>')
    else:
        r = 11 if expression == "surprised" else 8
        parts.append(f'<circle cx="125" cy="100" r="{r}" fill="{INK}"/>'
                     f'<circle cx="175" cy="100" r="{r}" fill="{INK}"/>')
    if BROWS[expression]:
        parts.append(f'<path d="{BROWS[expression]}" {s.replace("10", "8", 1)}/>')
    # Mouth
    if mouth or expression == "surprised":
        h = {0: 10, 1: 12, 2: 20}[mouth] if expression != "surprised" else 14 + 4 * mouth
        w = 20 if expression != "happy" else 26
        parts.append(f'<ellipse cx="150" cy="150" rx="{w}" ry="{h}" fill="{INK}"/>')
    else:
        parts.append(f'<path d="{MOUTHS[expression]}" {s}/>')
    parts.append(_costume(variant))
    if hand_on_face:
        parts.append(f'<path d="{_polyline(right)}" {s}/>'
                     f'<circle cx="{right[-1][0]}" cy="{right[-1][1]}" r="11" fill="{INK}"/>')
    body = "".join(parts)
    if facing == "left":
        body = f'<g transform="translate(300 0) scale(-1 1)">{body}</g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 520">{body}</svg>'


def _costume(variant: str) -> str:
    ink = f'stroke="{INK}" stroke-width="10" stroke-linejoin="round" stroke-linecap="round"'
    glasses = (f'<rect x="100" y="84" width="46" height="34" rx="8" fill="none" {ink}/>'
               f'<rect x="154" y="84" width="46" height="34" rx="8" fill="none" {ink}/>'
               f'<path d="M146 98 L154 98" {ink}/>')
    if variant == "narrator":
        return glasses
    if variant == "scientist":
        hair = (f'<path d="M86 84 L70 52 L104 62 L100 26 L132 50 L150 16 L168 50 L200 26 L196 62 L230 52 L214 84" '
                f'fill="#ffffff" {ink}/>')
        return hair + glasses
    if variant == "worker":
        return (f'<path d="M78 92 Q80 30 150 28 Q220 30 222 92 Z" fill="{PALETTE["yellow"]}" {ink}/>'
                f'<path d="M64 92 L236 92" {ink}/>')
    if variant == "royal":
        return (f'<path d="M96 56 L96 6 L122 32 L150 0 L178 32 L204 6 L204 56 Z" fill="{PALETTE["yellow"]}" {ink}/>')
    if variant == "fancy":
        return (f'<rect x="104" y="0" width="92" height="56" fill="{INK}"/>'
                f'<path d="M74 58 L226 58" {ink.replace("10", "14", 1)}/>')
    if variant == "kid":
        return (f'<path d="M84 76 Q88 28 150 26 Q212 28 216 76 Z" fill="{PALETTE["red"]}" {ink}/>'
                f'<path d="M210 74 L262 80" {ink.replace("10", "14", 1)}/>')
    if variant == "chef":
        return (f'<path d="M100 58 Q76 18 112 10 Q128 -12 150 6 Q172 -12 188 10 Q224 18 200 58 Z" '
                f'fill="#ffffff" {ink}/>')
    return ""


# --- backgrounds ---------------------------------------------------------------------------

@lru_cache(maxsize=32)
def background(name: str, width: int, height: int, floor_y: float = 0.62) -> Image.Image:
    """A full-frame background, drawn with Pillow."""
    if name == "yellow":
        return Image.new("RGBA", (width, height), PALETTE["yellow"])
    if name == "blue":
        return Image.new("RGBA", (width, height), PALETTE["blue"])
    image = Image.new("RGBA", (width, height), PALETTE["light_blue"] if name == "sky" else PAPER)
    draw = ImageDraw.Draw(image)
    if name == "grid":
        step = width // 18
        for x in range(0, width, step):
            draw.line((x, 0, x, height), fill="#e7e1d2", width=2)
        for y in range(0, height, step):
            draw.line((0, y, width, y), fill="#e7e1d2", width=2)
    elif name == "floor":
        y = int(height * floor_y)
        draw.rectangle((0, y, width, height), fill="#efe7d6")
        draw.line((0, y, width, y), fill=INK, width=6)
    elif name == "sky":
        for cx, cy, scale in ((0.2, 0.12, 1.0), (0.75, 0.22, 0.8), (0.45, 0.5, 0.6)):
            x, y, r = cx * width, cy * height, 70 * scale * width / 1080
            for dx, dy, k in ((0, 0, 1.0), (-1.1, 0.25, 0.75), (1.1, 0.25, 0.8)):
                rr = r * k
                draw.ellipse((x + dx * r - rr, y + dy * r - rr, x + dx * r + rr, y + dy * r + rr), fill="#ffffff")
    return image


def soft_shadow(image: Image.Image) -> Image.Image:
    """A faint drop shadow under a sprite, so it sits on the page."""
    alpha = image.getchannel("A").point(lambda a: int(a * 0.18))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(6)))
    out = Image.new("RGBA", (image.width + 12, image.height + 12), (0, 0, 0, 0))
    out.alpha_composite(shadow, (10, 12))
    out.alpha_composite(image, (0, 0))
    return out


def ease_out_back(x: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


def wobble(t: float, seed: int, amount: float) -> float:
    return amount * math.sin(t * 2 * math.pi * 0.5 + seed)
