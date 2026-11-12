"""Flags, maps and timelines for the ink style: things the narration names, shown.

Flags are flag-icons (MIT) and country outlines Natural Earth 1:110m (public
domain), packed by `tools/geo_import.py`. Both are drawn here the ink way: a
brush border that draws itself on, colours that come in after, handwritten
labels.
"""

import gzip
import json
import math
from functools import lru_cache

import numpy as np
from PIL import Image

from .. import config
from . import ink

MAP_COLOURS = ("warm", "good", "gold", "green", "bad")
LAND = "#ece5d6"


@lru_cache(maxsize=1)
def flags() -> dict[str, str]:
    return json.loads(gzip.decompress((config.ASSETS / "flags.json.gz").read_bytes()))


@lru_cache(maxsize=1)
def countries() -> dict[str, dict]:
    return json.loads(gzip.decompress((config.ASSETS / "countries.json.gz").read_bytes()))


ALIASES = {"uk": "gb", "england": "gb", "britain": "gb", "great_britain": "gb", "usa": "us", "america": "us",
           "holland": "nl", "persia": "ir", "russia": "ru", "korea": "kr", "south_korea": "kr", "north_korea": "kp",
           "czechia": "cz", "vatican": "va", "ivory_coast": "ci", "burma": "mm", "uae": "ae"}


def code(token: str) -> str | None:
    """A two-letter country code from a code or a name ("ro", "romania", "united_kingdom"), or None."""
    token = token.lower().strip().replace("-", "_")
    token = ALIASES.get(token, token)
    if len(token) == 2 and (token in flags() or token in countries()):
        return token
    name = token.replace("_", " ")
    for key, country in countries().items():
        if name in country["names"]:
            return key
    return None


def name(country: str) -> str:
    return countries().get(country, {}).get("name", country.upper())


# --- flags ---------------------------------------------------------------------------------

def flag_image(country: str, width: int, progress: float) -> Image.Image:
    """The flag, 4:3, with a hand-drawn border and pole; the border draws first, then the colours come in."""
    height = round(width * 3 / 4)
    pole = max(10, width // 14)
    pad = max(8, width // 40)
    total_w, total_h = width + pole + 2 * pad, height + round(height * 0.55) + 2 * pad
    svg = flags()[country].replace("<svg ", f'<svg width="{width}" height="{height}" ', 1)
    colours = np.asarray(ink.render(svg), dtype=np.float32)
    paper = np.array(ink._rgb(ink.PAPER), np.float32)
    colours[..., :3] = colours[..., :3] * 0.86 + paper * 0.14            # printed on paper
    show = min(1.0, max(0.0, (progress - 0.35) / 0.55))
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    if show > 0:
        wipe = int(width * show)                                           # painted left to right
        layer = colours.copy()
        layer[:, wipe:, 3] = 0
        canvas.alpha_composite(Image.fromarray(layer.astype(np.uint8), "RGBA"), (pole + pad, pad))
    w = max(5.0, width / 70)
    x0, y0, x1, y1 = pole + pad, pad, pole + pad + width, pad + height
    border = [(x0, y0), (x1, y0 + 2), (x1 - 1, y1), (x0 + 1, y1 - 1), (x0, y0)]
    stroke = [([(pole * 0.5 + pad, y0 - 4), (pole * 0.5 + pad, total_h - pad)], w * 1.3, ink.INK), (border, w, ink.INK)]
    body = ink.strokes_svg(stroke, min(1.0, progress / 0.45), taper=0.05)
    lines = ink.render(ink.svg_doc(body, total_w, total_h, (0, 0, total_w, total_h)))
    canvas.alpha_composite(lines)
    return canvas


# --- maps ---------------------------------------------------------------------------------

def _largest(rings: list) -> list:
    def area(ring):
        pts = np.asarray(ring)
        x, y = pts[:, 0], pts[:, 1]
        return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2
    return max(rings, key=area)


def map_view(codes: list[str], aspect: float = 4 / 3) -> tuple[float, float, float, float]:
    """(lon0, lat0, lon span, lat span) showing the countries with their surroundings."""
    pts = np.concatenate([np.asarray(_largest(countries()[c]["rings"])) for c in codes if c in countries()])
    lon0, lon1 = float(pts[:, 0].min()), float(pts[:, 0].max())
    lat0, lat1 = float(pts[:, 1].min()), float(pts[:, 1].max())
    clon, clat = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    k = math.cos(math.radians(clat))
    span_lat = max((lat1 - lat0) * 2.0, 16.0)
    span_lon = max((lon1 - lon0) * 2.0, span_lat * aspect / k)
    span_lat = max(span_lat, span_lon * k / aspect)
    return clon, clat, min(span_lon, 360.0), min(span_lat, 170.0)


def _path(ring: list, view, width: float, height: float) -> tuple[str, float]:
    clon, clat, slon, slat = view
    pts = np.asarray(ring, dtype=np.float64)
    x = (pts[:, 0] - clon) / slon * width + width / 2
    y = height / 2 - (pts[:, 1] - clat) / slat * height
    d = "M" + " L".join(f"{a:.1f} {b:.1f}" for a, b in zip(x, y, strict=True)) + "Z"
    return d, float(np.hypot(np.diff(x), np.diff(y)).sum())


def map_image(codes: list[str], width: int, progress: float, colours: dict[str, str] | None = None) -> Image.Image:
    """A hand-drawn map: the countries named in colour and labelled, their neighbours in outline."""
    codes = [c for c in codes if c in countries()]
    height = round(width * 3 / 4)
    view = map_view(codes, width / height)
    clon, clat, slon, slat = view
    box = (clon - slon / 2, clat - slat / 2, clon + slon / 2, clat + slat / 2)
    outline, washes, total = [], [], 0.0
    shapes = []
    for key, country in countries().items():
        for ring in country["rings"]:
            pts = np.asarray(ring)
            if pts[:, 0].max() < box[0] or pts[:, 0].min() > box[2] or pts[:, 1].max() < box[1] \
                    or pts[:, 1].min() > box[3]:
                continue
            d, length = _path(ring, view, width, height)
            shapes.append((key, d, length))
            total += length
    reach = total * min(1.0, progress / 0.6)
    line_w = max(1.6, width / 360)
    for key, d, length in shapes:
        lit = key in codes
        if reach > 0:
            seen = min(length, reach)
            dash = f' stroke-dasharray="{seen:.1f} {length + 2:.1f}"' if seen < length else ""
            weight = line_w * (1.6 if lit else 1)
            outline.append(f'<path d="{d}" fill="none" stroke="{ink.INK}" stroke-width="{weight:.1f}" '
                           f'stroke-linejoin="round"{dash}/>')
        reach -= length
        show = min(1.0, max(0.0, (progress - 0.45) / 0.4))
        fill = ink._wash(ink.COLORS[(colours or {}).get(key) or MAP_COLOURS[codes.index(key) % len(MAP_COLOURS)]]) \
            if lit else LAND
        opacity = (show if lit else min(1.0, progress * 2)) * 0.95
        washes.append(f'<path d="{d}" fill="{fill}" fill-opacity="{opacity:.2f}"/>')
    labels = []
    label_show = min(1.0, max(0.0, (progress - 0.7) / 0.3))
    if label_show > 0:
        size = max(18, width // 18)
        for key in codes:
            pts = np.asarray(_largest(countries()[key]["rings"]))
            d, _ = _path([[float(pts[:, 0].mean()), float(pts[:, 1].mean())]], view, width, height)
            x, y = (float(v) for v in d[1:-1].split())
            text = name(key)
            labels.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="Patrick Hand" font-size="{size}" '
                          f'text-anchor="middle" fill="{ink.INK}" stroke="{ink.PAPER}" stroke-width="{size / 6:.1f}" '
                          f'paint-order="stroke" opacity="{label_show:.2f}">{text}</text>')
    border_w = max(4.0, width / 120)
    frame = [([(2, 2), (width - 2, 3), (width - 3, height - 2), (3, height - 3), (2, 2)], border_w, ink.INK)]
    body = (f'<clipPath id="m"><rect x="0" y="0" width="{width}" height="{height}"/></clipPath>'
            f'<g clip-path="url(#m)">{"".join(washes)}{"".join(outline)}</g>{"".join(labels)}'
            + ink.strokes_svg(frame, min(1.0, progress / 0.3), taper=0.05))
    return ink.render(ink.svg_doc(body, width, height, (0, 0, width, height)))


# --- timelines ------------------------------------------------------------------------------

def timeline_image(labels: list[str], width: int, progress: float, colour: str = ink.INK) -> Image.Image:
    """A hand-drawn timeline: a line with a tick and a handwritten label for each moment, in order."""
    size = max(22, width // 22)
    height = round(size * 3.6)
    y = size * 1.2
    n = max(1, len(labels))
    xs = [width * (0.08 + 0.84 * i / max(1, n - 1)) for i in range(n)] if n > 1 else [width / 2]
    line = [(width * 0.03, y + 2), (width * 0.5, y - 2), (width * 0.97, y + 1)]
    w = max(4.0, width / 160)
    strokes = [(line, w, colour)]
    strokes += [([(x, y - size * 0.45), (x, y + size * 0.45)], w, colour) for x in xs]
    head = [(width * 0.94, y - size * 0.35), (width * 0.97, y + 1), (width * 0.94, y + size * 0.4)]
    strokes.append((head, w, colour))
    body = ink.strokes_svg(strokes, min(1.0, progress / 0.7), taper=0.1)
    for i, (x, label) in enumerate(zip(xs, labels, strict=True)):
        show = min(1.0, max(0.0, (progress - 0.2 - 0.6 * i / n) / 0.25))
        if show <= 0:
            continue
        safe = label.replace("&", "&amp;").replace("<", "&lt;")
        body += (f'<text x="{x:.1f}" y="{y + size * 1.7:.1f}" font-family="Patrick Hand" font-size="{size}" '
                 f'text-anchor="middle" fill="{ink.INK}" opacity="{show:.2f}">{safe}</text>')
    return ink.render(ink.svg_doc(body, width, height, (0, 0, width, height)))
