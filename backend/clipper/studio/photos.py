"""Archive photos for the ink style: paintings, engravings, documents and photographs.

Found on Wikimedia Commons through its public API (no scraping, no key), and
only under licences that are safe in a monetised video: public domain, CC0 and
CC BY (credit the author). Share-alike, no-derivatives and non-commercial files
are left out, for the same reasons as music: share-alike would bind the whole
video, a Ken Burns move is an adaptation, and the channel is commercial.

A search (`photo "Pope Gregory XIII portrait"` in a storyboard) is resolved
once and kept in `studio_photos`, so every video that asks for it shows the
same picture and its credit is always at hand. "Try another" takes the next
result. On the page a photo is an aged print taped to the paper, or fills the
frame, and moves slowly (a Ken Burns move).
"""

import io
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .. import config
from ..db import session_scope
from ..db.models import StudioPhoto, utcnow
from . import ink

API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "ClipperStudio/1.0 (self-hosted video tool)"}
MAX_BYTES = 25 * 1024 * 1024
MIN_SIDE = 500                   # smaller files look soft on a 1080p frame
STORE_SIDE = 1800                # what is kept on disk
SAFE = re.compile(r"^(public domain|pd\b|pdm|cc0|cc[ -]by[ -]\d|cc[ -]by$|no restrictions)", re.I)
UNSAFE = re.compile(r"\b(sa|nc|nd)\b|share|non-?commercial|no-?deriv", re.I)


def folder() -> Path:
    path = config.data_dir() / "studio" / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def allowed(license_name: str) -> bool:
    name = (license_name or "").strip()
    return bool(SAFE.match(name)) and not UNSAFE.search(name.replace("CC BY", "").replace("cc by", ""))


def _text(meta: dict, key: str) -> str:
    raw = (meta.get(key) or {}).get("value") or ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(raw))).strip()


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Wikimedia Commons error ({err.code})") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError("Could not reach Wikimedia Commons. Check your connection.") from err


STOP = {"with", "from", "that", "this", "into", "over", "about", "photo", "picture", "image", "painting", "portrait",
        "drawing", "engraving", "illustration", "the", "and", "of"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if (len(w) >= 4 or w.isdigit()) and w not in STOP}


def relevance(query: str, text: str) -> float:
    """The share of the search's meaningful words found in the file's title and description."""
    wanted = _words(query)
    return len(wanted & _words(text)) / len(wanted) if wanted else 1.0


def search(query: str, limit: int = 15) -> list[dict]:
    """Pictures for the search, best first, with what is needed to credit them.

    Unsafe licences are left out, and so are files whose title and description
    share less than half of the search's meaningful words: Commons matches any
    word, so "Julian calendar manuscript" would otherwise bring any manuscript.
    """
    data = _get({"action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
                 "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
                 "iiprop": "url|size|extmetadata|mime", "iiurlwidth": 1600})
    out = []
    for page in sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 99)):
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        license_name = _text(meta, "LicenseShortName")
        small = min(info.get("width", 0), info.get("height", 0)) < MIN_SIDE
        if info.get("mime") not in ("image/jpeg", "image/png") or small:
            continue
        if not allowed(license_name):
            continue
        title = re.sub(r"^File:", "", page.get("title", "")).rsplit(".", 1)[0]
        score = relevance(query, " ".join((title, _text(meta, "ImageDescription"), _text(meta, "ObjectName"),
                                           _text(meta, "Categories"))))
        if score < 0.6:
            continue
        out.append({"score": score,"key": str(page.get("pageid")), "title": title.replace("_", " ")[:200],
                    "creator": _once(_text(meta, "Artist"))[:200] or "unknown author",
                    "license": license_name[:60], "license_url": _text(meta, "LicenseUrl")[:300],
                    "landing_url": info.get("descriptionurl", ""), "image_url": info.get("thumburl") or info.get("url"),
                    "width": info.get("width", 0), "height": info.get("height", 0)})
    out.sort(key=lambda c: -c["score"])         # stable: Commons' own order among equals
    return out


def _once(text: str) -> str:
    """Commons often says a name twice ("Unknown author Unknown author", a label and its tooltip): once is enough."""
    words = text.split()
    half = len(words) // 2
    if half and len(words) % 2 == 0 and words[:half] == words[half:]:
        return " ".join(words[:half])
    return text


def credit(photo: dict) -> str:
    """The line for the video description."""
    photo = {**photo, "creator": _once(photo["creator"])}
    if re.match(r"(public domain|pd|pdm|cc0|no restrictions)", photo["license"], re.I):
        return (f"\"{photo['title']}\" by {photo['creator']}, public domain, via Wikimedia Commons "
                f"({photo['landing_url']})")
    return (f"\"{photo['title']}\" by {photo['creator']}, licensed under {photo['license']}"
            f"{' (' + photo['license_url'] + ')' if photo['license_url'] else ''}, via Wikimedia Commons "
            f"({photo['landing_url']})")


def _download(url: str, target: Path) -> tuple[int, int]:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError("Could not download the picture. Try another one.") from err
    if len(data) > MAX_BYTES:
        raise RuntimeError("The picture is larger than 25 MB; skipped")
    image = Image.open(io.BytesIO(data))
    image.thumbnail((STORE_SIDE, STORE_SIDE))
    image.convert("RGB").save(target, "JPEG", quality=90)
    return image.size


def find(query: str, rank: int = 0) -> StudioPhoto | None:
    """Resolve a search to a picture (the `rank`-th usable result), download it and remember it."""
    query = query.strip()[:200]
    results = search(query)
    if not results:              # too specific: the first words carry the name ("Graz", "Bede Historia")
        words = query.split()
        if len(words) > 2:
            results = search(" ".join(words[:2]))
    for candidate in results[rank:]:
        target = folder() / f"commons-{candidate['key']}.jpg"
        try:
            size = _download(candidate["image_url"], target) if not target.is_file() else Image.open(target).size
        except (RuntimeError, OSError):
            rank += 1
            continue
        with session_scope() as db:
            row = db.query(StudioPhoto).filter(StudioPhoto.query == query).one_or_none()
            if row is None:
                row = StudioPhoto(query=query)
                db.add(row)
            row.rank, row.key, row.source = rank, candidate["key"], "Wikimedia Commons"
            row.title, row.creator, row.license = candidate["title"], candidate["creator"], candidate["license"]
            row.license_url, row.landing_url = candidate["license_url"], candidate["landing_url"]
            row.credit = credit(candidate)
            row.width, row.height, row.path, row.updated_at = size[0], size[1], str(target), utcnow()
            db.flush()
            db.expunge(row)
            return row
    return None


# --- on the page -------------------------------------------------------------------------------

@lru_cache(maxsize=24)
def _aged(path: str) -> Image.Image:
    """The picture as an old print: a little faded, warmed towards the paper, with grain."""
    image = Image.open(path).convert("RGB")
    a = np.asarray(image, dtype=np.float32)
    grey = a.mean(axis=2, keepdims=True)
    a = a * 0.65 + grey * 0.35                                         # less saturated
    paper = np.array(ink._rgb(ink.PAPER), np.float32)
    a = a * 0.88 + paper * 0.12                                        # printed on warm paper
    a = (a - 128) * 0.92 + 128                                         # softer contrast
    rng = np.random.default_rng(len(path))
    a += rng.normal(0, 4, a.shape[:2])[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


@lru_cache(maxsize=8)
def _backdrop(path: str, width: int, height: int) -> Image.Image:
    src = _aged(path)
    sw, sh = src.size
    aspect = width / height
    cw, ch = (sw, sw / aspect) if sw / sh < aspect else (sh * aspect, sh)
    box = ((sw - cw) / 2, (sh - ch) / 2, (sw + cw) / 2, (sh + ch) / 2)
    back = src.resize((max(1, width // 8), max(1, height // 8)), Image.BILINEAR, box=box)
    back = back.filter(ImageFilter.GaussianBlur(3)).resize((width, height), Image.BICUBIC)
    return Image.fromarray((np.asarray(back, dtype=np.float32) * 0.6).astype(np.uint8), "RGB")


def _fitted(path: str, width: int, height: int, zoom: float) -> Image.Image:
    """The whole picture, as large as fits, centred on a darkened blur of itself."""
    src = _aged(path)
    canvas = _backdrop(path, width, height).copy()
    sw, sh = src.size
    scale = min(width / sw, height / sh) * 0.94 * zoom
    w, h = max(1, int(sw * scale)), max(1, int(sh * scale))
    canvas.paste(src.resize((w, h), Image.BILINEAR), ((width - w) // 2, (height - h) // 2))
    return canvas


def photo_image(path: str | None, query: str, width: int, height: int, progress: float, age: float,
                seed: int, full: bool) -> Image.Image:
    """The photo `width` x `height` (its picture area), taped to the page or filling the frame.

    `age` (seconds on screen) drives the slow Ken Burns move inside the frame.
    """
    width, height = max(8, width), max(8, height)
    if path and Path(path).is_file():
        src = _aged(path)
        rng = np.random.default_rng(seed)
        zoom_in = rng.random() < 0.6
        k = min(1.0, age / 14.0)
        zoom = 1.0 + 0.10 * (k if zoom_in else 1 - k)
        aspect = width / height
        sw, sh = src.size
        if full and abs(math.log((sw / sh) / aspect)) > 0.3:
            # far from the frame's shape (a portrait in a 16:9 frame): the whole picture, over a
            # blurred copy of itself, rather than a crop that cuts off heads
            picture = _fitted(path, width, height, zoom)
        else:
            cw, ch = (sw, sw / aspect) if sw / sh < aspect else (sh * aspect, sh)       # cover the frame
            cw, ch = cw / zoom, ch / zoom
            dx, dy = rng.uniform(-1, 1), rng.uniform(-0.6, 0.2)
            cx = sw / 2 + dx * (sw - cw) / 2 * (k if zoom_in else 1 - k)
            # lean to the top third, where faces usually are
            cy = min(max(sh * 0.4 + dy * (sh - ch) / 2 * (k if zoom_in else 1 - k), ch / 2), sh - ch / 2)
            box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
            picture = src.resize((width, height), Image.BILINEAR, box=box)
    else:   # not found yet: a paper card with the search, so the plan still reads
        picture = Image.new("RGB", (width, height), "#e9e2d2")
        note = ink.render(ink.text_svg(ink.wrap(f"photo: {query}", max(16, height // 12), width * 0.8),
                                       max(16, height // 12), ink.INK, 1.0))
        picture.paste(note, ((width - note.width) // 2, (height - note.height) // 2), note)
    show = min(1.0, progress / 0.6)
    if full:
        out = picture.convert("RGBA")
        out.putalpha(int(255 * show))
        return out
    border = max(8, width // 28)
    card = Image.new("RGBA", (width + 2 * border, height + 2 * border + border), (251, 248, 241, 255))
    card.paste(picture, (border, border))
    ImageDraw.Draw(card).rectangle((0, 0, card.width - 1, card.height - 1), outline=(205, 196, 178, 255), width=2)
    angle = np.random.default_rng(seed + 7).uniform(-2.5, 2.5)
    pad = border * 2
    canvas = Image.new("RGBA", (card.width + 2 * pad, card.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", card.size, (40, 30, 20, 70))
    canvas.alpha_composite(shadow, (pad + 6, pad + 8))
    canvas = canvas.filter(ImageFilter.GaussianBlur(6))
    canvas.alpha_composite(card, (pad, pad))
    if progress > 0.6:                                                 # two strips of tape hold it
        tape_w, tape_h = max(40, width // 5), max(14, border * 2)
        for x, turn in ((pad + border, -38), (pad + card.width - border - tape_w, 38)):
            tape = Image.new("RGBA", (tape_w, tape_h), (238, 226, 196, int(170 * min(1.0, (progress - 0.6) / 0.3))))
            tape = tape.rotate(turn * 0.25, expand=True, resample=Image.BICUBIC)
            canvas.alpha_composite(tape, (int(x), int(pad - tape_h * 0.45)))
    canvas = canvas.rotate(angle, expand=False, resample=Image.BICUBIC)
    if show < 1:
        alpha = canvas.getchannel("A").point(lambda v: int(v * show))
        canvas.putalpha(alpha)
    return canvas


def outer(width: float, height: float, full: bool) -> tuple[float, float]:
    """The size a picture area of width x height takes on the page: a taped print adds its border and shadow."""
    if full:
        return width, height
    border = max(8, width // 28)
    pad = border * 2
    return width + 2 * border + 2 * pad, height + 3 * border + 2 * pad

