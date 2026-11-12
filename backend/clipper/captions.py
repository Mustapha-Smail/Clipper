"""Burned-in captions and hook text, drawn with Pillow and blended onto frames.

Captions show two or three words at a time, upper case, with the word being
spoken highlighted. Homebrew's ffmpeg ships without subtitle filters, so the
text is rendered here instead of by ffmpeg.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import OUT_H, OUT_W

MAX_WORDS = 3
MAX_CHARS = 18
MAX_GAP = 0.5            # seconds of silence that forces a new caption group
LINGER = 0.4             # how long a caption stays after its last word
STRIP = ",.;:\"“”()[]"

CAPTION_SIZE = 86
CAPTION_STROKE = 10
CAPTION_MAX_W = 940
CAPTION_CENTER_Y = int(OUT_H * 0.72)
WHITE, YELLOW, BLACK = (255, 255, 255), (255, 221, 0), (0, 0, 0)

HOOK_SIZE = 62
HOOK_MAX_W = 880
HOOK_CENTER_Y = int(OUT_H * 0.15)
HOOK_PAD = 30


def group_words(words: list[dict], start: float, end: float) -> list[dict]:
    """Words inside [start, end] -> caption groups, with times relative to the clip."""
    inside = [w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.3]
    groups: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if current:
            groups.append({"words": list(current)})
            current.clear()

    for word in inside:
        text = word["w"].strip(STRIP).upper()
        if not text:
            continue
        item = {"text": text, "s": word["s"] - start, "e": word["e"] - start}
        chars = sum(len(w["text"]) for w in current) + len(current)
        if current and (item["s"] - current[-1]["e"] > MAX_GAP
                        or len(current) >= MAX_WORDS
                        or chars + len(text) > MAX_CHARS):
            flush()
        current.append(item)
        if word["w"].rstrip("\"')”").endswith((".", "?", "!", ",")):
            flush()
    flush()

    for k, group in enumerate(groups):
        group["start"] = group["words"][0]["s"]
        linger = group["words"][-1]["e"] + LINGER
        nxt = groups[k + 1]["words"][0]["s"] if k + 1 < len(groups) else linger
        group["end"] = min(nxt, linger)
    return groups


def _wrap(texts: list[str], font, max_width: int, space: float) -> list[list[int]]:
    """Greedy line wrap; returns lists of word indexes per line."""
    lines, line, width = [], [], 0.0
    for k, text in enumerate(texts):
        w = font.getlength(text)
        if line and width + space + w > max_width:
            lines.append(line)
            line, width = [], 0.0
        width += (space if line else 0) + w
        line.append(k)
    if line:
        lines.append(line)
    return lines


class Overlay:
    """An RGBA image prepared for fast alpha blending at a fixed position."""

    def __init__(self, image: Image.Image, center_y: int):
        rgba = np.asarray(image, dtype=np.float32)
        alpha = rgba[:, :, 3:4] / 255.0
        self.color = rgba[:, :, 2::-1] * alpha     # premultiplied, RGB -> BGR
        self.keep = 1.0 - alpha
        h, w = rgba.shape[:2]
        self.x = (OUT_W - w) // 2
        self.y = max(0, min(OUT_H - h, center_y - h // 2))
        self.h, self.w = h, w

    def draw(self, frame: np.ndarray) -> None:
        region = frame[self.y:self.y + self.h, self.x:self.x + self.w]
        blended = region * self.keep + self.color
        frame[self.y:self.y + self.h, self.x:self.x + self.w] = blended.astype(np.uint8)


class CaptionRenderer:
    def __init__(self, groups: list[dict], font_path: str, highlight: tuple = YELLOW):
        self.groups = groups
        self.highlight = highlight
        self.font = ImageFont.truetype(font_path, CAPTION_SIZE)
        self.space = self.font.getlength(" ") * 1.25
        ascent, descent = self.font.getmetrics()
        self.line_h = ascent + descent
        self._cache: dict[tuple[int, int], Overlay] = {}
        self._cached_group = -1
        self._cursor = 0

    def _render(self, group: dict, active: int) -> Overlay:
        texts = [w["text"] for w in group["words"]]
        lines = _wrap(texts, self.font, CAPTION_MAX_W, self.space)
        pad = CAPTION_STROKE + 4
        widths = [sum(self.font.getlength(texts[k]) for k in line) + self.space * (len(line) - 1)
                  for line in lines]
        img_w = min(OUT_W, int(max(widths)) + 2 * pad)
        img_h = self.line_h * len(lines) + 2 * pad
        image = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row, (line, line_w) in enumerate(zip(lines, widths, strict=False)):
            x = (img_w - line_w) / 2
            y = pad + row * self.line_h
            for k in line:
                draw.text((x, y), texts[k], font=self.font,
                          fill=self.highlight if k == active else WHITE,
                          stroke_width=CAPTION_STROKE, stroke_fill=BLACK)
                x += self.font.getlength(texts[k]) + self.space
        return Overlay(image, CAPTION_CENTER_Y)

    def draw(self, frame: np.ndarray, t: float) -> None:
        """Draw the caption for time t (seconds from clip start). Call with rising t."""
        while self._cursor < len(self.groups) and t >= self.groups[self._cursor]["end"]:
            self._cursor += 1
        if self._cursor >= len(self.groups):
            return
        group = self.groups[self._cursor]
        if t < group["start"]:
            return
        active = 0
        for k, word in enumerate(group["words"]):
            if t >= word["s"]:
                active = k
        if self._cached_group != self._cursor:
            self._cache.clear()
            self._cached_group = self._cursor
        key = (self._cursor, active)
        if key not in self._cache:
            self._cache[key] = self._render(group, active)
        self._cache[key].draw(frame)


def hook_overlay(text: str, font_path: str) -> Overlay | None:
    """White rounded box with black text, shown at the top during the opening seconds."""
    words = text.strip().split()
    if not words:
        return None
    font = ImageFont.truetype(font_path, HOOK_SIZE)
    space = font.getlength(" ")
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 1.05)
    lines = _wrap(words, font, HOOK_MAX_W, space)
    widths = [sum(font.getlength(words[k]) for k in line) + space * (len(line) - 1)
              for line in lines]
    img_w = min(OUT_W - 40, int(max(widths)) + 2 * HOOK_PAD)
    img_h = line_h * len(lines) + 2 * HOOK_PAD
    image = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, img_w - 1, img_h - 1), radius=26, fill=(255, 255, 255, 245))
    for row, (line, line_w) in enumerate(zip(lines, widths, strict=False)):
        draw.text(((img_w - line_w) / 2, HOOK_PAD + row * line_h),
                  " ".join(words[k] for k in line), font=font, fill=BLACK)
    return Overlay(image, HOOK_CENTER_Y)
