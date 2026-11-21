"""Draws the film style: a dark cork wall, things pinned on it, one travelling camera and a spotlight.

Every thing is drawn once as a sprite in world units (`S` pixels per unit,
with smaller copies for wide shots) and placed each frame with one affine
warp, so the camera can glide, zoom and lean freely. The camera is planned by
code from the beat lines: each line has one hero, the camera lands on it just
after it appears, holds with a slow push-in, and whips to the next cluster.
A spotlight keeps the hero lit and dims the rest of the wall.

World units are output pixels at zoom 1.
"""

import math
import re
import zlib
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .. import captions as captions_mod
from .. import config
from ..settings import Settings, hex_to_rgb, resolve_font
from . import animate, film, geo, photos

FPS = 24
S = 1.25                          # sprite pixels per world unit (the camera rarely zooms past 1.25)
FONTS = config.FONTS / "film"
CREAM = (244, 238, 226)
INK = (30, 26, 22)
PAPER = (241, 236, 224)
CARD = (247, 243, 231)
HILITE = (247, 204, 62)
SEA = (200, 216, 222)
RED = (190, 36, 32)
STRING = (168, 26, 26)
COL_W, GAP = 1000.0, 150.0         # a cluster is two columns of things
TAIL = 2.4                          # seconds after the voice: the camera pulls back to the whole wall


@dataclass(frozen=True)
class Look:
    """How the wall is drawn. A look never changes the plan."""
    name: str
    light: bool              # a light board: dark type, the spotlight dims towards grey
    type: tuple              # big type
    number: tuple            # counters
    caption: tuple
    pins: bool               # prints pinned (evidence) or laid down (a table)
    spot: float              # how much the spotlight dims, times the plan's strength
    grade: tuple


LOOKS = {
    "wall": Look("wall", False, CREAM, HILITE, CREAM, True, 1.0, (1.03, 1.0, 0.93)),
    "studio": Look("studio", True, INK, RED, INK, False, 0.55, (1.0, 1.0, 1.0)),
}


def get_look(name: str | None) -> Look:
    return LOOKS.get(name or "wall", LOOKS["wall"])


@dataclass
class Thing:
    id: str
    kind: str                      # print | big | count | card | label | highlight | mark | stamp
    line: film.Line
    born: float
    cluster: int
    w: float = 0.0
    h: float = 0.0
    x: float = 0.0
    y: float = 0.0
    rot: float = 0.0
    order: int = 0
    target: "Thing | None" = None
    local: tuple[float, float, float, float] = (0, 0, 0, 0)   # marks: box in the target's frame
    extra: dict = field(default_factory=dict)

    def box(self) -> tuple[float, float, float, float]:
        pad = self.extra.get("below", 0.0)
        return self.x - self.w / 2, self.y - self.h / 2, self.x + self.w / 2, self.y + self.h / 2 + pad

    def pin(self) -> tuple[float, float]:
        """Where a string ties on: the pin at the top."""
        dx, dy = 0.0, -self.h / 2 + 30
        a = math.radians(self.rot)
        return self.x + dx * math.cos(a) - dy * math.sin(a), self.y + dx * math.sin(a) + dy * math.cos(a)


def _ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * p * (p * (p * 6 - 15) + 10)


def _out(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


def _seed(*parts) -> int:
    return zlib.crc32(repr(parts).encode()) & 0xFFFFFFFF


def _font(name: str, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), max(4, int(round(size))))


def _premul(rgba: np.ndarray) -> np.ndarray:
    out = rgba.astype(np.float32)
    out[..., :3] *= out[..., 3:4] / 255.0
    return np.clip(out, 0, 255).astype(np.uint8)


def _mips(sprite: np.ndarray) -> list[np.ndarray]:
    levels = [sprite]
    while min(levels[-1].shape[:2]) > 24 and len(levels) < 7:
        h, w = levels[-1].shape[:2]
        levels.append(cv2.resize(levels[-1], (max(1, w // 2), max(1, h // 2)), interpolation=cv2.INTER_AREA))
    return levels


def _rot(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]], np.float64)


# --- the wall ----------------------------------------------------------------------------------

def _band(rng: np.random.Generator, size: int, sigma: float) -> np.ndarray:
    """Periodic smooth noise (it tiles), unit variance."""
    f = np.fft.fftfreq(size)
    fx, fy = np.meshgrid(f, f)
    g = np.exp(-(fx ** 2 + fy ** 2) * (2 * np.pi * sigma) ** 2 / 2)
    out = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((size, size))) * g))
    return out / (out.std() + 1e-9)


def cork_tile(size: int = 1024, seed: int = 3) -> np.ndarray:
    """A tile of dark, warm cork board that repeats seamlessly (RGB float32)."""
    rng = np.random.default_rng(seed)
    mottle, grain, fine, flecks = (_band(rng, size, s) for s in (60, 3.0, 1.0, 1.6))
    lum = 1 + 0.08 * mottle + 0.11 * np.tanh(1.6 * grain) + 0.05 * fine
    lum -= np.clip(-fine - 1.4, 0, 1.2) * 0.35            # small dark pits
    lum += np.clip(flecks - 2.0, 0, 1.0) * 0.30            # lighter granules
    base = np.array([108, 76, 50], np.float32)
    warm = np.array([1.0, 0.97, 0.9], np.float32)
    tile = base * lum[..., None].astype(np.float32) * (1 + 0.04 * np.tanh(grain)[..., None] * (warm - 1) * 4)
    return np.clip(tile, 0, 255).astype(np.float32)


def studio_tile(size: int = 1024, seed: int = 4) -> np.ndarray:
    """A light seamless backdrop with the faintest paper grain (it tiles)."""
    rng = np.random.default_rng(seed)
    mottle, fine = _band(rng, size, 70), _band(rng, size, 1.2)
    lum = 1 + 0.018 * mottle + 0.012 * fine
    base = np.array([228, 224, 216], np.float32)
    return np.clip(base * lum[..., None].astype(np.float32), 0, 255).astype(np.float32)


# --- sprites -----------------------------------------------------------------------------------

def _shadowed(image: Image.Image, margin: int, offset: tuple[int, int], blur: float, opacity: float) -> Image.Image:
    """The picture on a transparent canvas with a soft drop shadow under it."""
    w, h = image.size
    canvas = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    alpha = image.getchannel("A") if image.mode == "RGBA" else Image.new("L", image.size, 255)
    shadow = Image.new("L", canvas.size, 0)
    shadow.paste(alpha, (margin + offset[0], margin + offset[1]))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * opacity))
    dark = Image.new("RGBA", canvas.size, (8, 5, 3, 0))
    dark.putalpha(shadow)
    canvas = Image.alpha_composite(canvas, dark)
    canvas.alpha_composite(image.convert("RGBA"), (margin, margin))
    return canvas


def _pin(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, colour=(200, 40, 36)) -> None:
    draw.ellipse((cx - r + r * 0.25, cy - r + r * 0.35, cx + r + r * 0.25, cy + r + r * 0.35), fill=(0, 0, 0, 90))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour + (255,))
    k = r * 0.38
    draw.ellipse((cx - r * 0.55 - k / 2, cy - r * 0.55 - k / 2, cx - r * 0.55 + k, cy - r * 0.55 + k),
                 fill=(255, 220, 210, 200))


def print_sprite(picture: Image.Image | None, missing: str, width: float, max_h: float,
                 pin: bool = True) -> tuple[Image.Image, float, float, tuple[float, float, float]]:
    """A photo, flag or map as a print with a white border and a pin.

    Returns (sprite, w, h, (border, picture w, picture h)) in world units."""
    if picture is None:
        pw, ph = width, width * 0.72
    else:
        sw, sh = picture.size
        pw = width
        ph = pw * sh / sw
        if ph > max_h:
            ph, pw = max_h, max_h * sw / sh
    border = max(pw, ph) * 0.035
    w, h = pw + 2 * border, ph + 2 * border
    px = lambda v: max(1, int(round(v * S)))      # noqa: E731
    card = Image.new("RGBA", (px(w), px(h)), PAPER + (255,))
    if picture is None:
        draw = ImageDraw.Draw(card)
        font = _font("CourierPrime-Bold.ttf", 46 * S)
        draw.rectangle((px(border), px(border), px(w - border), px(h - border)), fill=(214, 206, 190, 255))
        text = f"PHOTO: {missing.upper()}"[:60]
        draw.text((px(w / 2), px(h / 2)), text, font=font, fill=INK + (255,), anchor="mm")
    else:
        card.paste(picture.convert("RGB").resize((px(pw), px(ph)), Image.LANCZOS), (px(border), px(border)))
    if pin:
        _pin(ImageDraw.Draw(card), px(w / 2), px(30), px(20))
    return _shadowed(card, px(60), (px(12), px(20)), px(14), 0.6 if pin else 0.4), w, h, (border, pw, ph)


def label_sprite(text: str) -> tuple[Image.Image, float, float]:
    """A red name bar with cream type."""
    size = 86
    font = _font("BebasNeue-Regular.ttf", size * S)
    tw = font.getlength(text) / S
    w, h = tw + 2 * 30, size * 1.15
    image = Image.new("RGBA", (int(w * S), int(h * S)), RED + (255,))
    ImageDraw.Draw(image).text((int(30 * S), int(h * S / 2) + int(4 * S)), text, font=font, fill=CREAM + (255,),
                               anchor="lm")
    return _shadowed(image, int(20 * S), (int(5 * S), int(8 * S)), int(6 * S), 0.5), w, h


def _distress(image: Image.Image, seed: int, amount: float = 0.22) -> Image.Image:
    """Worn type: small holes in the ink."""
    rng = np.random.default_rng(seed)
    w, h = image.size
    noise = rng.standard_normal((h // 3 + 1, w // 3 + 1)).astype(np.float32)
    noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)
    keep = np.clip(1 - np.clip(noise - 1.5, 0, 1) * (amount * 4), 0, 1)
    a = np.asarray(image.getchannel("A"), np.float32) * keep
    out = image.copy()
    out.putalpha(Image.fromarray(a.astype(np.uint8)))
    return out


def big_sprite(text: str, bar: bool, seed: int, look: Look = LOOKS["wall"],
               max_w: float = 960) -> tuple[Image.Image, float, float]:
    """Big condensed type: cream on the cork, or ink on a yellow block."""
    size = 190.0
    font = _font("BebasNeue-Regular.ttf", size * S)
    tw = font.getlength(text) / S
    if tw > max_w:
        size *= max_w / tw
        font = _font("BebasNeue-Regular.ttf", size * S)
        tw = font.getlength(text) / S
    padx, pady = size * 0.22, size * 0.1
    w, h = tw + 2 * padx, size * 0.92 + 2 * pady
    image = Image.new("RGBA", (int(w * S), int(h * S)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if bar:
        draw.rectangle((0, 0, image.width, image.height), fill=HILITE + (255,))
    draw.text((image.width / 2, image.height / 2 + size * S * 0.04), text, font=font,
              fill=(INK if bar else look.type) + (255,), anchor="mm")
    image = _distress(image, seed, 0.12 if bar else 0.2)
    return _shadowed(image, int(30 * S), (int(8 * S), int(12 * S)), int(10 * S), 0.25 if look.light else 0.55), w, h


def count_parts(text: str) -> tuple[str, float, str, int, bool]:
    """'$50,000' -> ('$', 50000, '', decimals, commas)."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if m is None:
        return text, 0.0, "", 0, False
    number = m.group(0)
    decimals = len(number.split(".")[1]) if "." in number else 0
    return text[:m.start()], float(number.replace(",", "")), text[m.end():], decimals, "," in number


def count_text(text: str, p: float) -> str:
    prefix, value, suffix, decimals, commas = count_parts(text)
    shown = value * _out(p)
    body = f"{shown:,.{decimals}f}" if commas else f"{shown:.{decimals}f}"
    return f"{prefix}{body}{suffix}"


def count_sprite(text: str, caption: str, seed: int, look: Look = LOOKS["wall"]) -> tuple[Image.Image, float, float]:
    size, csize = 300.0, 64.0
    font = _font("BebasNeue-Regular.ttf", size * S)
    cfont = _font("BebasNeue-Regular.ttf", csize * S)
    tw = font.getlength(text) / S
    cw = cfont.getlength(caption) / S if caption else 0
    w = max(tw, cw, 200) + 60
    h = size * 0.9 + (csize * 1.3 if caption else 0) + 40
    image = Image.new("RGBA", (int(w * S), int(h * S)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((image.width / 2, int((20 + size * 0.45) * S)), text, font=font, fill=look.number + (255,), anchor="mm")
    if caption:
        draw.text((image.width / 2, int((20 + size * 0.9 + csize * 0.62) * S)), caption, font=cfont,
                  fill=look.caption + (255,), anchor="mm")
    image = _distress(image, seed, 0.12)
    return _shadowed(image, int(30 * S), (int(8 * S), int(12 * S)), int(10 * S), 0.25 if look.light else 0.55), w, h


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    out, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.getlength(trial) > width:
            out.append(line)
            line = word
        else:
            line = trial
    if line:
        out.append(line)
    return out or [""]


def card_layout(title: str, lines: list[str]) -> dict:
    """Where a card's title and typed lines go (world units, from the card's top left)."""
    w = 860.0
    tsize, bsize, pad = 92.0, 50.0, 56.0
    body = _font("CourierPrime-Bold.ttf", bsize * S)
    rows: list[tuple[str, float, float]] = []      # text, x, baseline-middle y
    y = pad + 30 + tsize * 0.95 + 34
    for text in lines:
        for row in _wrap(text, body, (w - 2 * pad) * S):
            rows.append((row, pad, y + bsize * 0.5))
            y += bsize * 1.45
    h = max(y + pad * 0.6, 360.0)
    return {"w": w, "h": h, "tsize": tsize, "bsize": bsize, "pad": pad, "title_y": pad + 30 + tsize * 0.48,
            "rows": rows, "font": "CourierPrime-Bold.ttf"}


def card_sprite(title: str, layout: dict, chars: int | None = None, pin: bool = True) -> Image.Image:
    """An index card: title in condensed caps, lines typed. `chars` limits how much is typed so far."""
    w, h = layout["w"], layout["h"]
    image = Image.new("RGBA", (int(w * S), int(h * S)), CARD + (255,))
    draw = ImageDraw.Draw(image)
    rule = layout["title_y"] + layout["tsize"] * 0.62
    y = rule + layout["bsize"] * 1.45
    while y < h - 20:
        draw.line((0, y * S, w * S, y * S), fill=(176, 196, 222, 160), width=max(1, int(2 * S)))
        y += layout["bsize"] * 1.45
    draw.line((0, rule * S, w * S, rule * S), fill=(214, 96, 96, 220), width=max(1, int(3 * S)))
    tfont = _font("BebasNeue-Regular.ttf", layout["tsize"] * S)
    bfont = _font("CourierPrime-Bold.ttf", layout["bsize"] * S)
    left = chars if chars is not None else 10 ** 6
    title_shown = title[:max(0, left)]
    left -= len(title)
    draw.text((layout["pad"] * S, layout["title_y"] * S), title_shown, font=tfont, fill=INK + (255,), anchor="lm")
    for text, x, yy in layout["rows"]:
        if left <= 0:
            break
        draw.text((x * S, yy * S), text[:left], font=bfont, fill=(40, 36, 46, 255), anchor="lm")
        left -= len(text)
    if pin:
        _pin(draw, w * S / 2, 30 * S, 20 * S)
    return _shadowed(image, int(60 * S), (int(12 * S), int(20 * S)), int(14 * S), 0.6 if pin else 0.4)


def doc_layout(header: str, lines: list[str]) -> dict:
    """A typeset page: a header, a date line, grey rows of body copy and one real sentence."""
    w, pad = 900.0, 64.0
    hsize, msize, bsize = 74.0, 36.0, 46.0
    hfont = _font("OldStandard-Bold.ttf", hsize * S)
    body = _font("OldStandard-Regular.ttf", bsize * S)
    heads = _wrap(header.upper(), hfont, (w - 2 * pad) * S)[:2]
    y = pad + 24
    head_rows = []
    for text in heads:
        head_rows.append((text, w / 2, y + hsize * 0.5))
        y += hsize * 1.1
    dateline = lines[0] if len(lines) > 1 else ""
    sentence = lines[-1] if lines else ""
    date_y = y + msize * 0.7 if dateline else None
    y += (msize * 1.4 if dateline else 0) + 30
    rule_y = y
    y += 40
    grey_before = [y + k * bsize * 1.45 for k in range(2)]
    y += 2 * bsize * 1.45
    rows: list[tuple[str, float, float]] = []
    for row in _wrap(f"“{sentence}”", body, (w - 2 * pad) * S)[:6]:
        rows.append((row, pad, y + bsize * 0.5))
        y += bsize * 1.45
    grey_after = [y + k * bsize * 1.45 for k in range(4)]
    y += 4 * bsize * 1.45 + 60
    return {"w": w, "h": y, "pad": pad, "hsize": hsize, "msize": msize, "bsize": bsize, "heads": head_rows,
            "dateline": dateline, "date_y": date_y, "rule_y": rule_y, "grey": grey_before + grey_after,
            "rows": rows, "font": "OldStandard-Regular.ttf", "title_y": head_rows[0][2] if head_rows else pad,
            "tsize": hsize}


def doc_sprite(layout: dict, seed: int, pin: bool = True) -> Image.Image:
    """The page, aged a little, with RECREATION in small type: it is drawn, not a scan."""
    w, h = layout["w"], layout["h"]
    W, H = int(w * S), int(h * S)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    edge = np.clip(1 - 0.10 * (np.hypot((xx - W / 2) / (W / 2), (yy - H / 2) / (H / 2)) - 0.6), 0.86, 1)
    paper = np.array([238, 231, 212], np.float32) * edge[..., None]
    image = Image.fromarray(np.clip(paper, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    draw = ImageDraw.Draw(image)
    hfont = _font("OldStandard-Bold.ttf", layout["hsize"] * S)
    for text, x, y in layout["heads"]:
        draw.text((x * S, y * S), text, font=hfont, fill=(28, 24, 20, 255), anchor="mm")
    if layout["dateline"]:
        mfont = _font("CourierPrime-Regular.ttf", layout["msize"] * S)
        draw.text((w / 2 * S, layout["date_y"] * S), layout["dateline"].upper(), font=mfont, fill=(60, 54, 48, 255),
                  anchor="mm")
    pad = layout["pad"]
    ry = layout["rule_y"]
    draw.line((pad * S, ry * S, (w - pad) * S, ry * S), fill=(40, 36, 30, 255), width=max(1, int(4 * S)))
    draw.line((pad * S, (ry + 9) * S, (w - pad) * S, (ry + 9) * S), fill=(40, 36, 30, 255), width=max(1, int(2 * S)))
    rng = np.random.default_rng(seed)
    bh = layout["bsize"] * 0.42
    for gy in layout["grey"]:
        end = w - pad - rng.uniform(0, 160)
        draw.rounded_rectangle((pad * S, (gy - bh / 2 + 20) * S, end * S, (gy + bh / 2 + 20) * S),
                               radius=int(6 * S), fill=(150, 144, 132, 120))
    body = _font(layout["font"], layout["bsize"] * S)
    for text, x, y in layout["rows"]:
        draw.text((x * S, y * S), text, font=body, fill=(24, 22, 20, 255), anchor="lm")
    small = _font("CourierPrime-Regular.ttf", 24 * S)
    draw.text(((w - pad) * S, (h - 26) * S), "RECREATION", font=small, fill=(120, 112, 100, 255), anchor="rm")
    if pin:
        _pin(draw, w * S / 2, 30 * S, 20 * S)
    return _shadowed(image, int(60 * S), (int(12 * S), int(20 * S)), int(14 * S), 0.6 if pin else 0.4)


def map_point(codes: list[str], code: str) -> tuple[float, float]:
    """Where a country sits on a map print of `codes`, as fractions of the picture."""
    clon, clat, slon, slat = geo.map_view(codes, 4 / 3)
    pts = np.asarray(geo._largest(geo.countries()[code]["rings"]))
    lon, lat = float(pts[:, 0].mean()), float(pts[:, 1].mean())
    fx = (lon - clon) / slon + 0.5
    fy = 0.5 - (lat - clat) / slat
    return min(0.96, max(0.04, fx)), min(0.94, max(0.06, fy))


def map_pin_sprite(label: str, left: bool) -> tuple[Image.Image, float]:
    """A red map pin with a typed label beside it; the pin is at the sprite's centre. Returns (sprite, width)."""
    font = _font("CourierPrime-Bold.ttf", 40 * S)
    tw = font.getlength(label) / S if label else 0
    r = 22.0
    side = r + 14 + (tw + 28 if label else 0)
    w, h = 2 * side + 8, 2 * max(r, 34) + 8
    image = Image.new("RGBA", (int(w * S), int(h * S)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cx, cy = w / 2 * S, h / 2 * S
    if label:
        x0 = cx - (r + 10 + tw + 28) * S if left else cx + (r + 10) * S
        draw.rounded_rectangle((x0, cy - 28 * S, x0 + (tw + 28) * S, cy + 28 * S), radius=int(6 * S),
                               fill=INK + (235,))
        draw.text((x0 + 14 * S, cy + 2 * S), label, font=font, fill=CREAM + (255,), anchor="lm")
    draw.ellipse((cx - (r + 7) * S, cy - (r + 7) * S, cx + (r + 7) * S, cy + (r + 7) * S), fill=(255, 255, 255, 230))
    _pin(draw, cx, cy, r * S, (214, 40, 36))
    return image, w


def stamp_sprite(text: str, width: float, seed: int, colour=RED) -> tuple[Image.Image, float, float]:
    size = 150.0
    font = _font("BebasNeue-Regular.ttf", size * S)
    tw = font.getlength(text) / S
    scale = min(1.6, width / (tw + size * 0.6))
    size *= scale
    font = _font("BebasNeue-Regular.ttf", size * S)
    tw = font.getlength(text) / S
    w, h = tw + size * 0.6, size * 1.25
    image = Image.new("RGBA", (int(w * S), int(h * S)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    line = max(2, int(size * 0.07 * S))
    draw.rectangle((line, line, image.width - line, image.height - line), outline=colour + (255,), width=line)
    inset = line * 3
    draw.rectangle((inset, inset, image.width - inset, image.height - inset), outline=colour + (255,),
                   width=max(1, line // 2))
    draw.text((image.width / 2, image.height / 2 + size * S * 0.05), text, font=font, fill=colour + (255,),
              anchor="mm")
    image = _distress(image, seed, 0.45)
    a = np.asarray(image.getchannel("A"), np.float32) * 0.9
    image.putalpha(Image.fromarray(a.astype(np.uint8)))
    return image, w, h


def stroke_points(kind: str, w: float, h: float, seed: int) -> list[np.ndarray]:
    """A pen mark's strokes around (or across) a box of w x h, centred on 0, in world units."""
    rng = np.random.default_rng(seed)
    if kind == "circle":
        rx, ry = w / 2 + 50, h / 2 + 46
        a0 = rng.uniform(-2.4, -1.8)
        t = np.linspace(a0, a0 + 2 * math.pi + 0.45, 90)
        wob = 1 + 0.035 * np.sin(t * 2 + rng.uniform(0, 6)) + 0.04 * (t - a0) / (2 * math.pi)
        return [np.stack([np.cos(t) * rx * wob, np.sin(t) * ry * wob], 1)]
    if kind == "underline":
        x = np.linspace(-w * 0.46, w * 0.46, 30)
        y = h / 2 + 34 + np.sin(np.linspace(0, math.pi, 30)) * 10 * rng.uniform(-1, 1)
        return [np.stack([x, y], 1)]
    # cross
    a = np.linspace(0, 1, 20)[:, None]
    p0, p1 = np.array([-w * 0.4, -h * 0.4]), np.array([w * 0.4, h * 0.4])
    q0, q1 = np.array([w * 0.4, -h * 0.38]), np.array([-w * 0.38, h * 0.42])
    return [p0 + (p1 - p0) * a, q0 + (q1 - q0) * a]


def mark_sprite(strokes: list[np.ndarray], progress: float, colour, width: float) -> tuple[np.ndarray, tuple]:
    """The strokes drawn as far as `progress`, as a premultiplied sprite, and its world box."""
    pts = np.concatenate(strokes)
    x0, y0 = pts.min(0) - width * 2
    x1, y1 = pts.max(0) + width * 2
    cw, ch = int((x1 - x0) * S) + 2, int((y1 - y0) * S) + 2
    alpha = np.zeros((ch, cw), np.uint8)
    total = len(strokes)
    for i, stroke in enumerate(strokes):
        share = min(1.0, max(0.0, progress * total - i))
        if share <= 0:
            continue
        n = max(2, int(len(stroke) * share))
        pix = ((stroke[:n] - [x0, y0]) * S * 16).astype(np.int32)
        cv2.polylines(alpha, [pix], False, 255, int(width * S), cv2.LINE_AA, shift=4)
    rgba = np.zeros((ch, cw, 4), np.uint8)
    rgba[..., :3] = colour
    rgba[..., 3] = alpha
    return _premul(rgba), (x0, y0, x1, y1)


def highlight_sprite(w: float, h: float, seed: int) -> np.ndarray:
    """A highlighter band with ragged ends; alpha only matters (it multiplies)."""
    rng = np.random.default_rng(seed)
    cw, ch = int(w * S) + 2, int(h * S) + 2
    alpha = np.full((ch, cw), 255, np.float32)
    ragged = cv2.resize(rng.uniform(0.55, 1.0, (ch // 6 + 2, 1)).astype(np.float32), (1, ch))[:, 0]
    edge = int(14 * S)
    for k in range(edge):
        alpha[:, k] *= np.clip((k / edge) / ragged, 0, 1)
        alpha[:, -1 - k] *= np.clip((k / edge) / ragged[::-1], 0, 1)
    alpha *= 0.85 + 0.15 * np.cos(np.linspace(-1.2, 1.2, ch))[:, None]
    rgba = np.zeros((ch, cw, 4), np.uint8)
    rgba[..., :3] = HILITE
    rgba[..., 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    return rgba



# --- the composer ------------------------------------------------------------------------------

@dataclass
class Key:
    t: float              # when the camera arrives
    s: float              # when it starts moving there
    x: float
    y: float
    z: float
    strength: float       # spotlight
    roll: float
    whip: bool = False


class FilmComposer:
    """Draws any frame of a film storyboard."""

    cache_limit = 400 * 1024 * 1024

    def __init__(self, board: dict, props: dict, settings: Settings, mouths: np.ndarray | None = None):
        self.board = board
        self.format = board["format"]
        self.layout = animate.LAYOUTS[self.format]
        self.W, self.H = self.layout.width, self.layout.height
        self.short = self.format == "short"
        self.hero_y = 0.40 if self.short else 0.5          # Shorts keep the hero above the captions
        self.fit = (0.82, 0.46) if self.short else (0.6, 0.72)
        self.words = board["words"]
        self.duration = board["end"] - board["start"]
        self.photos: dict[str, dict] = board.get("photos") or {}
        self.look = get_look(board.get("look"))
        self.tail = TAIL if self.duration > 15 else 0.6
        self.shots = board["scenes"] or [{"start_word": 0, "lines": []}]
        self.captions = None
        if self.layout.captions and settings.captions_enabled and self.words:
            self.captions = captions_mod.CaptionRenderer(captions_mod.group_words(self.words, 0.0, self.duration),
                                                         resolve_font(settings), hex_to_rgb(settings.highlight_color))
        self.things: list[Thing] = []
        self.strings: list[dict] = []
        self.shakes: list[tuple[float, float]] = []
        self.events: list[tuple[float, str]] = []
        self.sprites: dict = {}
        self._build()
        self._layout()
        self._threads()
        self.keys = self._camera_plan()
        tile = studio_tile() if self.look.light else cork_tile()
        self.tiles = [tile]
        while self.tiles[-1].shape[0] > 32:
            t = self.tiles[-1]
            self.tiles.append(cv2.resize(t, (t.shape[1] // 2, t.shape[0] // 2), interpolation=cv2.INTER_AREA))
        rng = np.random.default_rng(9)
        self.grain = []
        for _ in range(4):
            g = np.repeat(rng.normal(0, 2.5 if self.look.light else 4.5, (self.H, self.W, 1)), 3, axis=2)
            self.grain.append((np.clip(g, 0, 255).astype(np.uint8), np.clip(-g, 0, 255).astype(np.uint8)))
        self._mask_grid = np.mgrid[0:self.H // 8, 0:self.W // 8].astype(np.float32) * 8 + 4

    @property
    def total(self) -> float:
        """The video's length: the voice, then the closing pull-back."""
        return self.duration + self.tail

    # --- time ----------------------------------------------------------------------------

    def word_time(self, index: int) -> float:
        if not self.words:
            return 0.0
        return self.words[max(0, min(index, len(self.words) - 1))]["s"]

    # --- the plan --------------------------------------------------------------------------

    def _build(self) -> None:
        by_id: dict[str, Thing] = {}
        latest: Thing | None = None
        order = 0
        self.lines: list[tuple[float, int, film.Line]] = []
        for c, shot in enumerate(self.shots):
            for line in film.parsed(shot):
                t = self.word_time(line.word)
                self.lines.append((t, c, line))
                order += 1
                seed = _seed(line.render())
                if line.verb in film.CREATES:
                    thing = Thing(line.id, line.verb, line, max(0.0, t - 0.08), c, order=order)
                    self._measure(thing, seed)
                    self.things.append(thing)
                    by_id[thing.id] = thing
                    latest = thing
                    if thing.kind in ("photo", "flag", "map") and line.label:
                        lab = Thing(thing.id + ":label", "label", line, t + 0.45, c, order=order)
                        image, lab.w, lab.h = label_sprite(line.label.upper())
                        self.sprites[lab.id] = _mips(_premul(np.asarray(image)))
                        lab.target = thing
                        thing.extra["below"] = lab.h * 0.6
                        self.things.append(lab)
                        self.events.append((lab.born, "label"))
                    continue
                if line.verb in ("pin", "route"):
                    maps = [x for x in by_id.values() if x.kind == "map"]
                    board = by_id.get(line.target) if line.target else (maps[-1] if maps else None)
                    if board is None or board.kind != "map":
                        continue
                    line.target = board.id
                    if line.verb == "pin":
                        self._map_pin(line, board, t, c, order)
                    else:
                        self._route(line, board, t, c, order)
                    continue
                target = by_id.get(line.target) if line.target else latest
                if target is not None and line.verb not in ("string", "look", "wide"):
                    line.target = target.id          # the camera looks at what the line acts on
                if line.verb == "string":
                    a, b = by_id.get(line.target), by_id.get(line.target2)
                    if a and b:
                        self.strings.append({"a": a, "b": b, "born": t - 0.1, "dur": 0.7, "line": line})
                        self.events.append((t - 0.1, "string"))
                    continue
                if line.verb in ("look", "wide") or target is None:
                    continue
                if line.verb == "stamp":
                    st = Thing(f"stamp{order}", "stamp", line, t - 0.04, c, order=order, target=target)
                    image, st.w, st.h = stamp_sprite(line.text.upper(), min(target.w * 0.58, 620), seed)
                    self.sprites[st.id] = _mips(_premul(np.asarray(image)))
                    rng = np.random.default_rng(seed)
                    # on the lower part, where it hides least of what is written
                    low = target.h / 2 - st.h * 0.55 + rng.uniform(-10, 20)
                    if target.kind in ("photo", "flag", "map"):   # above the name bar
                        low = rng.uniform(0.05, 0.2) * target.h
                    st.extra["offset"] = (rng.uniform(0.02, 0.16) * target.w * rng.choice([-1, 1]), low)
                    st.extra["angle"] = rng.choice([-1, 1]) * rng.uniform(5, 10)
                    self.things.append(st)
                    self.shakes.append((t + 0.08, 18.0))
                    self.events.append((t + 0.06, "stamp"))
                elif line.verb == "highlight":
                    if target.kind == "big" and not target.line.bar:
                        target.extra["bar_at"] = t - 0.25                 # the block wipes in behind the type
                        self.events.append((t - 0.25, "marker"))
                        continue
                    if target.kind in ("card", "doc"):
                        boxes = self._phrase_boxes(target, line.text)
                        for k, box in enumerate(boxes):       # a phrase over two rows: one swipe per row
                            hl = Thing(f"hl{order}_{k}", "highlight", line, t - 0.3 + 0.22 * k, c, order=order,
                                       target=target, local=box)
                            hl.w, hl.h = box[2] - box[0], box[3] - box[1]
                            self.sprites[hl.id] = [highlight_sprite(hl.w, hl.h, seed + k)]
                            self.things.append(hl)
                        self.events.append((t - 0.3, "marker"))
                        continue
                    line = film.Line(line.word, "underline", target=line.target)
                    self._mark(line, target, t, c, order, seed)
                else:
                    self._mark(line, target, t, c, order, seed)

    def _mark(self, line: film.Line, target: Thing, t: float, c: int, order: int, seed: int) -> None:
        mk = Thing(f"mk{order}", "mark", line, t - 0.15, c, order=order, target=target)
        mk.extra["strokes"] = stroke_points(line.verb, target.w, target.h, seed)
        light = target.kind in film.PINNED or self.look.light
        mk.extra["colour"] = RED if light or line.verb == "cross" or line.colour == "red" else CREAM
        mk.extra["dur"] = {"circle": 0.55, "underline": 0.35, "cross": 0.45}.get(line.verb, 0.4)
        self.things.append(mk)
        self.events.append((mk.born, "cross" if line.verb == "cross" else "scribble"))

    def _map_pin(self, line: film.Line, board: Thing, t: float, c: int, order: int) -> None:
        border, pw, ph = board.extra["inner"]
        fx, fy = map_point(board.extra["codes"], line.text)
        lx, ly = -board.w / 2 + border + fx * pw, -board.h / 2 + border + fy * ph
        image, width = map_pin_sprite(line.caption.upper(), left=fx > 0.62)
        pin = Thing(f"pin{order}", "mappin", line, t - 0.05, c, order=order, target=board, local=(lx, ly, lx, ly))
        pin.w, pin.h = width, image.height / S
        self.sprites[pin.id] = _mips(_premul(np.asarray(image)))
        self.things.append(pin)
        self.events.append((pin.born, "pin"))

    def _route(self, line: film.Line, board: Thing, t: float, c: int, order: int) -> None:
        border, pw, ph = board.extra["inner"]
        ends = []
        for code in line.lines[:2]:
            fx, fy = map_point(board.extra["codes"], code)
            ends.append(np.array([-board.w / 2 + border + fx * pw, -board.h / 2 + border + fy * ph]))
        a, b = ends
        d = b - a
        normal = np.array([d[1], -d[0]]) / (np.linalg.norm(d) + 1e-6)
        if normal[1] > 0:
            normal = -normal                          # the arc bows upwards
        ctrl = (a + b) / 2 + normal * 0.25 * np.linalg.norm(d)
        u = np.linspace(0, 1, 60)[:, None]
        arc = (1 - u) ** 2 * a + 2 * (1 - u) * u * ctrl + u ** 2 * b
        mk = Thing(f"route{order}", "mark", line, t - 0.2, c, order=order, target=board)
        mk.extra.update(strokes=[arc], colour=RED, dur=1.1, width=11)
        self.things.append(mk)
        self.events.append((mk.born, "marker"))

    def _measure(self, thing: Thing, seed: int) -> None:
        """Size a new thing and draw its sprite (world units)."""
        line = thing.line
        if thing.kind == "photo":
            found = self.photos.get(line.text)
            picture = photos._aged(found["path"]) if found and found.get("path") else None
            image, thing.w, thing.h, _ = print_sprite(picture, line.text, 820, 980, self.look.pins)
            self.events.append((thing.born, "pin"))
        elif thing.kind == "flag":
            flag = geo.flag_image(line.text, int(720 * S), 1.0)
            image, thing.w, thing.h, _ = print_sprite(flag, geo.name(line.text), 720, 700, self.look.pins)
            self.events.append((thing.born, "pin"))
        elif thing.kind == "map":
            codes = [c for c in line.lines if c in geo.countries()]
            picture = None
            if codes:                   # the sea is transparent in the drawing: print it on pale blue
                drawn = geo.map_image(codes, int(940 * S), 1.0)
                picture = Image.new("RGBA", drawn.size, SEA + (255,))
                picture.alpha_composite(drawn.convert("RGBA"))
            image, thing.w, thing.h, inner = print_sprite(picture, "map", 940, 900, self.look.pins)
            thing.extra.update(codes=codes, inner=inner)
            self.events.append((thing.born, "pin"))
        elif thing.kind == "doc":
            layout = doc_layout(line.text, line.lines)
            thing.extra["layout"] = layout
            thing.w, thing.h = layout["w"], layout["h"]
            image = doc_sprite(layout, seed, self.look.pins)
            self.events.append((thing.born, "pin"))
        elif thing.kind == "big":
            image, thing.w, thing.h = big_sprite(line.text.upper(), line.bar, seed, self.look)
            if not line.bar:
                barred, _, _ = big_sprite(line.text.upper(), True, seed, self.look)
                self.sprites[thing.id + ":bar"] = _mips(_premul(np.asarray(barred)))
            self.events.append((thing.born, "impact" if line.bar else "type"))
        elif thing.kind == "count":
            image, thing.w, thing.h = count_sprite(line.text.upper(), line.caption.upper(), seed, self.look)
            thing.extra["seed"] = seed
            self.events.append((thing.born, "ticks"))
        else:      # card
            layout = card_layout(line.text.upper(), line.lines)
            thing.extra["layout"] = layout
            thing.w, thing.h = layout["w"], layout["h"]
            image = card_sprite(line.text.upper(), layout, pin=self.look.pins)
            thing.extra["chars"] = len(line.text) + sum(len(r[0]) for r in layout["rows"])
            self.events.append((thing.born, "pin"))
        self.sprites[thing.id] = _mips(_premul(np.asarray(image.convert("RGBA"))))

    def _phrase_boxes(self, page: Thing, phrase: str) -> list[tuple[float, float, float, float]]:
        """The boxes of a phrase on a card or a page (one per row it spans), in its frame (centre at 0).
        With no phrase, or one not found, the title."""
        layout = page.extra["layout"]
        font = _font(layout["font"], layout["bsize"] * S)
        joined, starts = "", []
        for text, _, _ in layout["rows"]:
            starts.append(len(joined))
            joined += text + " "
        k = joined.lower().find(phrase.lower()) if phrase else -1
        boxes = []
        if k >= 0:
            end = k + len(phrase)
            for (text, x, y), s0 in zip(layout["rows"], starts, strict=True):
                a, b = max(k, s0), min(end, s0 + len(text))
                if a < b:
                    x0 = x + font.getlength(text[:a - s0]) / S
                    x1 = x + font.getlength(text[:b - s0]) / S
                    boxes.append((x0, y - layout["bsize"] * 0.62, x1, y + layout["bsize"] * 0.62))
        if not boxes:
            if page.kind == "doc":
                hfont = _font("OldStandard-Bold.ttf", layout["hsize"] * S)
                text, x, y = layout["heads"][0]
                half = hfont.getlength(text) / S / 2
                boxes = [(x - half, y - layout["hsize"] * 0.55, x + half, y + layout["hsize"] * 0.5)]
            else:
                tfont = _font("BebasNeue-Regular.ttf", layout["tsize"] * S)
                x0 = layout["pad"]
                x1 = x0 + tfont.getlength(page.line.text.upper()) / S
                boxes = [(x0, layout["title_y"] - layout["tsize"] * 0.45, x1,
                          layout["title_y"] + layout["tsize"] * 0.42)]
        pad = 12
        return [(x0 - pad - page.w / 2, y0 - page.h / 2, x1 + pad - page.w / 2, y1 - page.h / 2)
                for x0, y0, x1, y1 in boxes]

    def _layout(self) -> None:
        """Clusters of two columns, the clusters snaking over the wall."""
        boxes = []
        for c in range(len(self.shots)):
            heights = [0.0, 0.0]
            for thing in (x for x in self.things if x.cluster == c and x.kind in film.CREATES):
                col = 0 if heights[0] <= heights[1] else 1
                rng = np.random.default_rng(_seed(thing.id, c))
                tall = thing.h + thing.extra.get("below", 0)
                thing.x = (col - 0.5) * (COL_W + GAP) + rng.uniform(-40, 40)
                thing.y = heights[col] + tall / 2 + rng.uniform(-20, 20) - thing.extra.get("below", 0) / 2
                heights[col] += tall + GAP + rng.uniform(0, 60)
                spin = (2.6 if thing.kind in film.PINNED else 1.2) * (0.5 if self.look.light else 1)
                thing.rot = float(rng.uniform(-spin, spin))
            boxes.append(max(heights) or 400.0)
        n = len(self.shots)
        cols = max(1, math.ceil(math.sqrt(n * (0.75 if self.short else 2.0))))
        cell_w = 2 * COL_W + GAP + 700
        cell_h = max(boxes) + 650
        for c in range(n):
            row, k = divmod(c, cols)
            col = k if row % 2 == 0 else cols - 1 - k           # snake: neighbours stay neighbours
            rng = np.random.default_rng(_seed("cluster", c))
            ox = col * cell_w + rng.uniform(-160, 160)
            oy = row * cell_h + (cell_h - boxes[c]) / 2 - cell_h / 2 + rng.uniform(-120, 120)
            for thing in self.things:
                if thing.cluster == c and thing.kind in film.CREATES:
                    thing.x += ox
                    thing.y += oy
        for thing in self.things:              # things placed on other things follow them
            if thing.target is None:
                continue
            tg = thing.target
            if thing.kind == "label":
                thing.rot = tg.rot * 0.5
                thing.x = tg.x - tg.w / 2 + thing.w / 2 + 34
                thing.y = tg.y + tg.h / 2 - 6
            elif thing.kind == "mappin":
                lx, ly = thing.local[:2]
                dx, dy = _rot(tg.rot) @ np.array([lx, ly])
                thing.x, thing.y, thing.rot = tg.x + dx, tg.y + dy, tg.rot
            elif thing.kind == "stamp":
                ox, oy = thing.extra["offset"]
                thing.x, thing.y = tg.x + ox, tg.y + oy
                thing.rot = tg.rot + thing.extra["angle"]
            else:
                lx0, ly0, lx1, ly1 = thing.local
                cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
                r = _rot(tg.rot)
                dx, dy = r @ np.array([cx, cy])
                thing.x, thing.y, thing.rot = tg.x + dx, tg.y + dy, tg.rot

    def _threads(self) -> None:
        """A red string from each cluster to the next, drawn as the camera travels along it."""
        for c in range(1, len(self.shots)):
            if self.shots[c].get("cut"):
                continue
            before = [x for x in self.things if x.cluster == c - 1 and x.kind in film.CREATES]
            after = [x for x in self.things if x.cluster == c and x.kind in film.CREATES]
            if before and after:
                first = min(after, key=lambda x: x.born)
                born = max(0.0, first.born - 0.9)
                self.strings.append({"a": before[-1], "b": first, "born": born, "dur": 1.0, "line": None})
                self.events.append((born, "string"))

    # --- the camera ---------------------------------------------------------------------------

    def _frame_box(self, box) -> tuple[float, float, float]:
        x0, y0, x1, y1 = box
        bw, bh = max(80.0, x1 - x0), max(80.0, y1 - y0)
        z = min(self.fit[0] * self.W / bw, self.fit[1] * self.H / bh)
        z = max(0.05, min(1.2, z))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + (0.5 - self.hero_y) * self.H / z
        return cx, cy, z

    @staticmethod
    def _union(a, b):
        return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])

    def _camera_plan(self) -> list[Key]:
        by_id = {x.id: x for x in self.things}
        heroes: list[tuple[float, tuple, float, int]] = []      # time, box, spotlight strength, cluster
        for t, c, line in sorted(self.lines, key=lambda x: x[0]):
            v = line.verb
            if v in film.CREATES:
                box, strength = by_id[line.id].box(), 0.55
            elif v == "string":
                a, b = by_id.get(line.target), by_id.get(line.target2)
                if not (a and b):
                    continue
                box, strength = self._union(a.box(), b.box()), 0.3
            elif v == "wide":
                members = [x.box() for x in self.things if x.cluster == c and x.kind in film.CREATES
                           and x.born <= t + 0.5]
                if not members:
                    continue
                box = members[0]
                for m in members[1:]:
                    box = self._union(box, m)
                strength = 0.25
            else:
                target = by_id.get(line.target) if line.target else None
                if target is None:
                    continue
                box, strength = target.box(), 0.6
            heroes.append((t, box, strength, c))
        if not heroes:
            return [Key(0, 0, 0, 0, 0.3, 0.2, 0)]
        merged: list[list] = []
        for t, box, strength, c in heroes:
            if merged and t - merged[-1][0] < 0.6 and merged[-1][3] == c:
                union = self._union(merged[-1][1], box)
                if self._frame_box(union)[2] > 0.55 * self._frame_box(merged[-1][1])[2]:
                    merged[-1][1] = union
                    merged[-1][2] = min(merged[-1][2], strength)
                    continue
            merged.append([t, box, strength, c])
        keys: list[Key] = []
        for i, (t, box, strength, c) in enumerate(merged):
            x, y, z = self._frame_box(box)
            roll = (0.7 if i % 2 else -0.6)
            arrive = t + 0.3
            if not keys:
                keys.append(Key(0.0, 0.0, x, y, z * 0.92, strength, roll))
                continue
            prev = keys[-1]
            new_cluster = c != merged[i - 1][3]
            whip = new_cluster
            if whip:
                arrive = t + 0.05            # a whip lands as the new cluster's first thing does
            screen = math.hypot(x - prev.x, y - prev.y) * min(z, prev.z)
            travel = 1.1 if whip else min(1.3, max(0.6, 0.45 + screen / 1700))
            if whip:            # the string leads the way; the camera lands as the first thing does
                start = max(prev.t + 0.2, arrive - travel)
            else:               # the camera notices the new thing as it appears, then goes to it
                start = max(prev.t + 0.2, t - 0.1)
                arrive = start + travel
            if arrive - start < 0.3:
                arrive = start + 0.3
            keys.append(Key(arrive, start, x, y, z, strength, roll, whip))
        made = [x for x in self.things if x.kind in film.CREATES]
        if self.tail >= 1.5 and made:              # the end: the camera pulls back to the whole wall
            box = made[0].box()
            for x in made:
                box = self._union(box, x.box())
            x, y, z = self._frame_box(box)
            start = max(keys[-1].t + 0.4, self.duration - 0.5)
            arrive = max(start + 1.2, self.total - 0.3)
            keys.append(Key(arrive, start, x, y + (self.hero_y - 0.5) * self.H / z * 0.6, z * 0.95,
                            0.06, 0.0, True))
        for key in keys:
            if key.whip and key.s > 0:
                self.events.append((key.s, "pan"))
        return keys

    def _hold(self, k: int, t: float) -> tuple[float, float, float, float, float]:
        key = self.keys[k]
        held = max(0.0, t - key.t)
        z = key.z * min(1.07, 1 + 0.028 * held)               # a slow push-in while it holds
        return key.x, key.y, z, key.strength, key.roll

    def camera(self, t: float) -> tuple[float, float, float, float, float]:
        """(x, y, zoom, roll in degrees, spotlight strength) at time t."""
        k = 0
        while k + 1 < len(self.keys) and self.keys[k + 1].s <= t:
            k += 1
        key = self.keys[k]
        if k == 0 or t >= key.t:
            x, y, z, strength, roll = self._hold(k, t)
        else:
            ax, ay, az, astr, aroll = self._hold(k - 1, key.s)
            p = _ease((t - key.s) / max(1e-3, key.t - key.s))
            x, y = ax + (key.x - ax) * p, ay + (key.y - ay) * p
            lz = math.log(az) + (math.log(key.z) - math.log(az)) * p
            # a long move pulls back on the way, so the wall reads as one place
            dist = math.hypot(key.x - ax, key.y - ay)
            if key.whip and dist * min(az, key.z) > self.W * 0.5:
                fit = max(0.05, min(az, key.z, self.fit[0] * self.W / (dist + 1200)))
                mid = (math.log(az) + math.log(key.z)) / 2
                lz += math.sin(math.pi * p) * min(0.0, math.log(fit) - mid) * 0.8
            z = math.exp(lz)
            strength = astr + (key.strength - astr) * p
            roll = aroll + (key.roll - aroll) * p
        # handheld drift and stamp shakes, in screen pixels
        dx = 7 * math.sin(0.8 * t) + 3 * math.sin(2.1 * t + 1.3)
        dy = 5 * math.sin(0.6 * t + 0.7) + 2.5 * math.sin(1.7 * t + 2.1)
        for at, amp in self.shakes:
            age = t - at
            if 0 <= age < 0.4:
                dy += amp * math.exp(-age * 12) * math.sin(age * 75)
                dx += amp * 0.5 * math.exp(-age * 12) * math.sin(age * 61 + 1)
        roll += 0.45 * math.sin(0.31 * t) + 0.2 * math.sin(0.97 * t + 1)
        return x + dx / z, y + dy / z, z, roll, strength

    # --- drawing ------------------------------------------------------------------------------

    def _view(self, cx: float, cy: float, z: float, roll: float):
        """World -> screen as (A, b): screen = A @ world + b."""
        A = z * _rot(roll)
        b = np.array([self.W / 2, self.H / 2]) - A @ np.array([cx, cy])
        return A, b

    def _background(self, cx: float, cy: float, z: float, roll: float) -> np.ndarray:
        m = int(max(0, min(len(self.tiles) - 1, math.floor(math.log2(1 / z)) if z < 1 else 0)))
        tile = self.tiles[m]
        period = tile.shape[0] * 2 ** m
        ax = (cx % period) / 2 ** m
        ay = (cy % period) / 2 ** m
        inv = _rot(-roll) / (z * 2 ** m)
        b = np.array([ax, ay]) - inv @ np.array([self.W / 2, self.H / 2])
        M = np.hstack([inv, b[:, None]]).astype(np.float64)
        return cv2.warpAffine(tile, M, (self.W, self.H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_WRAP)

    def _paste(self, frame: np.ndarray, levels: list[np.ndarray], view, x: float, y: float, rot: float,
               scale: float = 1.0, alpha: float = 1.0, reveal: float = 1.0, multiply=None) -> None:
        A, b = view
        z = math.hypot(A[0, 0], A[1, 0])
        need = z * scale / S
        m = 0
        while m + 1 < len(levels) and need * 2 ** (m + 1) <= 1.0:
            m += 1
        sprite = levels[m]
        sm = S / 2 ** m
        h, w = sprite.shape[:2]
        L = (A @ _rot(rot)) * (scale / sm)
        sc = np.array([w / 2, h / 2])
        t = A @ np.array([x, y]) + b - L @ sc
        if reveal < 1:
            sprite = sprite[:, :max(1, int(w * reveal))]
        hh, ww = sprite.shape[:2]
        corners = np.array([[0, 0], [ww, 0], [0, hh], [ww, hh]], np.float64) @ L.T + t
        x0, y0 = np.floor(corners.min(0)).astype(int) - 1
        x1, y1 = np.ceil(corners.max(0)).astype(int) + 1
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1)
        if x1 <= x0 or y1 <= y0:
            return
        M = np.hstack([L, (t - [x0, y0])[:, None]])
        warped = cv2.warpAffine(sprite, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        a = warped[..., 3:4].astype(np.float32) * (alpha / 255.0)
        region = frame[y0:y1, x0:x1]
        if multiply is not None:
            tint = 1 - (1 - np.array(multiply, np.float32) / 255.0) * 0.85
            region *= 1 - a * (1 - tint)
        else:
            region *= 1 - a
            region += warped[..., :3].astype(np.float32) * alpha

    def _counter_levels(self, thing: Thing, text: str) -> list[np.ndarray]:
        key = (thing.id, text)
        levels = self.sprites.get(key)
        if levels is None:
            image, _, _ = count_sprite(text, thing.line.caption.upper(), thing.extra["seed"], self.look)
            levels = _mips(_premul(np.asarray(image)))
            if len(self.sprites) > 600:
                for k in [k for k in self.sprites if isinstance(k, tuple)][:200]:
                    del self.sprites[k]
            self.sprites[key] = levels
        return levels

    def _card_levels(self, thing: Thing, chars: int) -> list[np.ndarray]:
        key = (thing.id, chars)
        levels = self.sprites.get(key)
        if levels is None:
            image = card_sprite(thing.line.text.upper(), thing.extra["layout"], chars)
            levels = _mips(_premul(np.asarray(image)))
            self.sprites[key] = levels
        return levels

    def _mark_levels(self, thing: Thing, p: float) -> tuple[list[np.ndarray], float, float]:
        q = round(p * 16) / 16
        key = (thing.id, q)
        made = self.sprites.get(key)
        if made is None:
            sprite, (x0, y0, x1, y1) = mark_sprite(thing.extra["strokes"], q, thing.extra["colour"],
                                                   thing.extra.get("width", 13))
            made = (_mips(sprite), (x0 + x1) / 2, (y0 + y1) / 2)
            self.sprites[key] = made
        return made

    def _draw_thing(self, frame: np.ndarray, view, thing: Thing, t: float) -> None:
        age = t - thing.born
        if age < 0:
            return
        if thing.kind in film.PINNED:
            p = _out(age / 0.3)
            scale = 1.1 - 0.1 * p
            levels = self.sprites[thing.id]
            if thing.kind == "card":
                chars = thing.extra["chars"]
                typed = min(chars, int(age / 0.025))
                if typed < chars:
                    levels = self._card_levels(thing, typed - typed % 2)
            dy = (1 - p) * -40
            self._paste(frame, levels, view, thing.x, thing.y + dy, thing.rot, scale, min(1.0, age / 0.18))
        elif thing.kind == "big":
            reveal = min(1.0, age / max(0.25, min(0.6, 0.05 * len(thing.line.text))))
            scale = 1.0 + 0.05 * (1 - _out(age / 0.4))
            self._paste(frame, self.sprites[thing.id], view, thing.x, thing.y, thing.rot, scale, 1.0, reveal)
            bar_at = thing.extra.get("bar_at")
            if bar_at is not None and t >= bar_at:
                self._paste(frame, self.sprites[thing.id + ":bar"], view, thing.x, thing.y, thing.rot, scale, 1.0,
                            min(1.0, (t - bar_at) / 0.35))
        elif thing.kind == "count":
            text = count_text(thing.line.text.upper(), age / 0.9)
            self._paste(frame, self._counter_levels(thing, text), view, thing.x, thing.y, thing.rot, 1.0,
                        min(1.0, age / 0.15))
        elif thing.kind == "label":
            self._paste(frame, self.sprites[thing.id], view, thing.x, thing.y, thing.rot, 1.0, 1.0,
                        min(1.0, age / 0.3))
        elif thing.kind == "mappin":
            p = _out(age / 0.2)
            self._paste(frame, self.sprites[thing.id], view, thing.x, thing.y, thing.rot, 1.6 - 0.6 * p,
                        min(1.0, age / 0.12))
        elif thing.kind == "stamp":
            p = min(1.0, age / 0.12)
            scale = 1.9 - 0.9 * p * p
            self._paste(frame, self.sprites[thing.id], view, thing.x, thing.y, thing.rot, scale, p)
        elif thing.kind == "highlight":
            self._paste(frame, self.sprites[thing.id], view, thing.x, thing.y, thing.rot, 1.0, 1.0,
                        min(1.0, age / 0.4), multiply=HILITE)
        elif thing.kind == "mark":
            levels, mx, my = self._mark_levels(thing, min(1.0, age / thing.extra["dur"]))
            tg = thing.target
            dx, dy = _rot(tg.rot) @ np.array([mx, my])
            self._paste(frame, levels, view, tg.x + dx, tg.y + dy, tg.rot)

    def _draw_string(self, frame: np.ndarray, view, s: dict, t: float) -> None:
        age = t - s["born"]
        if age <= 0:
            return
        A, b = view
        z = math.hypot(A[0, 0], A[1, 0])
        p0, p1 = np.array(s["a"].pin()), np.array(s["b"].pin())
        d = float(np.linalg.norm(p1 - p0))
        ctrl = (p0 + p1) / 2 + np.array([0.0, 0.12 * d])
        u = np.linspace(0, min(1.0, _out(age / s["dur"])), 48)[:, None]
        pts = (1 - u) ** 2 * p0 + 2 * (1 - u) * u * ctrl + u ** 2 * p1
        scr = pts @ A.T + b
        fade = 1.0 if age < 5 else max(0.55, 1 - (age - 5) * 0.2)
        width = max(1, int(round(7 * z)))
        x0, y0 = np.floor(scr.min(0)).astype(int) - width - 2
        x1, y1 = np.ceil(scr.max(0)).astype(int) + width + 2
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1)
        if x1 <= x0 or y1 <= y0:
            return
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        shadow = np.zeros_like(mask)
        pix = ((scr - [x0, y0]) * 16).astype(np.int32)
        cv2.polylines(shadow, [pix + int(5 * z * 16)], False, 255, width, cv2.LINE_AA, shift=4)
        cv2.polylines(mask, [pix], False, 255, width, cv2.LINE_AA, shift=4)
        region = frame[y0:y1, x0:x1]
        region *= 1 - (shadow[..., None].astype(np.float32) / 255.0) * 0.35 * fade
        a = mask[..., None].astype(np.float32) / 255.0 * fade
        region *= 1 - a
        region += a * np.array(STRING, np.float32)

    def _finish(self, frame: np.ndarray, view, z: float, strength: float, speed: np.ndarray) -> np.ndarray:
        """Spotlight, vignette, the wall's uneven light, a warm grade, motion blur, grain. Returns uint8 RGB."""
        gy, gx = self._mask_grid
        cx, cy = self.W / 2, self.H * self.hero_y
        rx, ry = self.fit[0] * self.W * 0.66, self.fit[1] * self.H * 0.78
        d = np.hypot((gx - cx) / rx, (gy - cy) / ry)
        s = np.clip((d - 0.8) / 1.2, 0, 1)
        spot = 1 - strength * (s * s * (3 - 2 * s))
        v = np.hypot((gx - self.W / 2) / (self.W / 2), (gy - self.H / 2) / (self.H / 2))
        mask = spot * (1 - 0.32 * np.clip(v - 0.55, 0, 1) ** 1.4)
        A, b = view
        inv = np.linalg.inv(A)
        wx = inv[0, 0] * (gx - b[0]) + inv[0, 1] * (gy - b[1])
        wy = inv[1, 0] * (gx - b[0]) + inv[1, 1] * (gy - b[1])
        mask *= 1 + 0.06 * np.sin(wx / 2300 + 1) * np.sin(wy / 1900) + 0.04 * np.sin(wx / 900 + wy / 1300)
        if self.look.light:          # a light board dims towards grey, not black
            mask = 1 - (1 - mask) * self.look.spot
        graded = np.clip(mask[..., None] * np.array(self.look.grade) * 200, 0, 255).astype(np.uint8)
        graded = cv2.resize(graded, (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        out = cv2.multiply(cv2.convertScaleAbs(frame), graded, scale=1 / 200)
        if self.look.light:
            lift = np.clip((1 - mask) * 110, 0, 255).astype(np.uint8)
            lift = cv2.resize(cv2.merge([lift, lift, lift]), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
            out = cv2.add(out, lift)
        v = float(np.hypot(*speed))
        if v > 90:                                  # a whip: blur along the move
            k = min(31, int((v - 60) * 0.3)) | 1
            half = cv2.resize(out, (self.W // 2, self.H // 2), interpolation=cv2.INTER_AREA)
            kernel = np.zeros((k, k), np.float32)
            ang = math.atan2(speed[1], speed[0])
            c = k // 2
            cv2.line(kernel, (int(c - math.cos(ang) * c), int(c - math.sin(ang) * c)),
                     (int(c + math.cos(ang) * c), int(c + math.sin(ang) * c)), 1.0, 1)
            kernel /= kernel.sum()
            out = cv2.resize(cv2.filter2D(half, -1, kernel), (self.W, self.H), interpolation=cv2.INTER_LINEAR)
        up, down = self.grain[int(self._t * 12) % len(self.grain)]
        return cv2.subtract(cv2.add(out, up), down)

    def frame_rgb(self, t: float) -> np.ndarray:
        self._t = t
        cx, cy, z, roll, strength = self.camera(t)
        px, py, pz, _, _ = self.camera(max(0.0, t - 1 / FPS))
        speed = np.array([(cx - px) * z, (cy - py) * z])
        view = self._view(cx, cy, z, roll)
        frame = self._background(cx, cy, z, roll)
        half_w, half_h = self.W / 2 / z, self.H / 2 / z
        reach = math.hypot(half_w, half_h) + 1400
        layer = {"photo": 0, "flag": 0, "card": 0, "doc": 0, "map": 0, "big": 0, "count": 0, "label": 1,
                 "highlight": 2, "mark": 3, "mappin": 4, "stamp": 5}
        for s in self.strings:                 # strings run under the prints, from pin to pin
            self._draw_string(frame, view, s, t)
        for thing in sorted(self.things, key=lambda x: (layer[x.kind], x.order)):
            if t < thing.born:
                continue
            if abs(thing.x - cx) > reach or abs(thing.y - cy) > reach:
                continue
            self._draw_thing(frame, view, thing, t)
        return self._finish(frame, view, z, strength, speed)

    def frame(self, t: float) -> Image.Image:
        return Image.fromarray(self.frame_rgb(t), "RGB").convert("RGBA")

    def bgr(self, t: float, observer: animate.visual_qa.FrameObserver | None = None) -> np.ndarray:
        rgb = self.frame_rgb(t)
        if observer is not None:
            observer.observe(t, rgb)       # intentionally before captions
        out = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if self.captions is not None:
            self.captions.draw(out, t)
        return out

    def sound_events(self) -> list[tuple[float, str]]:
        """(time, kind) for every sound the wall makes: pins, stamps, ticks, the marker, strings, whips."""
        kept: list[tuple[float, str]] = []
        for t, kind in sorted((t, k) for t, k in self.events if 0 <= t < self.total):
            if kept and kept[-1][1] == kind and t - kept[-1][0] < 0.15:
                continue
            kept.append((t, kind))
        return kept
