"""The renderer: storyboard + voice -> video, every frame drawn with Pillow.

Each frame is the scene's background, its elements placed in their slots and
animated (pop-ins, motion, a camera move), cast members that bob, blink and
move their mouths with the loudness of the voice, then the word-by-word
captions shared with the clipping side. Frames are piped to ffmpeg with the
voice. Everything is cached that can be: a video reuses a few drawings
thousands of times.
"""

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import captions as captions_mod
from ..db import session_scope
from ..db.models import Storyboard, StudioAsset, utcnow
from ..ffutil import require
from ..jobs import Reporter
from ..render import RenderOptions
from ..settings import Settings, hex_to_rgb, resolve_font
from . import art, sound
from . import voice as voice_mod

FPS = 24
ENTER = 0.32          # seconds an entrance takes
SIDE_SLOTS = {"left", "right", "top_left", "top_right", "bottom_left", "bottom_right"}
BANNER = 3.2          # seconds a chapter banner stays on screen
MARGIN = 0.03         # of the frame width, kept clear at the edges


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    slots: dict
    character: dict       # size -> sprite height
    prop: dict            # size -> sprite box
    text: dict            # size -> font size
    floor_y: float
    captions: bool
    stage_bottom: float   # elements stay above this (the captions live below it)
    stage_top: float      # and below this (long videos keep the chapter banner's corner clear)


LAYOUTS = {
    "short": Layout(1080, 1920, {
        "top_left": (0.26, 0.16), "top": (0.5, 0.16), "top_right": (0.74, 0.16),
        "left": (0.26, 0.39), "center": (0.5, 0.39), "right": (0.74, 0.39),
        "bottom_left": (0.26, 0.58), "bottom": (0.5, 0.58), "bottom_right": (0.74, 0.58),
    }, {"small": 420, "medium": 620, "large": 860}, {"small": 300, "medium": 470, "large": 680},
        {"small": 72, "medium": 104, "large": 146}, 0.62, True, 0.665, 0.018),
    "long": Layout(1920, 1080, {
        "top_left": (0.2, 0.22), "top": (0.5, 0.21), "top_right": (0.8, 0.22),
        "left": (0.22, 0.56), "center": (0.5, 0.56), "right": (0.78, 0.56),
        "bottom_left": (0.22, 0.85), "bottom": (0.5, 0.85), "bottom_right": (0.78, 0.85),
    }, {"small": 300, "medium": 470, "large": 680}, {"small": 230, "medium": 360, "large": 520},
        {"small": 60, "medium": 88, "large": 124}, 0.78, False, 0.97, 0.12),
}


def mouth_levels(wav: Path, start: float, duration: float, fps: int = FPS) -> np.ndarray:
    """0, 1 or 2 per frame: how open a talking mouth is, from the loudness of the voice."""
    proc = subprocess.run([require("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
                           "-i", str(wav), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                          capture_output=True, check=True)
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    per = 16000 // fps
    frames = max(1, math.ceil(duration * fps))
    audio = np.pad(audio, (0, max(0, frames * per - len(audio))))[:frames * per]
    rms = np.sqrt((audio.reshape(frames, per) ** 2).mean(axis=1))
    loud = rms[rms > rms.max() * 0.05] if rms.max() > 0 else rms
    ref = np.percentile(loud, 90) if len(loud) else 1.0
    level = rms / max(ref, 1e-6)
    out = np.where(level > 0.6, 2, np.where(level > 0.2, 1, 0)).astype(np.int8)
    # Hold each shape for at least two frames: faster flapping looks like a glitch.
    for i in range(1, len(out) - 1):
        if out[i] != out[i - 1] and out[i] != out[i + 1]:
            out[i] = out[i - 1]
    return out


class Composer:
    """Draws any frame of a storyboard."""

    def __init__(self, board: dict, props: dict[str, str | None], settings: Settings, mouths: np.ndarray | None):
        self.board = board
        self.layout = LAYOUTS[board["format"]]
        self.props = props
        self.mouths = mouths
        self.font_path = resolve_font(settings)
        self.words = board["words"]
        self.scenes = board["scenes"] or [{"start_word": 0, "background": "paper", "camera": "still",
                                           "elements": [], "note": ""}]
        self.starts = [self.word_time(s["start_word"]) for s in self.scenes]
        self.starts[0] = 0.0
        self.duration = board["end"] - board["start"]
        self.captions = self.captions_on_yellow = None
        if self.layout.captions and settings.captions_enabled and self.words:
            groups = captions_mod.group_words(self.words, 0.0, self.duration)
            highlight = hex_to_rgb(settings.highlight_color)
            self.captions = captions_mod.CaptionRenderer(groups, self.font_path, highlight)
            # A yellow highlight vanishes on a yellow background: those scenes highlight in red.
            self.captions_on_yellow = captions_mod.CaptionRenderer(
                captions_mod.group_words(self.words, 0.0, self.duration), self.font_path,
                hex_to_rgb(art.PALETTE["red"]))
        self._sprites: dict = {}
        self._fonts: dict = {}
        # Long videos show a banner when a new chapter (script section) starts.
        self.chapters: list[tuple[float, int, str]] = []
        if board["format"] == "long":
            seen = None
            for w in self.words:
                key = w.get("section")
                if key and key != seen:
                    if seen is not None:
                        self.chapters.append((w["s"], len(self.chapters) + 2, board.get("headings", {}).get(key, "")))
                    seen = key

    def word_time(self, index: int | None) -> float:
        if index is None or not self.words:
            return 0.0
        return self.words[max(0, min(index, len(self.words) - 1))]["s"]

    # --- sprites -----------------------------------------------------------------------

    def sprite(self, key: tuple, make) -> Image.Image:
        image = self._sprites.get(key)
        if image is None:
            image = make()
            if len(self._sprites) > 400:
                self._sprites.clear()
            self._sprites[key] = image
        return image

    def character(self, e: dict, mouth: int, blink: bool) -> Image.Image:
        height = self.layout.character[e["size"]] * (0.75 if e["name"] == "kid" else 1.0)
        key = ("c", e["name"], e["pose"], e["expression"], mouth, blink, e["facing"], int(height))
        return self.sprite(key, lambda: art.soft_shadow(art.rasterize(
            art.character_svg(e["name"], e["pose"], e["expression"], mouth, blink, e["facing"]), height)))

    def prop(self, e: dict) -> Image.Image:
        box = self.layout.prop[e["size"]]
        key = ("p", e["name"], box)

        def make() -> Image.Image:
            svg = self.props.get(e["name"]) or art.placeholder_svg(e["name"])
            image = art.rasterize(svg, box)
            if image.width > box:   # wide drawings: fit the width instead
                image = image.resize((box, max(1, round(image.height * box / image.width))), Image.LANCZOS)
            image = art.soft_shadow(image)
            if e.get("text"):
                image = self._with_label(image, e["text"])
            return image
        return self.sprite(key + (e.get("text", ""),), make)

    def font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(self.font_path, size)
        return self._fonts[size]

    def _with_label(self, image: Image.Image, label: str) -> Image.Image:
        font = self.font(self.layout.text["small"] * 2 // 3)
        text = label.upper()
        w = int(font.getlength(text)) + 40
        h = font.size + 28
        out = Image.new("RGBA", (max(image.width, w), image.height + h), (0, 0, 0, 0))
        out.alpha_composite(image, ((out.width - image.width) // 2, 0))
        draw = ImageDraw.Draw(out)
        x = (out.width - w) // 2
        draw.rounded_rectangle((x, image.height + 4, x + w, image.height + h - 4), radius=14,
                               fill="#ffffff", outline=art.INK, width=5)
        draw.text((out.width / 2, image.height + h / 2), text, font=font, fill=art.INK, anchor="mm")
        return out

    def text(self, e: dict, dark: bool) -> Image.Image:
        number = e["kind"] == "number"
        size = int(self.layout.text[e["size"]] * (1.35 if number else 1.0))
        side = e["slot"] in SIDE_SLOTS
        key = ("t", e["text"], size, number, dark, side)

        def make() -> Image.Image:
            font = self.font(size)
            words = e["text"].split()
            max_w = self.layout.width * (0.44 if side else 0.86)
            lines, line = [], ""
            for word in words:
                trial = f"{line} {word}".strip()
                if line and font.getlength(trial) > max_w:
                    lines.append(line)
                    line = word
                else:
                    line = trial
            lines.append(line)
            stroke = max(6, size // 10)
            line_h = int(size * 1.08)
            w = int(max(font.getlength(x) for x in lines)) + 2 * stroke + 8
            h = line_h * len(lines) + 2 * stroke + 8
            image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            fill = art.PALETTE["yellow"] if number else ("#ffffff" if dark else art.INK)
            outline = art.INK if (number or dark) else art.PAPER
            for i, text in enumerate(lines):
                draw.text((w / 2, stroke + 4 + i * line_h + line_h / 2), text, font=font, fill=fill,
                          anchor="mm", stroke_width=stroke if (number or dark) else stroke // 2, stroke_fill=outline)
            return image
        return self.sprite(key, make)

    # --- layout ------------------------------------------------------------------------

    def base_sprite(self, e: dict, dark: bool) -> Image.Image | None:
        if e["kind"] == "character":
            return self.character(e, 0, False)
        if e["kind"] == "prop":
            return self.prop(e)
        if e["kind"] in ("text", "number"):
            return self.text(e, dark)
        return None

    def placement(self, index: int) -> list[tuple[float, float, float]]:
        """Where each element of a scene sits, and how much it is scaled to avoid its neighbours.

        Elements start at their slots, are kept inside the frame (and above the
        captions), then overlapping pairs are pushed apart along the axis where
        they overlap least, and shrunk a little when the frame edge stops them.
        """
        key = ("layout", index)
        if key in self._sprites:
            return self._sprites[key]
        lay = self.layout
        scene = self.scenes[index]
        dark = scene["background"] == "blue"
        items = []
        for e in scene["elements"]:
            cx, cy = lay.slots[e["slot"]]
            sprite = self.base_sprite(e, dark)
            w, h = (sprite.width, sprite.height) if sprite else (0, 0)
            items.append([cx * lay.width, cy * lay.height, 1.0, w, h])
        left, right = lay.width * MARGIN, lay.width * (1 - MARGIN)
        top, bottom = lay.height * lay.stage_top, lay.height * lay.stage_bottom

        def clamp(item) -> bool:
            """Keep inside the stage; shrink when it cannot fit. True if it had to move."""
            x, y, s, w, h = item
            s = min(s, (right - left) / max(w, 1), (bottom - top) / max(h, 1))
            hw, hh = w * s / 2, h * s / 2
            nx, ny = min(max(x, left + hw), right - hw), min(max(y, top + hh), bottom - hh)
            moved = abs(nx - x) > 0.5 or abs(ny - y) > 0.5
            item[0], item[1], item[2] = nx, ny, s
            return moved

        for item in items:
            clamp(item)
        for _ in range(24):
            settled = True
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    if not a[3] or not b[3]:
                        continue
                    ox = (a[3] * a[2] + b[3] * b[2]) / 2 - abs(a[0] - b[0])
                    oy = (a[4] * a[2] + b[4] * b[2]) / 2 - abs(a[1] - b[1])
                    if ox <= 6 or oy <= 6:
                        continue
                    settled = False
                    if ox < oy:   # side by side: push apart horizontally
                        sign = 1 if a[0] <= b[0] else -1
                        a[0] -= sign * (ox / 2 + 8)
                        b[0] += sign * (ox / 2 + 8)
                    else:
                        sign = 1 if a[1] <= b[1] else -1
                        a[1] -= sign * (oy / 2 + 8)
                        b[1] += sign * (oy / 2 + 8)
                    stuck = clamp(a) | clamp(b)
                    if stuck:   # the frame edge got in the way: make the bigger one smaller
                        big = a if a[3] * a[4] * a[2] >= b[3] * b[4] * b[2] else b
                        big[2] = max(0.55, big[2] * 0.92)
                        clamp(big)
            if settled:
                break
        result = [(x, y, s) for x, y, s, _, _ in items]
        self._sprites[key] = result
        return result

    # --- frames ------------------------------------------------------------------------

    def scene_at(self, t: float) -> int:
        index = 0
        for i, start in enumerate(self.starts):
            if t >= start:
                index = i
        return index

    def frame(self, t: float) -> Image.Image:
        """The picture at time t, without captions."""
        lay = self.layout
        index = self.scene_at(t)
        scene = self.scenes[index]
        scene_start = self.starts[index]
        scene_end = self.starts[index + 1] if index + 1 < len(self.scenes) else self.duration
        dark = scene["background"] == "blue"
        canvas = art.background(scene["background"], lay.width, lay.height, lay.floor_y).copy()
        frame_no = int(t * FPS)
        mouth = int(self.mouths[min(frame_no, len(self.mouths) - 1)]) if self.mouths is not None else 0

        places = self.placement(index)
        for n, e in enumerate(scene["elements"]):
            appear = max(scene_start, self.word_time(e.get("enter_word"))) if e.get("enter_word") is not None \
                else scene_start
            age = t - appear
            if age < 0:
                continue
            seed = n * 1.7 + index
            cx = lay.slots[e["slot"]][0]
            x, y, scale = places[n]
            alpha, angle = 1.0, 0.0
            p = min(1.0, age / ENTER) if e["enter"] != "none" else 1.0
            if e["enter"] == "pop":
                scale *= max(0.01, art.ease_out_back(p))
            elif e["enter"] == "fade":
                alpha = p
            elif e["enter"] == "slide":
                x += (1 - art.ease_out(p)) * (-1 if cx < 0.5 else 1) * lay.width * 0.7
            motion = e["motion"]
            if motion == "bounce":
                y -= abs(math.sin(age * 2 * math.pi * 1.4)) * 26
            elif motion == "shake":
                x += 8 * math.sin(age * 2 * math.pi * 9)
            elif motion == "pulse":
                scale *= 1 + 0.05 * math.sin(age * 2 * math.pi * 1.2)
            elif motion == "float":
                y += 16 * math.sin(age * 2 * math.pi * 0.4 + seed)
            elif motion == "spin":
                angle = -age * 90
            elif motion == "grow":
                scale *= 0.8 + 0.3 * art.ease_out(min(1.0, age / 2.5))

            if e["kind"] == "arrow":
                self._arrow(canvas, e, min(1.0, age / 0.4), dark)
                continue
            if e["kind"] == "character":
                y += art.wobble(t, seed, 5)
                blink = ((t + seed) % 3.7) < 0.13
                sprite = self.character(e, mouth if e.get("talking") else 0, blink)
            elif e["kind"] == "prop":
                sprite = self.prop(e)
            else:
                sprite = self.text(e, dark)
            self._paste(canvas, sprite, x, y, scale, alpha, angle)

        camera = scene["camera"]
        if camera != "still":
            progress = art.ease_out(min(1.0, (t - scene_start) / max(scene_end - scene_start, 0.5)))
            zoom, shift = 1.0, 0.0
            if camera == "zoom_in":
                zoom = 1 + 0.09 * progress
            elif camera == "zoom_out":
                zoom = 1.09 - 0.09 * progress
            else:
                zoom = 1.06
                shift = (progress - 0.5) * 0.04 * lay.width * (1 if camera == "pan_left" else -1)
            w, h = lay.width / zoom, lay.height / zoom
            left = (lay.width - w) / 2 + shift
            top = (lay.height - h) / 2 - (lay.height * 0.08 * (zoom - 1) if lay.captions else 0)
            canvas = canvas.resize((lay.width, lay.height), Image.BILINEAR, box=(left, top, left + w, top + h))
        for at, number, heading in self.chapters:
            if at <= t < at + BANNER:
                self._banner(canvas, number, heading, t - at)
        return canvas

    def _banner(self, canvas: Image.Image, number: int, heading: str, age: float) -> None:
        """'Chapter 3 · Heading' in a card that slides in at the top left, then out."""
        sprite = self.sprite(("banner", number, heading), lambda: self._banner_sprite(number, heading))
        slide = sprite.width + 60
        if age < 0.4:
            offset = -slide * (1 - art.ease_out(age / 0.4))
        elif age > BANNER - 0.4:
            offset = -slide * (1 - art.ease_out((BANNER - age) / 0.4))
        else:
            offset = 0.0
        _composite_clipped(canvas, sprite, round(36 + offset), 28)

    def _banner_sprite(self, number: int, heading: str) -> Image.Image:
        """Kept within the top 120 px, above the 16:9 layout's top row."""
        small, big = self.font(22), self.font(40)
        label = f"CHAPTER {number}"
        width = int(max(small.getlength(label), big.getlength(heading))) + 48
        image = Image.new("RGBA", (width + 8, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((6, 6, width + 6, 98), radius=16, fill=art.INK)        # offset shadow
        draw.rounded_rectangle((0, 0, width, 92), radius=16, fill="#ffffff", outline=art.INK, width=5)
        draw.text((24, 14), label, font=small, fill=art.PALETTE["red"])
        draw.text((24, 40), heading, font=big, fill=art.INK)
        return image

    @staticmethod
    def _paste(canvas: Image.Image, sprite: Image.Image, x: float, y: float, scale: float, alpha: float,
               angle: float) -> None:
        if scale <= 0.02 or alpha <= 0.01:
            return
        if abs(scale - 1) > 0.01:
            sprite = sprite.resize((max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale))),
                                   Image.BILINEAR)
        if angle:
            sprite = sprite.rotate(angle, resample=Image.BILINEAR, expand=True)
        if alpha < 1:
            sprite = sprite.copy()
            sprite.putalpha(sprite.getchannel("A").point(lambda a: int(a * alpha)))
        _composite_clipped(canvas, sprite, round(x - sprite.width / 2), round(y - sprite.height / 2))

    def _arrow(self, canvas: Image.Image, e: dict, progress: float, dark: bool) -> None:
        lay = self.layout
        (ax, ay), (bx, by) = lay.slots[e["slot"]], lay.slots[e["to_slot"]]
        ax, ay, bx, by = ax * lay.width, ay * lay.height, bx * lay.width, by * lay.height
        # Leave room for whatever sits in the two slots.
        sx, sy = ax + (bx - ax) * 0.3, ay + (by - ay) * 0.3
        ex, ey = ax + (bx - ax) * 0.7, ay + (by - ay) * 0.7
        ex, ey = sx + (ex - sx) * art.ease_out(progress), sy + (ey - sy) * art.ease_out(progress)
        colour = "#ffffff" if dark else art.INK
        width = 16 if e["size"] != "small" else 11
        draw = ImageDraw.Draw(canvas)
        draw.line((sx, sy, ex, ey), fill=colour, width=width, joint="curve")
        angle = math.atan2(ey - sy, ex - sx)
        head = width * 3.2
        points = [(ex + math.cos(angle) * head * 0.6, ey + math.sin(angle) * head * 0.6),
                  (ex + math.cos(angle + 2.5) * head, ey + math.sin(angle + 2.5) * head),
                  (ex + math.cos(angle - 2.5) * head, ey + math.sin(angle - 2.5) * head)]
        draw.polygon(points, fill=colour)

    def bgr(self, t: float) -> np.ndarray:
        """The finished frame, captions included, as BGR for the encoder."""
        frame = np.ascontiguousarray(np.asarray(self.frame(t).convert("RGB"))[:, :, ::-1])
        if self.captions is not None:
            yellow = self.scenes[self.scene_at(t)]["background"] == "yellow"
            (self.captions_on_yellow if yellow else self.captions).draw(frame, t)
        return frame


def _composite_clipped(canvas: Image.Image, sprite: Image.Image, left: int, top: int) -> None:
    """alpha_composite for a sprite that hangs off the edge of the frame."""
    box = (max(0, -left), max(0, -top),
           min(sprite.width, canvas.width - left), min(sprite.height, canvas.height - top))
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    canvas.alpha_composite(sprite.crop(box), (max(0, left), max(0, top)))


# --- loading and rendering -----------------------------------------------------------------

def load(storyboard_id: int) -> tuple[dict, dict[str, str | None], Path | None]:
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        if board is None:
            raise RuntimeError(f"Storyboard {storyboard_id} no longer exists")
        data = {"id": board.id, "script_id": board.script_id, "format": board.format, "title": board.title,
                "start": board.start, "end": board.end, "words": list(board.words), "scenes": list(board.scenes),
                "headings": {s["key"]: s["heading"] for s in board.script.sections},
                "effects": board.effects, "music_db": board.music_db,
                "music": board.music.path if board.music and Path(board.music.path).is_file() else None}
        names = {e["name"] for s in board.scenes for e in s["elements"] if e["kind"] == "prop"}
        props = {a.name: a.svg for a in db.query(StudioAsset).filter(StudioAsset.name.in_(names))} if names else {}
        wav = Path(board.take.path) if board.take_id and board.take and board.take.path else None
    return data, props, wav


def still(storyboard_id: int, t: float, settings: Settings, width: int = 540) -> bytes:
    """One frame as PNG, for the storyboard editor. No captions, mouths closed."""
    import io

    board, props, _ = load(storyboard_id)
    composer = Composer(board, props, settings, None)
    image = composer.frame(max(0.0, min(t, composer.duration - 0.01))).convert("RGB")
    image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def preview_mix(board, voice_path: str, music_path: str | None, music_db: float, effects: bool,
                start: float, seconds: float) -> bytes:
    """Voice, effects and music for [start, start + seconds) of a storyboard, as AAC audio."""
    import tempfile

    duration = board.end - board.start
    start = min(start, max(0.0, duration - 5))
    seconds = min(seconds, duration - start)
    with tempfile.TemporaryDirectory() as tmp:
        inputs = ["-ss", f"{board.start + start:.3f}", "-t", f"{seconds:.3f}", "-i", voice_path]
        if effects:
            composer_words = board.words

            def word_time(i):
                return composer_words[max(0, min(i, len(composer_words) - 1))]["s"] if composer_words else 0.0
            events = [(at - start, kind) for at, kind in sound.effect_events(board.scenes, word_time, duration)
                      if start <= at < start + seconds]
            fx = Path(tmp) / "fx.wav"
            sound.write_effects(events, seconds, fx, seed=board.id)
            inputs += ["-i", str(fx)]
        if music_path:
            looped = [] if music_path.startswith(("http://", "https://")) else ["-stream_loop", "-1"]
            inputs += [*looped, "-i", music_path]
        graph, mixed = sound.mix_graph(seconds, effects, bool(music_path), music_db)
        # mix_graph numbers the voice as input 1 (input 0 is the video when rendering): add a silent input 0.
        out = Path(tmp) / "preview.m4a"
        proc = subprocess.run([require("ffmpeg"), "-v", "error", "-f", "lavfi", "-t", "0.1", "-i", "anullsrc",
                               *inputs, "-filter_complex", graph, "-map", mixed, "-t", f"{seconds:.3f}",
                               "-c:a", "aac", "-b:a", "128k", str(out)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Could not mix the preview: {proc.stderr[-400:]}")
        return out.read_bytes()


def video_path(board: dict) -> Path:
    return voice_mod.script_media_dir(board["script_id"]) / f"storyboard-{board['id']}" / f"{board['format']}.mp4"


def render(storyboard_id: int, settings: Settings, rep: Reporter) -> None:
    board, props, wav = load(storyboard_id)
    if wav is None or not wav.is_file():
        raise RuntimeError("The voice take of this storyboard is missing. Make a new storyboard from a take.")
    if not board["scenes"]:
        raise RuntimeError("The storyboard has no scenes yet")
    missing = sorted(n for n, svg in props.items() if not svg)
    if missing:
        rep.warn(f"Not drawn yet, shown as placeholders: {', '.join(missing)}")
    duration = board["end"] - board["start"]
    lay = LAYOUTS[board["format"]]
    composer = Composer(board, props, settings, mouth_levels(wav, board["start"], duration))
    total = math.ceil(duration * FPS)
    out = video_path(board)
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_suffix(".partial.mp4")
    options = RenderOptions.from_settings(settings)
    audio_inputs = ["-ss", f"{board['start']:.3f}", "-t", f"{duration:.3f}", "-i", str(wav)]
    effects_wav = out.parent / "effects.wav"
    if board["effects"]:
        events = sound.effect_events(composer.scenes, composer.word_time, duration)
        sound.write_effects(events, duration, effects_wav, seed=board["id"])
        audio_inputs += ["-i", str(effects_wav)]
        rep.info(f"{len(events)} sound effects")
    if board["music"]:
        audio_inputs += ["-stream_loop", "-1", "-i", board["music"]]
        rep.info(f"Music under the voice at {board['music_db']:g} dB, lowered while the voice speaks")
    graph, mixed = sound.mix_graph(duration, board["effects"], bool(board["music"]), board["music_db"])
    cmd = [require("ffmpeg"), "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{lay.width}x{lay.height}", "-r", str(FPS), "-i", "-",
           *audio_inputs, "-filter_complex", graph,
           "-map", "0:v", "-map", mixed, *options.encoder_args(), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(partial)]
    rep.info(f"Rendering {total} frames ({duration:.0f} s at {FPS} fps, {lay.width}x{lay.height}, {options.encoder})")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(total):
            proc.stdin.write(composer.bgr(n / FPS).tobytes())
            if n % 12 == 0:
                rep.progress(n / total, f"Frame {n} of {total}")
        proc.stdin.close()
        error = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed while encoding:\n{error[-1500:]}")
    except BaseException:
        proc.kill()
        proc.wait()
        partial.unlink(missing_ok=True)
        raise
    partial.replace(out)
    with session_scope() as db:
        row = db.get(Storyboard, storyboard_id)
        row.video_path, row.rendered_at, row.review = str(out), utcnow(), "pending"
    rep.info(f"Rendered {out.name}: {out.stat().st_size / 1e6:.1f} MB. It waits for your review.")
