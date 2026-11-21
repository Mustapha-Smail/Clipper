"""Looks for the ink engine: the same storyboard, drawn several ways.

  ink        black brush lines on off-white paper, drawn on, a light boil (the default)
  paper      a cut-out look: kraft paper, props in their full flat colours, a drop
             shadow under every piece, pieces that pop on instead of being drawn
  blueprint  a technical drawing: blue paper with a grid, lines in white
  clean_doodle thin, steady dark lines and a small flat palette on near-white
  rough_paint heavy uneven lines and sparse flat colour on white

A look changes only how things are drawn, never the plan, so a storyboard can
switch looks and render again without planning anew. Archive photos and flags
are pasted as they are in every look.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image

LOOK_NAMES = ("ink", "paper", "blueprint", "cartoon", "clean_doodle", "rough_paint")

Palette = tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class Look:
    name: str
    background_kind: str = "ink_paper"  # ink_paper, kraft, blueprint, near_white or white
    line_profile: str = "brush"          # brush, blueprint, cartoon, uniform or rough
    line_width: float = 1.0               # relative to the current brush line
    jitter: float = 1.0                   # multiplier for the chosen global wobble setting
    fill_mode: str = "wash"               # wash, native_flat, blueprint, cartoon or locked_flat
    palette: Palette = ()
    shadow: bool = False         # a drop shadow under every piece
    entrance_mode: str = "draw"           # draw or pop
    scene_mode: str = "page"               # page or place

    @property
    def pop(self) -> bool:
        return self.entrance_mode == "pop"

    @property
    def boil(self) -> bool:
        return self.jitter > 0

    @property
    def flat_props(self) -> bool:
        return self.fill_mode in ("native_flat", "cartoon")

    @property
    def recolour(self) -> str:
        return "blueprint" if self.line_profile == "blueprint" else ""

    @property
    def cartoon(self) -> bool:
        return self.scene_mode == "place"


LOOKS = {
    "ink": Look("ink"),
    # These four profiles deliberately encode the old boolean combinations exactly.
    "paper": Look("paper", background_kind="kraft", jitter=0, fill_mode="native_flat", shadow=True,
                  entrance_mode="pop"),
    "blueprint": Look("blueprint", background_kind="blueprint", line_profile="blueprint",
                      fill_mode="blueprint"),
    "cartoon": Look("cartoon", line_profile="cartoon", jitter=0, fill_mode="cartoon",
                    entrance_mode="pop", scene_mode="place"),
    "clean_doodle": Look("clean_doodle", background_kind="near_white", line_profile="uniform",
                         line_width=0.7, jitter=0, fill_mode="locked_flat",
                         palette=((236, 184, 71), (81, 139, 126), (218, 111, 92))),
    "rough_paint": Look("rough_paint", background_kind="white", line_profile="rough",
                        line_width=1.8, jitter=2.0, fill_mode="locked_flat",
                        palette=((230, 88, 67), (35, 118, 156), (238, 184, 50))),
}
POPPED = ("actor", "prop", "photo", "flag", "map", "text", "timeline")   # what pops on in a cut-out look


def get(name: str | None) -> Look:
    return LOOKS.get(name or "ink", LOOKS["ink"])


def _vignette(height: int, width: int, strength: float) -> np.ndarray:
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    r = np.hypot((x - width / 2) / (width / 2), (y - height / 2) / (height / 2))
    return (1 - strength * np.clip(r - 0.5, 0, 1) ** 1.5)[..., None]


def kraft(width: int, height: int, seed: int = 21) -> np.ndarray:
    """Brown kraft card with fibres."""
    rng = np.random.default_rng(seed)
    base = np.array([214, 192, 156], np.float32)
    grain = rng.normal(0, 3.5, (height, width, 1)).astype(np.float32)
    fibres = rng.normal(0, 1, (height // 3 + 2, width // 24 + 2)).astype(np.float32)
    fibres = np.asarray(Image.fromarray(fibres).resize((width, height), Image.BICUBIC))[..., None] * 3.0
    mottle = rng.normal(0, 1, (height // 60 + 2, width // 60 + 2)).astype(np.float32)
    mottle = np.asarray(Image.fromarray(mottle).resize((width, height), Image.BICUBIC))[..., None] * 2.2
    return np.clip((base + grain + fibres + mottle) * _vignette(height, width, 0.12), 0, 255).astype(np.uint8)


def blueprint(width: int, height: int, seed: int = 31) -> np.ndarray:
    """Blueprint paper: deep blue, a fine grid and a coarser one, faintly uneven."""
    rng = np.random.default_rng(seed)
    out = np.empty((height, width, 3), np.float32)
    out[:] = (24, 74, 138)
    cell = max(24, height // 22)
    lines = np.zeros((height, width), np.float32)
    lines[::cell, :] = 0.5
    lines[:, ::cell] = 0.5
    lines[:: cell * 5, :] = 1.0
    lines[:, :: cell * 5] = 1.0
    # lines two pixels wide at 1080p, so they survive the video encoder
    thick = max(1, height // 700)
    for k in range(1, thick):
        lines[k::cell, :] = np.maximum(lines[k::cell, :], lines[::cell, :][: lines[k::cell, :].shape[0]])
        lines[:, k::cell] = np.maximum(lines[:, k::cell], lines[:, ::cell][:, : lines[:, k::cell].shape[1]])
    out += lines[..., None] * np.array([40, 50, 58], np.float32)
    mottle = rng.normal(0, 1, (height // 120 + 2, width // 120 + 2)).astype(np.float32)
    mottle = np.asarray(Image.fromarray(mottle).resize((width, height), Image.BICUBIC))[..., None] * 1.6
    out += mottle + rng.normal(0, 1.5, (height, width, 1))
    return np.clip(out * _vignette(height, width, 0.18), 0, 255).astype(np.uint8)


def background(look: Look, width: int, height: int, ink_paper) -> np.ndarray:
    if look.background_kind == "kraft":
        return kraft(width, height)
    if look.background_kind == "blueprint":
        return blueprint(width, height)
    if look.background_kind == "near_white":
        out = np.empty((height, width, 3), np.uint8)
        out[:] = (250, 249, 246)
        return out
    if look.background_kind == "white":
        return np.full((height, width, 3), 255, np.uint8)
    return ink_paper(width, height)


PASTED = ("photo", "flag", "map")     # pasted in as they are, never recoloured
NO_EDGE = ("photo", "arrow", "mark", "tag")    # drawn straight on the card, no cut edge


def blueprint_sprite(image: Image.Image) -> Image.Image:
    """A drawing as a blueprint: dark lines become white, colours a pale cyan wash, light fills nearly clear."""
    a = np.asarray(image, np.float32) / 255
    rgb, alpha = a[..., :3], a[..., 3:4]
    lum = (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2])[..., None]
    sat = (rgb.max(axis=2) - rgb.min(axis=2))[..., None]
    inky = np.clip((0.72 - lum) / 0.45, 0, 1)
    colour = np.clip(sat * 2.5, 0, 1) * (1 - inky)
    white = np.array([0.95, 0.97, 1.0], np.float32)
    cyan = np.array([0.62, 0.82, 1.0], np.float32)
    new_rgb = white * inky + cyan * colour + white * np.clip(1 - inky - colour, 0, 1)
    new_alpha = alpha * np.clip(inky + colour * 0.4 + (1 - inky) * (1 - colour) * 0.12, 0, 1)
    out = np.concatenate([new_rgb, new_alpha], axis=2)
    return Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8), "RGBA")


def profiled_sprite(image: Image.Image, look: Look) -> Image.Image:
    """Apply a code-native line/fill profile without changing the sprite's box.

    The established profiles are returned byte-for-byte. The two opt-in profiles
    flatten drawn washes into a locked palette and make line weight explicit.
    """
    if look.line_profile not in ("uniform", "rough"):
        return image

    import cv2

    source = np.asarray(image)
    alpha = source[..., 3]
    rgb = source[..., :3]
    visible = alpha > 12
    lum = (rgb[..., 0].astype(np.uint16) * 30 + rgb[..., 1].astype(np.uint16) * 59
           + rgb[..., 2].astype(np.uint16) * 11) // 100
    line = (visible & (lum < 118)).astype(np.uint8) * 255
    if look.line_width < 1:
        # One deterministic erosion produces a steadier, thinner line without
        # inventing detail or moving the sprite.
        line = cv2.erode(line, np.ones((2, 2), np.uint8), iterations=1)
    elif look.line_width > 1:
        radius = max(1, round(look.line_width))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        line = cv2.dilate(line, kernel, iterations=1)
        # Break only the outer fringe with a fixed coordinate pattern. The core
        # remains solid and readable; the edge reads as deliberately uneven.
        core = cv2.erode(line, np.ones((3, 3), np.uint8), iterations=1)
        yy, xx = np.indices(line.shape)
        fringe = (line > 0) & (core == 0)
        line[fringe & (((xx * 17 + yy * 29) % 13) == 0)] = 0

    line_mask = line > 0
    fill = visible & ~line_mask
    out = np.zeros_like(source)
    out[..., :3] = (35, 35, 33) if look.line_profile == "uniform" else (30, 27, 25)
    out[..., 3] = np.where(line_mask, np.maximum(alpha, 230), 0)
    if fill.any() and look.palette:
        palette = np.asarray(look.palette, np.int32)
        pixels = rgb.astype(np.int32)
        distance = ((pixels[..., None, :] - palette[None, None, ...]) ** 2).sum(axis=3)
        chosen = palette[distance.argmin(axis=2)].astype(np.uint8)
        if look.line_profile == "rough":
            # Sparse colour: retain the original wash footprint, but leave light
            # neutral regions unpainted rather than turning them into a solid fill.
            saturation = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
            fill &= (saturation > 18) | (lum < 225)
        out[fill, :3] = chosen[fill]
        out[fill, 3] = alpha[fill]
    return Image.fromarray(out, "RGBA")


CARD = (250, 246, 236)


def cutout(image: Image.Image, edge: int) -> Image.Image:
    """A piece cut out of card: a pale margin of `edge` pixels all round. Grows by `edge` on every side."""
    import cv2

    pad = edge + 1
    a = np.zeros((image.height + 2 * pad, image.width + 2 * pad), np.uint8)
    a[pad:-pad, pad:-pad] = np.asarray(image)[..., 3]
    # solid where there is anything at all, so thin lines get a full margin
    solid = np.where(a > 24, 255, 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * edge + 1, 2 * edge + 1))
    margin = cv2.GaussianBlur(cv2.dilate(solid, kernel), (3, 3), 0)
    card = np.zeros((a.shape[0], a.shape[1], 4), np.uint8)
    card[..., :3] = CARD
    card[..., 3] = margin
    out = Image.fromarray(card, "RGBA")
    out.alpha_composite(image, (pad, pad))
    return out


def board(image: Image.Image, edge: int) -> Image.Image:
    """A picture with holes (a map's seas) pinned on a card that fills its whole box."""
    pad = edge
    out = Image.new("RGBA", (image.width + 2 * pad, image.height + 2 * pad), (0, 0, 0, 0))
    from PIL import ImageDraw

    ImageDraw.Draw(out).rounded_rectangle((1, 1, out.width - 2, out.height - 2), radius=pad,
                                          fill=(*CARD, 255), outline=(43, 34, 32, 255), width=max(2, pad // 3))
    out.alpha_composite(image, (pad, pad))
    return out


def shadow(layer: Image.Image, height: int) -> Image.Image | None:
    """A soft shadow down and to the right of everything on the layer, as its own layer (None if empty)."""
    import cv2

    box = layer.getbbox()
    if box is None:
        return None
    shift = max(3, height // 150)
    blur = max(2.0, height / 300)
    pad = shift + int(blur * 3) + 2
    x0, y0 = box[0], box[1]
    x1, y1 = min(layer.width, box[2] + pad), min(layer.height, box[3] + pad)
    alpha = np.asarray(layer)[y0:y1, x0:x1, 3]
    # blur at half size: a shadow has no detail worth the time
    small = cv2.resize(alpha, (max(1, (x1 - x0) // 2), max(1, (y1 - y0) // 2)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), blur / 2)
    soft = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
    out = np.zeros((layer.height, layer.width, 4), np.uint8)
    out[..., :3] = (60, 40, 20)
    w, h = x1 - x0 - shift, y1 - y0 - shift
    out[y0 + shift:y1, x0 + shift:x1, 3] = (soft[:h, :w].astype(np.uint16) * 45 // 100).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def pop_scale(age: float, gone_for: float | None, erase: float) -> float:
    """A cut-out piece springs onto the card in 0.45 s and shrinks away when it leaves."""
    from .ink import spring

    s = spring(min(1.0, max(0.0, age / 0.45)), 0.4, 1.6)
    if gone_for is not None and gone_for >= 0:
        s *= max(0.0, 1 - gone_for / erase) ** 2
    return s


def flat_prop(svg: str, box: int) -> Image.Image:
    """A library prop in its own colours, `box` pixels on its longer side."""
    from . import art

    image = art.rasterize(svg, box)
    if image.width > box:
        image = image.resize((box, max(4, round(image.height * box / image.width))), Image.LANCZOS)
    return image
