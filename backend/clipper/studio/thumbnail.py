"""Thumbnails of a long video: three options, A, B and C, for YouTube's A/B test.

YouTube Studio's A/B test ("Test & compare") shows up to three thumbnails to
shares of the audience and keeps the one watched most. Three options that look
alike teach it nothing, so each one takes a different angle:

  A  reaction  a character with a strong expression next to the key thing
  B  contrast  parallel/scorecard: one readable before/after or A/B difference
     stakes    legacy/other shapes: the consequence or number the video turns on
  C  mystery   the key thing pointed at or circled, little or no text

The model plans them (`llm` task `thumbnail`, writing tier) and answers with a
headline and, for ink storyboards, a picture in the beat language (never
coordinates); other styles take a frame of the video by shot number (never a
timestamp). Code draws everything: an ink picture is one frozen shot drawn by
the stage at 1920x1080 with the camera set to fill its half of the frame, and
the headline goes on in Archivo Black with a thick outline. The picture may
use only the people, props, flags and photos of the video itself, so it cannot
show what the video does not.

`check` is arithmetic with every point on its own line: headline length, how
big the text is at the smallest size YouTube shows (a phone's list), the
duration badge's corner, clutter, contrast when small, whether the options
differ enough for the test, an echo of the title, clickbait words, and numbers
the narration never says. Then the quick tier looks at the three at small size
once (`thumbnail_check`), and an option it finds unclear or misleading is
planned again once. The app never uploads: you add them in YouTube Studio.
"""

import base64
import io
import logging
import re
import shutil
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, Field

from .. import config, llm, prompts
from ..ai_errors import AiUnavailable
from ..db import session_scope
from ..db.models import Storyboard, StudioAsset, utcnow
from ..jobs import Reporter
from ..settings import Settings
from . import animate, art, beats, ink
from . import voice as voice_mod

log = logging.getLogger("clipper.studio")

KEYS = ("A", "B", "C")
ANGLES = {
    "reaction": "a character with a strong, readable expression next to the one thing the video is about",
    "contrast": "the before and after, opposing sides, or the one number the video turns on",
    # Read old plans and AI answers written before contrast became canonical.
    "stakes": "the contrast, the before and after, or the one number the video turns on",
    "mystery": "the key thing alone, pointed at or circled, with few words or none: makes the viewer ask why",
}
ORDER = ("reaction", "contrast", "mystery")  # parallel/scorecard A, B, C
LEGACY_ORDER = ("reaction", "stakes", "mystery")
W, H = 1920, 1080                     # YouTube asks for 16:9, at least 1280x720
SMALL_W, SMALL_H = 168, 94            # the smallest size YouTube shows (a phone's list of videos)
MAX_BYTES = 2 * 1024 * 1024           # the upload limit on every device
MAX_WORDS = 4
BADGE = (0.83, 0.85)                  # YouTube draws the duration in this corner (fractions of W and H)
FONT = config.FONTS / "ArchivoBlack-Regular.ttf"
WHITE = (255, 255, 255)
INK_RGB = tuple(int(ink.INK[i:i + 2], 16) for i in (1, 3, 5))
ACCENTS = {"gold": "#ffc531", "warm": "#ff8a3d", "bad": "#ff4b3a", "good": "#55a8ff"}
CLICKBAIT = ("shocking", "insane", "unbelievable", "you won't believe", "gone wrong", "crazy", "mind blowing",
             "mind-blowing", "must see", "must-see", "exposed", "destroyed", "epic", "omg")
STOP = {"the", "a", "an", "of", "in", "on", "to", "and", "is", "it", "your", "you", "how", "why", "what",
        "who", "was", "for", "by", "at", "this", "that", "are", "be"}
ALLOWED_VERBS = ("actor", "do", "face", "draw", "flag", "photo", "arrow", "circle", "underline", "cross", "tick",
                 "shake", "bounce", "pulse")
# The picture has half the frame: the things placed in it go side by side on neighbouring slots of
# the middle row, in the order the plan put them from left to right (not at the stage's far sides,
# nor stacked on one slot).
PLACED = ("actor", "draw", "flag", "photo")
COLUMN = {"left": 0, "top_left": 0, "bottom_left": 0, "center": 1, "top": 1, "bottom": 1, "": 1,
          "right": 2, "top_right": 2, "bottom_right": 2}
NEIGHBOURS = {1: ["center"], 2: ["left", "center"], 3: ["left", "center", "right"]}
FILL = 0.92                           # of the picture zone the things fill
FILL_MARKED = 0.74                    # when a circle goes round them (it is drawn wider than its target)
LINE_GAP = 2                          # words between beat lines in the drawn shot
WORD_SECONDS = 0.5
SETTLE = 4.0                          # seconds after the last line: entrances done, name tags gone


class AiThumbnail(BaseModel):
    angle: Literal["reaction", "contrast", "stakes", "mystery"]
    headline: str = Field(description="At most four words, or empty. Put *stars* around the one word to colour")
    title: int = Field(description="The title option (1, 2 or 3) this thumbnail goes with")
    side: Literal["left", "right"] = Field(description="Where the headline goes; the picture takes the other side")
    accent: Literal["gold", "warm", "bad", "good"] = Field(description="Colour of the starred word")
    beats: str = Field(description="Ink videos: the picture's beat lines, one per line. Other videos: empty")
    shot: int = Field(description="Other videos: the number of the shot whose picture to use. Ink videos: 0")
    why: str = Field(description="One sentence: why a viewer would click this one")


class AiThumbnails(BaseModel):
    thumbnails: list[AiThumbnail]


class Verdict(BaseModel):
    key: str = Field(description="A, B or C")
    clear: bool = Field(description="At this small size you can tell at once what it shows and read the words")
    honest: bool = Field(description="It promises nothing the titles and narration do not deliver")
    note: str = Field(description="What to change, in one sentence; empty when it works")


class Looks(BaseModel):
    verdicts: list[Verdict]


SYSTEM = prompts.Prompt("thumbnail_plan")

INK_PICTURE = prompts.Prompt("thumbnail_ink_picture")

FRAME_PICTURE = prompts.Prompt("thumbnail_frame_picture")

LOOK = prompts.Prompt("thumbnail_check")


# --- files ---------------------------------------------------------------------------------

def folder(board: Storyboard | dict) -> Path:
    """Kept apart from the render folder, which the grace period may delete."""
    script_id, board_id = (board["script_id"], board["id"]) if isinstance(board, dict) else (board.script_id, board.id)
    return voice_mod.script_media_dir(script_id) / f"thumbnails-{board_id}"


def path_of(board: Storyboard, key: str) -> Path | None:
    option = next((o for o in (board.thumbnails or {}).get("options", []) if o["key"] == key), None)
    return Path(option["path"]) if option and option.get("path") else None


def remove(board: Storyboard) -> None:
    shutil.rmtree(folder(board), ignore_errors=True)


# --- what the picture may use ---------------------------------------------------------------

class Cast:
    """The video's own people, props, flags and photos: all a drawn thumbnail may show."""

    def __init__(self, scenes: list[dict], drawn_props: set[str], photos: set[str]):
        self.people: dict[str, str] = {}          # id -> its `who` line without the word number
        self.actors: set[str] = set(art.CHARACTERS)
        self.props: set[str] = set()
        self.flags: dict[str, str] = {}
        self.photos: dict[str, str] = {}
        for sh in scenes:
            for line in beats.parsed(sh):
                if line.verb == "who" and line.who not in self.people:
                    self.people[line.who] = line.render().split(" ", 1)[1]
                elif line.verb == "actor":
                    self.actors.add(line.who)
                elif line.verb == "draw" and line.who in drawn_props:
                    self.props.add(line.who)
                elif line.verb == "flag":
                    self.flags.setdefault(line.who, f"flag {line.who}")
                elif line.verb == "photo" and line.text in photos:
                    self.photos.setdefault(line.text, f'photo "{line.text}"')
        self.actors |= set(self.people)

    def names(self) -> set[str]:
        return self.actors | self.props | {f"flag_{c}" for c in self.flags} | ({"photo"} if self.photos else set())


def picture_lines(text: str, cast: Cast) -> tuple[list[str], list[str]]:
    """The model's beat lines, numbered and kept to the video's cast. Returns (lines, dropped with reasons)."""
    lines: list[str] = []
    dropped: list[str] = []
    people: list[str] = []
    for raw in text.splitlines():
        raw = re.sub(r"^\s*(?:\[\d+\]|\d+)\s+", "", raw.strip().strip("`-• ").strip())
        if not raw:
            continue
        line = beats.parse_line(f"{len(lines) * LINE_GAP} {raw}")
        if line is None:
            dropped.append(f"{raw}: not understood")
            continue
        if line.verb not in ALLOWED_VERBS:
            dropped.append(f"{raw}: '{line.verb}' is not used in a thumbnail")
            continue
        if line.verb in ("actor", "do", "face"):
            if line.who not in cast.actors:
                dropped.append(f"{raw}: {line.who} is not in this video")
                continue
            if line.pose and line.pose not in beats.STILL_POSES:
                line.pose = ""                     # a move is caught mid-air: hold a pose instead
            if line.who in cast.people and line.who not in people:
                people.append(line.who)
        elif line.verb == "draw" and line.who not in cast.props:
            dropped.append(f"{raw}: the prop {line.who} is not in this video")
            continue
        elif line.verb == "flag" and line.who not in cast.flags:
            dropped.append(f"{raw}: that flag is not in this video")
            continue
        elif line.verb == "photo" and line.text not in cast.photos:
            dropped.append(f"{raw}: that picture is not in this video")
            continue
        line.word = len(lines) * LINE_GAP
        lines.append(line)
    placed = sorted((x for x in lines if x.verb in PLACED), key=lambda x: (COLUMN.get(x.slot, 1), x.word))
    slots = NEIGHBOURS.get(len(placed), ["left", "center", "right"])
    for n, x in enumerate(placed):
        x.slot = slots[n % len(slots)]
    # people of the video come with their own description (the first one wins, as in the video)
    who = [f"0 {cast.people[p]}" for p in people]
    return who + [x.render() for x in lines], dropped


# --- drawing --------------------------------------------------------------------------------

def _zones(side: str, headline: str) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """(text zone, picture zone) in pixels."""
    if not headline.strip():
        return (0, 0, 0, 0), (W * 0.06, H * 0.06, W * 0.94, H * 0.94)
    if side == "left":
        return (W * 0.04, H * 0.1, W * 0.5, H * 0.8), (W * 0.49, H * 0.05, W * 0.97, H * 0.95)
    return (W * 0.5, H * 0.1, W * 0.96, H * 0.8), (W * 0.03, H * 0.05, W * 0.51, H * 0.95)


def drawn_picture(board: dict, props: dict, lines: list[str], side: str, headline: str,
                  settings: Settings) -> tuple[Image.Image, dict]:
    """One frozen ink shot, framed so its things fill the picture zone. Returns the image and measures."""
    from .stage import BODIES

    body = [x for x in lines if not x.split(" ", 2)[1] == "who"]
    last = max([int(x.split(" ", 1)[0]) for x in body] or [0])
    count = last + LINE_GAP + 1
    words = [{"w": "-", "s": i * WORD_SECONDS, "e": i * WORD_SECONDS + 0.4} for i in range(count)]
    duration = count * WORD_SECONDS + SETTLE
    shots, _ = beats.clean([beats.shot(0, "thumbnail", "wide", lines=lines)], count)
    data = {**board, "words": words, "scenes": shots, "start": 0.0, "end": duration, "headings": {}}
    if data.get("places"):
        data["places"] = data["places"][:1]       # the video's first place, behind the one shot
    composer = animate.composer_for(data, props, settings, None)
    t = duration - 0.05
    things = [x for x in composer.things
              if x.kind in BODIES and x.born <= t and (x.gone is None or x.gone > t)]
    _, zone = _zones(side, headline)
    measures = {"things": len(things), "subject": 0.0}
    if things:
        boxes = [composer.body_box(x, t) if x.kind == "prop" else composer.world_box(x, t) for x in things]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        zw, zh = zone[2] - zone[0], zone[3] - zone[1]
        fill = FILL_MARKED if any(x.split(" ", 2)[1] == "circle" for x in lines) else FILL
        zoom = max(0.6, min(zw * fill / max(x1 - x0, 1), zh * fill / max(y1 - y0, 1), 2.6))
        zcx, zcy = (zone[0] + zone[2]) / 2, (zone[1] + zone[3]) / 2
        view = ((x0 + x1) / 2 - (zcx - W / 2) / zoom, (y0 + y1) / 2 - (zcy - H / 2) / zoom, zoom)
        composer.keyframes = [(0.0, 0.0, view, view, 0)]
        measures["subject"] = round(max(b[3] - b[1] for b in boxes) * zoom / H, 3)   # the biggest thing's height
    return composer.frame(t).convert("RGB"), measures


def shot_time(style: str, start: float, end: float) -> float:
    """A moment of a shot with everything on it, before the camera leaves (as the editor's stills)."""
    if style == "film":
        return max(start + 0.3, end - 1.3)
    if style == "ink":
        return max(start, end - 0.45)
    return max(start + 0.5, min(end - 0.4, start + (end - start) * 0.7))


def shot_times(board: dict) -> list[tuple[float, float]]:
    words, scenes = board["words"], board["scenes"]
    duration = board["end"] - board["start"]
    if not words or not scenes:
        return [(0.0, duration)]
    starts = [0.0] + [words[min(sh["start_word"], len(words) - 1)]["s"] for sh in scenes[1:]]
    return list(zip(starts, [*starts[1:], duration], strict=True))


def starred(headline: str) -> list[tuple[str, bool]]:
    """The headline's words in capitals, each with whether it is between *stars* (coloured)."""
    out = []
    inside = False
    for word in headline.upper().split():
        stars = word.count("*")
        out.append((word.replace("*", ""), inside or stars > 0))
        if stars % 2:
            inside = not inside
    return [(w, on) for w, on in out if w]


def _splits(count: int, rows: int):
    """Every way to cut `count` words into `rows` lines, in order."""
    if rows == 1:
        yield [count]
        return
    for first in range(1, count - rows + 2):
        for rest in _splits(count - first, rows - 1):
            yield [first, *rest]


def _fit(words: list[str], zone: tuple[float, float, float, float]) -> tuple[list[list[int]], int]:
    """Line breaks (word indexes per line) and the biggest font size that fits the zone."""
    zw, zh = zone[2] - zone[0], zone[3] - zone[1]
    probe = ImageFont.truetype(str(FONT), 100)
    best: tuple[list[list[int]], int] = ([list(range(len(words)))], 40)
    for rows in range(1, min(3, len(words)) + 1):
        for cut in _splits(len(words), rows):
            lines, at = [], 0
            for n in cut:
                lines.append(list(range(at, at + n)))
                at += n
            widest = max(probe.getlength(" ".join(words[i] for i in line)) for line in lines) / 100
            size = int(min(260, zw / 1.1 / widest, zh / (rows * 1.08)))
            if size > best[1]:
                best = (lines, size)
    return best


def headline_layer(headline: str, side: str, accent: str, plate: bool) -> tuple[Image.Image, dict]:
    """The headline on a transparent layer, and where it went."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    marked = starred(headline)
    words = [w for w, _ in marked]
    if not words:
        return layer, {"size": 0, "box": None}
    zone, _ = _zones(side, headline)
    breaks, size = _fit(words, zone)
    rows = [[marked[i] for i in line] for line in breaks]
    font = ImageFont.truetype(str(FONT), size)
    draw = ImageDraw.Draw(layer)
    stroke = max(4, round(size * 0.09))
    line_h = size * 1.08
    total = line_h * len(rows)
    top = (zone[1] + zone[3]) / 2 - total / 2
    colour = ACCENTS.get(accent, ACCENTS["gold"])
    boxes = []
    for r, row in enumerate(rows):
        text = " ".join(w for w, _ in row)
        width = font.getlength(text)
        x = zone[0] if side == "left" else zone[2] - width
        y = top + r * line_h
        boxes.append((x, y, x + width, y + size))
    x0, y0 = min(b[0] for b in boxes) - stroke, min(b[1] for b in boxes) - stroke
    x1, y1 = max(b[2] for b in boxes) + stroke, max(b[3] for b in boxes) + stroke * 2
    if plate:                    # a frame of the video is busy: the words sit on a dark card
        pad = size * 0.22
        draw.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=round(size * 0.18),
                               fill=(*INK_RGB, 225))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for r, row in enumerate(rows):
        bx, by = boxes[r][0], boxes[r][1]
        sd.text((bx + stroke * 0.8, by + stroke * 1.2), " ".join(w for w, _ in row), font=font,
                fill=(0, 0, 0, 150), stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
    layer = Image.alpha_composite(layer, shadow)
    draw = ImageDraw.Draw(layer)
    space = font.getlength(" ")
    for r, row in enumerate(rows):
        x, y = boxes[r][0], boxes[r][1]
        for word, on in row:
            draw.text((x, y), word, font=font, fill=colour if on else WHITE,
                      stroke_width=stroke, stroke_fill=INK_RGB)
            x += font.getlength(word) + space
    return layer, {"size": size, "box": (x0, y0, x1, y1)}


def compose(picture: Image.Image, option: dict) -> tuple[Image.Image, dict]:
    """The picture with its headline: the final thumbnail at 1920x1080."""
    picture = ImageOps.fit(picture.convert("RGB"), (W, H), Image.LANCZOS)
    headline = option.get("headline", "")
    side = option.get("side", "left")
    # Contrast thumbnails have a deliberately legible two-zone treatment: a dark text
    # panel and a clean picture panel. It is deterministic and remains readable at phone size.
    if option.get("angle") == "contrast" and headline.strip():
        zone, _ = _zones(side, headline)
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel)
        pd.rectangle(zone, fill=(*INK_RGB, 238))
        x = zone[2] if side == "left" else zone[0]
        pd.rectangle((x - 8, 0, x + 8, H), fill=(*ACCENTS.get(option.get("accent", "gold"), ACCENTS["gold"]), 255))
        picture = Image.alpha_composite(picture.convert("RGBA"), panel).convert("RGB")
    layer, placed = headline_layer(headline, side,
                                   option.get("accent", "gold"),
                                   plate=option.get("mode") == "frame" and option.get("angle") != "contrast")
    out = Image.alpha_composite(picture.convert("RGBA"), layer).convert("RGB")
    return out, placed


def jpeg(image: Image.Image) -> bytes:
    """As a JPEG under YouTube's 2 MB."""
    for quality in (92, 86, 80, 72, 64):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
        if buffer.tell() <= MAX_BYTES:
            return buffer.getvalue()
    return buffer.getvalue()


def small(image: Image.Image) -> Image.Image:
    return image.resize((SMALL_W, SMALL_H), Image.LANCZOS)


# --- the check ------------------------------------------------------------------------------

def _plain(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("*", ""))


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,.]*", text.replace("*", ""))


def check(option: dict, image: Image.Image, others: dict[str, Image.Image], titles: list[str],
          narration: str) -> dict:
    """Out of 100, every point on its own line; `problems` are what could break YouTube's rules."""
    lines: list[dict] = []
    problems: list[str] = []

    def line(text: str, points: int = 0) -> None:
        lines.append({"text": text, "points": points})

    headline = option.get("headline", "").strip()
    words = _plain(headline)
    if not words:
        line("No headline: the picture does all the work", -5 if option.get("angle") != "mystery" else 0)
    elif len(words) <= MAX_WORDS:
        line(f"Headline: {len(words)} word{'s' if len(words) > 1 else ''}, read at a glance")
    else:
        line(f"Headline: {len(words)} words, more than {MAX_WORDS} are skimmed past", -10 * (len(words) - MAX_WORDS))
    size = option.get("measures", {}).get("text_size", 0)
    if words:
        cap = size * 0.72 * SMALL_W / W
        if cap >= 9:
            line(f"Letters {cap:.0f} px tall at the smallest size YouTube shows: readable")
        elif cap >= 6:
            line(f"Letters {cap:.0f} px tall at the smallest size: hard to read on a phone", -10)
        else:
            line(f"Letters {cap:.0f} px tall at the smallest size: unreadable on a phone", -25)
        box = option.get("measures", {}).get("text_box")
        if box and box[2] > W * BADGE[0] and box[3] > H * BADGE[1]:
            line("The headline runs into the corner where YouTube shows the duration", -15)
    things = option.get("measures", {}).get("things")
    if things is not None:
        if things > 4:
            line(f"{things} things in the picture: cluttered when small", -5 * (things - 4))
        elif things == 0:
            line("Nothing drawn in the picture", -30)
        else:
            line(f"{things} thing{'s' if things > 1 else ''} in the picture: one idea")
    subject = option.get("measures", {}).get("subject")
    if subject is not None and things:
        if subject >= 0.3:
            line(f"The biggest thing is {subject:.0%} of the frame's height")
        else:
            line(f"The biggest thing is only {subject:.0%} of the frame's height: small on a phone", -10)
    gray = np.asarray(small(image).convert("L"), dtype=np.float32)
    spread = float(gray.std())
    if spread >= 40:
        line(f"Strong contrast when small ({spread:.0f})")
    else:
        line(f"Weak contrast when small ({spread:.0f}): it looks flat in a list", -10)
    for key, other in sorted(others.items()):
        diff = float(np.abs(gray - np.asarray(small(other).convert("L"), dtype=np.float32)).mean())
        if diff < 14:
            line(f"Too close to {key} ({diff:.0f}): the test may find no winner", -15)
    title = option.get("title_text") or (titles[option.get("title", 1) - 1]
                                         if titles and 0 < option.get("title", 1) <= len(titles) else "")
    meaningful = [w for w in words if w not in STOP]
    if title and meaningful:
        echo = sum(w in _plain(title) for w in meaningful) / len(meaningful)
        if echo >= 0.75:
            line("The headline repeats its title: say something the title does not", -10)
    low = headline.lower().replace("*", "")
    for bait in CLICKBAIT:
        if re.search(rf"\b{re.escape(bait)}\b", low):
            line(f"Clickbait word: '{bait}'", -25)
            problems.append(f"'{bait}' reads as clickbait; YouTube removes thumbnails that mislead")
    said = {n.replace(",", "").rstrip(".") for n in _numbers(narration)}
    for number in _numbers(headline):
        if number.replace(",", "").rstrip(".") not in said:
            line(f"The number {number} is not in the narration", -30)
            problems.append(f"The number {number} is not in the narration: a thumbnail must not promise "
                            "what the video does not say")
    if option.get("bytes", 0) > MAX_BYTES:
        problems.append("The file is over 2 MB, YouTube's limit")
    if option.get("dropped"):
        line(f"{len(option['dropped'])} line{'s' if len(option['dropped']) > 1 else ''} of the plan left out "
             "(not in this video)")
    score = max(0, min(100, 100 + sum(x["points"] for x in lines)))
    return {"score": score, "lines": lines, "problems": problems}


# --- the job --------------------------------------------------------------------------------

def _narration(board: Storyboard) -> str:
    sections = board.script.sections
    if board.section_key:
        sections = [s for s in sections if s["key"] == board.section_key]
    return "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in sections)


class Kit:
    """What one job needs: the board as the renderer reads it, its cast and composer."""

    def __init__(self, storyboard_id: int, settings: Settings):
        self.data, self.props, _ = animate.load(storyboard_id)
        self.settings = settings
        self.style = self.data["style"]
        with session_scope() as db:
            board = db.get(Storyboard, storyboard_id)
            self.title = board.title
            self.story_shape = getattr(board, "story_shape", None)
            self.titles = list((board.youtube or {}).get("titles") or [board.title])
            self.narration = _narration(board)
            self.audience = _audience()
            names = beats.prop_names(board.scenes) if self.style == "ink" else set()
            drawn = {a.name for a in db.query(StudioAsset).filter(StudioAsset.name.in_(names),
                                                                    StudioAsset.svg.is_not(None))} if names else set()
            scenes = list(board.scenes)
        self.cast = Cast(scenes, drawn, set(self.data.get("photos", {}))) if self.style == "ink" else None
        self.times = shot_times(self.data)
        self._composer = None

    @property
    def drawn(self) -> bool:
        return self.style == "ink"

    def composer(self):
        if self._composer is None:
            self._composer = animate.composer_for(self.data, self.props, self.settings, None)
        return self._composer

    def shot_list(self) -> str:
        out = []
        words = self.data["words"]
        for i, sh in enumerate(self.data["scenes"]):
            start = sh.get("start_word", 0)
            end = self.data["scenes"][i + 1].get("start_word", len(words)) if i + 1 < len(self.data["scenes"]) \
                else len(words)
            said = " ".join(w["w"] for w in words[start:end])[:160]
            note = sh.get("note") or sh.get("caption") or ""
            out.append(f"[{i + 1}] {note} | narration: {said}")
        return "\n".join(out)

    def frame_at(self, at: float) -> Image.Image:
        composer = self.composer()
        return composer.frame(max(0.0, min(at, composer.duration - 0.01))).convert("RGB")


def _audience() -> str:
    from . import plain

    return plain.audience()


def system(kit: Kit, count: int) -> str:
    if kit.drawn:
        cast = kit.cast
        shown = ""
        if cast.flags:
            shown += ("  flag CODE [SLOT] [SIZE]                         a flag of this video: "
                      + ", ".join(cast.flags) + "\n")
        if cast.photos:
            shown += ("  photo \"SEARCH\" [SLOT] [SIZE]                   an archive picture of this video: "
                      + "; ".join(f'"{q}"' for q in cast.photos) + "\n")
        picture = INK_PICTURE.format(cast=", ".join(sorted(cast.actors)), props=", ".join(sorted(cast.props)) or "none",
                                     shown=shown, poses=", ".join(beats.STILL_POSES),
                                     expressions=", ".join(beats.EXPRESSIONS))
    else:
        picture = FRAME_PICTURE.format(shots=kit.shot_list())
    wanted = angle_order(getattr(kit, "story_shape", None))
    many = (f"Plan exactly three thumbnails, one per angle, in this order: {', '.join(wanted)}. They must look "
            "clearly different from each other, or the test finds no winner.") if count == 3 else \
        "Plan one thumbnail, for the angle asked."
    return SYSTEM.format(count=many, audience=kit.audience, picture=picture, **ANGLES)


def canonical_angle(angle: str) -> str:
    """Map the pre-contrast name to the canonical angle without breaking old JSON."""
    return "contrast" if angle == "stakes" else angle


def angle_order(story_shape: str | None = None) -> tuple[str, str, str]:
    """Use contrast for comparison shapes and preserve the established policy otherwise."""
    return ORDER if story_shape in {"parallel", "scorecard"} else LEGACY_ORDER


def matches_angle(candidate: str, wanted: str) -> bool:
    """Accept an old stakes answer as contrast only when contrast was requested."""
    return candidate == wanted or (wanted == "contrast" and candidate == "stakes")


def _content(kit: Kit, extra: str = "") -> str:
    titles = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(kit.titles))
    return (f"<video>A long YouTube video; working title: {kit.title}</video>\n<titles>\n{titles}\n</titles>\n"
            f"<narration>\n{kit.narration}\n</narration>{extra}")


def from_ai(kit: Kit, idea: AiThumbnail, key: str) -> dict:
    """An option as stored, from the model's idea (not drawn yet)."""
    headline = " ".join(idea.headline.replace("#", "").split())
    option = {"key": key, "angle": idea.angle, "headline": headline, "accent": idea.accent, "side": idea.side,
              "title": idea.title if 1 <= idea.title <= len(kit.titles) else 1, "why": idea.why.strip()[:300],
              "by": "ai", "lines": [], "dropped": [], "mode": "drawn" if kit.drawn else "frame", "at": None}
    option["title_text"] = kit.titles[option["title"] - 1]
    if kit.drawn:
        option["lines"], option["dropped"] = picture_lines(idea.beats, kit.cast)
        if not [x for x in option["lines"] if " who " not in f" {x} "]:
            option["mode"] = "frame"             # nothing usable: take a frame of the video instead
    if option["mode"] == "frame":
        shot = max(1, min(idea.shot or 1, len(kit.times)))
        option["shot"] = shot
        option["at"] = round(shot_time(kit.style, *kit.times[shot - 1]), 2)
    return option


def by_rules(kit: Kit) -> list[dict]:
    """No AI: three frames of the video, from its beginning, middle and end, the busiest shot of each part."""
    scenes = kit.data["scenes"]
    count = len(scenes)

    def busy(i: int) -> int:
        sh = scenes[i]
        return len(sh.get("lines") or sh.get("elements") or [])

    picks: list[int] = []
    for part in range(3):
        span = range(count * part // 3, max(count * (part + 1) // 3, count * part // 3 + 1))
        options = [i for i in span if i < count and i not in picks]
        if options:
            picks.append(max(options, key=busy))
    while len(picks) < 3:
        picks.append(picks[-1] if picks else 0)
    out = []
    for key, angle, i in zip(KEYS, angle_order(getattr(kit, "story_shape", None)), picks, strict=True):
        out.append({"key": key, "angle": angle, "headline": "", "accent": "gold", "side": "left", "title": 1,
                    "why": "Picked by rules: the busiest shot of this part of the video", "by": "rules",
                    "title_text": kit.titles[0],
                    "lines": [], "dropped": [], "mode": "frame", "shot": i + 1,
                    "at": round(shot_time(kit.style, *kit.times[min(i, len(kit.times) - 1)]), 2)})
    return out


def draw(kit: Kit, option: dict, target: Path) -> tuple[Image.Image, dict]:
    """Draw one option to `target` (a fresh file name). Returns the image and the option with measures."""
    if option["mode"] == "drawn" and option.get("lines"):
        picture, measures = drawn_picture(kit.data, kit.props, option["lines"], option["side"], option["headline"],
                                          kit.settings)
    else:
        picture, measures = kit.frame_at(option.get("at") or 0.0), {}
    image, placed = compose(picture, option)
    data = jpeg(image)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    measures = {**measures, "text_size": placed["size"], "text_box": placed["box"]}
    return image, {**option, "path": str(target), "bytes": len(data), "measures": measures}


def _next_path(board_folder: Path, key: str, old: str | None) -> Path:
    """A new name per drawing, so a browser never shows the old picture."""
    n = 1
    if old:
        m = re.search(rf"{key}-(\d+)\.jpg$", old)
        n = int(m.group(1)) + 1 if m else 1
    return board_folder / f"{key}-{n}.jpg"


def _opened(option: dict) -> Image.Image:
    path = Path(option.get("path") or "")
    return Image.open(path).convert("RGB") if path.is_file() else Image.new("RGB", (W, H))


def recheck(kit: Kit, options: list[dict], images: dict[str, Image.Image]) -> None:
    for option in options:
        others = {k: v for k, v in images.items() if k != option["key"]}
        option["check"] = check(option, images[option["key"]], others, kit.titles, kit.narration)


def sheet(images: dict[str, Image.Image]) -> bytes:
    """The options side by side at twice the smallest size, labelled, for the quick tier to look at."""
    w, h, gap = SMALL_W * 2, SMALL_H * 2, 24
    out = Image.new("RGB", (len(images) * (w + gap) + gap, h + 60), (245, 245, 245))
    draw_ = ImageDraw.Draw(out)
    font = ImageFont.truetype(str(FONT), 22)
    for n, (key, image) in enumerate(sorted(images.items())):
        x = gap + n * (w + gap)
        out.paste(image.resize((w, h), Image.LANCZOS), (x, 44))
        draw_.text((x, 10), key, font=font, fill=(20, 20, 20))
    buffer = io.BytesIO()
    out.save(buffer, "PNG")
    return buffer.getvalue()


def look(kit: Kit, options: list[dict], images: dict[str, Image.Image]) -> dict[str, Verdict]:
    """The quick tier looks at the options at small size. A failure means no verdicts, said by the caller."""
    titles = "\n".join(f"{o['key']} goes with the title: {o.get('title_text') or kit.title}" for o in options)
    content = [{"type": "text", "text": f"{_content(kit)}\n<thumbnails>\n{titles}\n</thumbnails>"},
               {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.standard_b64encode(sheet(images)).decode()}}]
    answer: Looks = llm.ask("thumbnail_check", LOOK, content, Looks, effort="low")
    return {v.key.strip().upper()[:1]: v for v in answer.verdicts}


def run(storyboard_id: int, settings: Settings, rep: Reporter, only: str | None = None, note: str = "") -> None:
    """Plan and draw the three thumbnails (or plan `only` one again, with your note), check and look at them."""
    kit = Kit(storyboard_id, settings)
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        if board.format != "long":
            raise RuntimeError("Thumbnails are for long videos: a Short's cover is chosen in the YouTube app")
        stored = dict(board.thumbnails or {})
        target_folder = folder(board)
    current = {o["key"]: o for o in stored.get("options", [])}
    if only and only not in current:
        raise RuntimeError("Make the three thumbnails first")
    rep.info(f"{'Planning thumbnail ' + only if only else 'Planning three thumbnails'} "
             f"({'drawn in the ink style' if kit.drawn else 'frames of the video'})")
    fresh: dict[str, dict] = {}
    by = "ai"
    try:
        if only:
            angle = current[only]["angle"]
            extra = f"\n<angle>{angle}</angle>"
            if note:
                extra += f"\n<request>{note}</request>"
            others = "; ".join(f"{k}: {o['headline'] or '(no headline)'}" for k, o in current.items() if k != only)
            extra += f"\n<other_thumbnails>{others}</other_thumbnails>"
            answer: AiThumbnails = llm.ask("thumbnail", system(kit, 1), _content(kit, extra), AiThumbnails,
                                           effort="medium")
            if not answer.thumbnails:
                raise RuntimeError("The AI planned no thumbnail")
            fresh[only] = from_ai(kit, answer.thumbnails[0], only)
            fresh[only]["angle"] = angle
        else:
            answer = llm.ask("thumbnail", system(kit, 3), _content(kit), AiThumbnails, effort="medium")
            ideas = list(answer.thumbnails)
            for angle in angle_order(getattr(kit, "story_shape", None)):      # one per angle, in order
                if not any(matches_angle(i.angle, angle) for i in ideas):
                    rep.warn(f"The AI planned no {angle} thumbnail")
            ordered = [next((i for i in ideas if matches_angle(i.angle, a)), None)
                       for a in angle_order(getattr(kit, "story_shape", None))]
            spare = [i for i in ideas if i not in ordered]
            for key, angle, idea in zip(KEYS, angle_order(getattr(kit, "story_shape", None)), ordered, strict=True):
                idea = idea or (spare.pop(0) if spare else None)
                if idea is None:
                    raise RuntimeError("The AI planned fewer than three thumbnails")
                fresh[key] = {**from_ai(kit, idea, key), "angle": angle}
    except AiUnavailable as err:
        if only or settings.ai_fallback != "rules":
            raise
        rep.warn(f"AI unavailable ({err}): three frames of the video picked by rules, without headlines. "
                 "Add the headlines yourself, or make them again when the AI is back.")
        fresh = {o["key"]: o for o in by_rules(kit)}
        by = "rules"
    for option in fresh.values():
        for line in option.get("dropped", []):
            rep.warn(f"{option['key']}: left out {line}")

    images: dict[str, Image.Image] = {}
    options = [fresh.get(k) or current[k] for k in KEYS]
    for n, option in enumerate(options):
        key = option["key"]
        if key in fresh:
            old = current.get(key, {}).get("path")
            image, drawn = draw(kit, option, _next_path(target_folder, key, old))
            options[n] = drawn
            images[key] = image
            if old and old != drawn["path"]:
                Path(old).unlink(missing_ok=True)
            rep.progress((n + 1) / 3 * 0.6, f"Drew thumbnail {key}")
        else:
            images[key] = _opened(option)
    recheck(kit, options, images)

    if by == "ai":
        try:
            verdicts = look(kit, options, images)
        except AiUnavailable as err:
            verdicts = {}
            rep.warn(f"The AI could not look at the thumbnails ({err}); the arithmetic check stands alone")
        except Exception as err:  # noqa: BLE001 - an unlooked thumbnail is still a thumbnail
            verdicts = {}
            rep.warn(f"The AI's look at the thumbnails could not be read ({str(err)[:160]})")
        for n, option in enumerate(options):
            verdict = verdicts.get(option["key"])
            if verdict is None or option["key"] not in fresh:
                continue
            option["look"] = verdict.model_dump()
            if verdict.clear and verdict.honest:
                rep.info(f"{option['key']}: the AI finds it clear and honest")
                continue
            rep.info(f"{option['key']}: the AI finds it {'unclear' if not verdict.clear else 'misleading'} "
                     f"({verdict.note}); planning it once more")
            try:
                extra = (f"\n<angle>{option['angle']}</angle>\n<request>Your last plan for this one was judged: "
                         f"{verdict.note}</request>")
                again: AiThumbnails = llm.ask("thumbnail", system(kit, 1), _content(kit, extra), AiThumbnails,
                                              effort="medium")
                if not again.thumbnails:
                    continue
                redo = {**from_ai(kit, again.thumbnails[0], option["key"]), "angle": option["angle"]}
                image, drawn = draw(kit, redo, _next_path(target_folder, option["key"], option["path"]))
                Path(option["path"]).unlink(missing_ok=True)
                drawn["look"] = {**verdict.model_dump(), "redrawn": True}
                options[n] = drawn
                images[option["key"]] = image
            except AiUnavailable as err:
                rep.warn(f"{option['key']}: could not plan it again ({err}); kept as it was")
        recheck(kit, options, images)

    for option in options:
        c = option["check"]
        rep.info(f"{option['key']} ({option['angle']}): {option['headline'] or 'no headline'}, check {c['score']}/100"
                 + (f"; {len(c['problems'])} problem{'s' if len(c['problems']) > 1 else ''}" if c["problems"] else ""))
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.thumbnails = {"options": options, "made_at": utcnow().isoformat(),
                            "by": by if not only else stored.get("by", "ai"),
                            # These are deliberately separate: neither is inferred from CTR.
                            "selected_key": stored.get("selected_key"),
                            "winner_key": stored.get("winner_key")}


_UNSET = object()


def record_result(storyboard_id: int, *, selected_key: str | None | object = _UNSET,
                  winner_key: str | None | object = _UNSET) -> dict:
    """Record creator choice and manually observed A/B winner, never analytics inference."""
    if selected_key is not _UNSET and selected_key is not None and selected_key not in KEYS:
        raise KeyError(selected_key)
    if winner_key is not _UNSET and winner_key is not None and winner_key not in KEYS:
        raise KeyError(winner_key)
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        value = dict(board.thumbnails or {})
        if not value:
            raise KeyError("thumbnails")
        if selected_key is not _UNSET:
            value["selected_key"] = selected_key
        if winner_key is not _UNSET:
            value["winner_key"] = winner_key
        board.thumbnails = value
        return public(value)


def edit(storyboard_id: int, key: str, change: dict, settings: Settings) -> dict:
    """Your change to one option (headline, colour, side, title, picture): drawn again, no AI."""
    kit = Kit(storyboard_id, settings)
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        stored = dict(board.thumbnails or {})
        target_folder = folder(board)
    options = [dict(o) for o in stored.get("options", [])]
    index = next((i for i, o in enumerate(options) if o["key"] == key), None)
    if index is None:
        raise KeyError(key)
    option = {**options[index]}
    for field in ("headline", "accent", "side", "title", "mode"):
        if change.get(field) is not None:
            option[field] = change[field]
    option["headline"] = " ".join(str(option["headline"]).split())
    option["title"] = max(1, min(int(option.get("title") or 1), len(kit.titles)))
    if change.get("title") is not None or not option.get("title_text"):
        option["title_text"] = kit.titles[option["title"] - 1]
    if change.get("beats") is not None and kit.drawn:
        option["lines"], option["dropped"] = picture_lines(change["beats"], kit.cast)
    if change.get("at") is not None:
        option["at"] = max(0.0, float(change["at"]))
    if option["mode"] == "drawn" and (not kit.drawn or not option.get("lines")):
        option["mode"] = "frame"
    if option["mode"] == "frame" and option.get("at") is None:
        option["at"] = round(shot_time(kit.style, *kit.times[0]), 2)
    option["by"], option["edited"] = "you", True
    option.pop("look", None)                     # the AI looked at the old one
    old = option.get("path")
    image, drawn = draw(kit, option, _next_path(target_folder, key, old))
    if old and old != drawn["path"]:
        Path(old).unlink(missing_ok=True)
    options[index] = drawn
    images = {o["key"]: image if o["key"] == key else _opened(o) for o in options}
    recheck(kit, options, images)
    stored["options"] = options
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        board.thumbnails = stored
    return stored


def beats_text(option: dict) -> str:
    """The picture's lines as you edit them: without word numbers or the people's descriptions."""
    out = []
    for raw in option.get("lines", []):
        word, rest = raw.split(" ", 1)
        if rest.startswith("who "):
            continue
        out.append(rest)
    return "\n".join(out)


def public(stored: dict | None) -> dict | None:
    """For the page: the options without file paths."""
    if not stored:
        return None
    options = []
    for o in stored.get("options", []):
        options.append({k: v for k, v in o.items() if k not in ("path", "lines", "measures")}
                       | {"beats": beats_text(o), "version": Path(o.get("path") or "x-0.jpg").stem.split("-")[-1]})
    return {**{k: v for k, v in stored.items() if k != "options"}, "options": options}
