"""A theme's banner: a wide illustration shown on its card and at the top of its page.

The writing tier draws it as SVG (`llm` task `banner`, prompt `theme_banner`), in
the same rules as props and places: no text, scripts, styles, links or images.
It is rasterised with resvg to a JPEG in `<data>/studio/themes/theme-N/`. You
can also upload your own picture, which is cropped to the same shape. Whether
banners are drawn at all is `Settings.theme_banners`.
"""

import io
import logging

from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .. import config, llm, prompts
from ..db import session_scope
from ..db.models import Theme, utcnow
from ..jobs import Reporter
from . import art, cartoon

log = logging.getLogger("clipper.studio")

WIDTH, HEIGHT = 1600, 480            # the banner's shape, 10:3
MAX_SVG = 120_000
BACKGROUND = (246, 245, 241)
MAX_UPLOAD = 15 * 1024 * 1024


class Drawing(BaseModel):
    svg: str = Field(description="One complete SVG document")


def folder(theme_id: int):
    return config.studio_dir() / "themes" / f"theme-{theme_id}"


def path(theme_id: int):
    return folder(theme_id) / "banner.jpg"


def check(svg: str) -> str:
    svg = art.check_prop_svg(svg, limit=MAX_SVG)
    view = cartoon.re_view(svg)
    if view is None or abs(view[0] / view[1] - WIDTH / HEIGHT) > 0.12:
        raise ValueError(f'The viewBox must be "0 0 {WIDTH} {HEIGHT}"')
    return svg


def _save(theme_id: int, image: Image.Image, by: str, note: str = "") -> None:
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, BACKGROUND)
        flat.paste(image, mask=image.getchannel("A"))
        image = flat
    image = ImageOps.fit(image.convert("RGB"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    target = path(theme_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    image.save(temp, "JPEG", quality=88, optimize=True, progressive=True)
    temp.replace(target)
    with session_scope() as db:
        theme = db.get(Theme, theme_id)
        if theme is not None:
            theme.banner = {"made_at": utcnow().isoformat(), "by": by, "note": note[:300]}


def draw(theme_id: int, rep: Reporter, note: str = "") -> str:
    """Draw the banner of a theme; up to three tries when a drawing breaks the rules. Returns the job's message."""
    with session_scope() as db:
        theme = db.get(Theme, theme_id)
        if theme is None:
            raise RuntimeError("The theme was deleted")
        name, about = theme.name, theme.description
    prompt = f"<theme>{name}</theme>" + (f"\n<about>{about}</about>" if about else "")
    if note.strip():
        prompt += f"\n\nChange requested by the creator: {note.strip()}"
    rep.info(f"Drawing a banner for “{name}”")
    error = ""
    with llm.attribute(f"theme:{theme_id}"):
        for attempt in range(3):
            rep.progress(attempt / 3)
            ask = prompt + (f"\n\nYour last drawing was rejected: {error}. Fix that." if error else "")
            svg = llm.ask("banner", prompts.Prompt("theme_banner"), ask, Drawing, effort="medium").svg
            try:
                svg = check(svg)
                image = art.rasterize(svg, HEIGHT)
            except Exception as err:  # noqa: BLE001 - any rendering failure means: draw it again
                error = str(err)[:300]
                rep.warn(f"Drawing {attempt + 1} was not usable ({error}); drawing it again")
                continue
            _save(theme_id, image, "ai", note)
            log.info("theme banner drawn", extra={"theme_id": theme_id, "tries": attempt + 1})
            return "Banner drawn"
    raise RuntimeError(f"No usable banner after three tries: {error}")


def upload(theme_id: int, data: bytes) -> None:
    """Your own picture as the banner, cropped to the banner's shape."""
    if len(data) > MAX_UPLOAD:
        raise ValueError("The picture is too large: 15 MB at most")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as err:  # noqa: BLE001 - anything Pillow cannot read is not a picture
        raise ValueError("Could not read that file as a picture (use JPEG, PNG or WebP)") from err
    _save(theme_id, ImageOps.exif_transpose(image), "you")


def remove(theme_id: int) -> None:
    path(theme_id).unlink(missing_ok=True)
    with session_scope() as db:
        theme = db.get(Theme, theme_id)
        if theme is not None:
            theme.banner = None
