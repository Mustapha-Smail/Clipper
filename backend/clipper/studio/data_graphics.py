"""Fixed, source-backed data cards for Studio storyboards.

The story map owns the numbers and citations; renderers only choose a semantic
viewport.  There are deliberately no counters or coordinate fields here: every
value is fixed when a board is rendered and the layout is code-owned.
"""

import math
import re
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, model_validator

from ..settings import Settings, resolve_font

Kind = Literal["metric", "comparison", "bars", "gauge", "cohort", "clock", "scorecard"]
ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class Item(BaseModel):
    label: str = Field(min_length=1, max_length=36)
    value: float

    @model_validator(mode="after")
    def finite(self):
        if not math.isfinite(self.value):
            raise ValueError("value must be finite")
        return self


class Card(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    kind: Kind
    label: str = Field(min_length=1, max_length=60)
    value: float | None = None
    unit: str = Field(default="", max_length=16)
    items: list[Item] = Field(default_factory=list, max_length=6)
    max_value: float | None = None
    source_numbers: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def shape(self):
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("value must be finite")
        if self.max_value is not None and (not math.isfinite(self.max_value) or self.max_value <= 0):
            raise ValueError("max_value must be a positive finite number")
        if self.kind in {"metric", "gauge", "clock"} and self.value is None:
            raise ValueError(f"{self.kind} needs value")
        if self.kind in {"comparison", "bars", "cohort", "scorecard"} and not self.items:
            raise ValueError(f"{self.kind} needs items")
        if self.kind == "comparison" and len(self.items) != 2:
            raise ValueError("comparison needs exactly two items")
        if self.kind == "gauge" and self.max_value is None:
            raise ValueError("gauge needs max_value")
        return self


@dataclass(frozen=True)
class Resolved:
    cards: dict[str, dict]
    omissions: list[str]


def resolve(raw_cards: object, source_numbers: set[int]) -> Resolved:
    """Return cards safe to render plus human-readable reasons for every omission."""
    cards: dict[str, dict] = {}
    omissions: list[str] = []
    if not isinstance(raw_cards, list):
        return Resolved(cards, omissions)
    for index, raw in enumerate(raw_cards, 1):
        try:
            card = Card.model_validate(raw)
        except Exception as err:
            omissions.append(f"Data card {index} is invalid and was omitted: {err.errors()[0]['msg']}")
            continue
        if card.id in cards:
            omissions.append(f"Data card '{card.id}' is duplicated and was omitted.")
            continue
        cited = list(dict.fromkeys(card.source_numbers))
        unknown = [number for number in cited if number not in source_numbers]
        if not cited:
            omissions.append(f"Data card '{card.id}' has no source numbers and was omitted.")
            continue
        if unknown:
            omissions.append(
                f"Data card '{card.id}' cites unknown source number"
                f"{'s' if len(unknown) != 1 else ''} "
                + ", ".join(str(number) for number in unknown)
                + " and was omitted."
            )
            continue
        cards[card.id] = card.model_dump()
    return Resolved(cards, omissions)


def referenced(card_id: str, cards: dict[str, dict]) -> tuple[dict | None, str | None]:
    """Resolve a planned reference without ever falling back to arbitrary text."""
    if card_id in cards:
        return cards[card_id], None
    return None, f"Data card '{card_id}' is unknown or is missing valid citations and was omitted."


def stored(story_map: dict | None) -> dict[str, dict]:
    """Validated cards from an already-cleaned story map.

    Story-map saving is where citation numbers are checked against research.
    This second boundary keeps legacy or hand-edited database JSON from
    reaching a renderer, and still requires at least one retained citation.
    """
    out: dict[str, dict] = {}
    raw_cards = story_map.get("data_cards") if isinstance(story_map, dict) else None
    if not isinstance(raw_cards, list):
        return out
    for raw in raw_cards:
        try:
            card = Card.model_validate(raw)
        except Exception:
            continue
        if card.source_numbers and card.id not in out:
            out[card.id] = card.model_dump()
    return out


def prompt_block(cards: dict[str, dict]) -> str:
    """The only data-card IDs a storyboard planner may reference."""
    if not cards:
        return ""
    lines = [
        "<data_cards>",
        "Use a fixed cited graphic with `data ID` (and `lane a|b` in a split). Never invent an ID.",
    ]
    for card in cards.values():
        lines.append(
            f"- {card['id']}: {card['kind']} — {card['label']} (sources "
            + ", ".join(str(n) for n in card["source_numbers"])
            + ")"
        )
    return "\n".join([*lines, "</data_cards>"])


def _number(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def display(value: float, unit: str) -> str:
    value_text = _number(value)
    return f"{value_text}{unit}" if unit in {"%", "°", "x"} else f"{value_text} {unit}".strip()


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(10, size))


def _fit(draw: ImageDraw.ImageDraw, text: str, path: str, max_size: int, max_width: float) -> ImageFont.FreeTypeFont:
    for size in range(max_size, 9, -2):
        font = _font(path, size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return _font(path, 10)


def render(card: dict, width: int, height: int, *, progress: float = 1.0, font_path: str | None = None) -> Image.Image:
    """Draw one legible, static card. ``progress`` only reveals it deterministically."""
    path = font_path or resolve_font(Settings())
    progress = max(0.0, min(1.0, progress))
    image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(10, round(min(width, height) * 0.07))
    radius = max(12, round(min(width, height) * 0.075))
    draw.rounded_rectangle(
        (1, 1, width - 2, height - 2), radius=radius, fill="#10243a", outline="#e8f2ff", width=max(2, pad // 7)
    )
    label = str(card["label"]).upper()
    label_font = _fit(draw, label, path, max(14, round(height * 0.105)), width - 2 * pad)
    draw.text((pad, pad), label, font=label_font, fill="#dbeafe")
    kind, unit = card["kind"], str(card.get("unit") or "")
    value = card.get("value")
    items = list(card.get("items") or [])
    accent = "#43d9c2"
    muted = "#8da4bd"
    bottom, top = height - pad, pad + label_font.size * 1.55

    if kind == "metric":
        text = display(float(value), unit)
        font = _fit(draw, text, path, max(22, round(height * 0.36)), width - 2 * pad)
        draw.text((width / 2, (top + bottom) / 2), text, font=font, fill="#ffffff", anchor="mm")
    elif kind == "gauge":
        ratio = max(0.0, min(1.0, float(value) / float(card["max_value"])))
        cx, cy = width / 2, top + (bottom - top) * 0.61
        r = min(width * 0.34, (bottom - top) * 0.42)
        stroke = max(8, round(r * 0.16))
        draw.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill="#38516c", width=stroke)
        draw.arc((cx - r, cy - r, cx + r, cy + r), 180, 180 + 180 * ratio, fill=accent, width=stroke)
        text = display(float(value), unit)
        draw.text(
            (cx, cy - r * 0.05),
            text,
            font=_fit(draw, text, path, max(16, round(r * 0.43)), width - 2 * pad),
            fill="#fff",
            anchor="mm",
        )
    elif kind == "clock":
        cx, cy = width * 0.27, (top + bottom) / 2
        r = min(width * 0.18, (bottom - top) * 0.36)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent, width=max(4, round(r * 0.12)))
        angle = math.radians((float(value) % 12) * 30 - 90)
        draw.line(
            (cx, cy, cx + math.cos(angle) * r * 0.65, cy + math.sin(angle) * r * 0.65),
            fill="#fff",
            width=max(3, round(r * 0.1)),
        )
        text = display(float(value), unit)
        draw.text(
            (width * 0.64, cy),
            text,
            font=_fit(draw, text, path, max(18, round(height * 0.25)), width * 0.55),
            fill="#fff",
            anchor="mm",
        )
    elif kind == "comparison":
        max_value = max(abs(float(item["value"])) for item in items) or 1.0
        gap = pad
        column_w = (width - 2 * pad - gap) / 2
        chart_h = max(1.0, bottom - top - pad * 0.8)
        for index, item in enumerate(items):
            x0 = pad + index * (column_w + gap)
            ratio = abs(float(item["value"])) / max_value
            bar_top = bottom - chart_h * max(0.08, ratio)
            colour = accent if index else "#e25bb5"
            draw.rounded_rectangle((x0, bar_top, x0 + column_w, bottom), radius=max(5, pad // 3), fill=colour)
            text = display(float(item["value"]), unit)
            draw.text(
                (x0 + column_w / 2, bar_top - 5),
                text,
                font=_fit(draw, text, path, round(height * 0.13), column_w),
                fill="#fff",
                anchor="mb",
            )
            draw.text(
                (x0 + column_w / 2, bottom - pad * 1.05),
                item["label"],
                font=_fit(draw, item["label"], path, round(height * 0.075), column_w * 0.9),
                fill="#10243a",
                anchor="mm",
            )
    elif kind == "cohort":
        max_value = max((abs(float(item["value"])) for item in items), default=1.0) or 1.0
        row_h = max(18, (bottom - top) / max(1, len(items)))
        for index, item in enumerate(items):
            y = top + (index + 0.5) * row_h
            draw.text(
                (pad, y),
                item["label"],
                font=_fit(draw, item["label"], path, round(row_h * 0.36), width * 0.28),
                fill="#fff",
                anchor="lm",
            )
            x0, x1 = width * 0.34, width - pad
            gap = max(2, round((x1 - x0) * 0.012))
            cell_w = (x1 - x0 - 9 * gap) / 10
            filled = round(10 * abs(float(item["value"])) / max_value)
            for cell in range(10):
                left = x0 + cell * (cell_w + gap)
                draw.rounded_rectangle(
                    (left, y - row_h * 0.16, left + cell_w, y + row_h * 0.16),
                    radius=max(2, round(cell_w * 0.12)),
                    fill=accent if cell < filled else "#28435e",
                )
            text = display(float(item["value"]), unit)
            draw.text(
                (x1, y - row_h * 0.22),
                text,
                font=_fit(draw, text, path, round(row_h * 0.3), width * 0.25),
                fill=muted,
                anchor="rs",
            )
    elif kind == "scorecard":
        row_h = max(16, (bottom - top) / max(1, len(items)))
        for index, item in enumerate(items):
            y = top + (index + 0.5) * row_h
            draw.text(
                (pad, y),
                item["label"],
                font=_fit(draw, item["label"], path, round(row_h * 0.48), width * 0.58),
                fill="#fff",
                anchor="lm",
            )
            text = display(float(item["value"]), unit)
            draw.text(
                (width - pad, y),
                text,
                font=_fit(draw, text, path, round(row_h * 0.5), width * 0.32),
                fill=accent,
                anchor="rm",
            )
            if index + 1 < len(items):
                draw.line((pad, y + row_h * 0.45, width - pad, y + row_h * 0.45), fill="#2b435d", width=1)
    else:
        max_value = max((abs(float(item["value"])) for item in items), default=1.0) or 1.0
        row_h = max(18, (bottom - top) / max(1, len(items)))
        for index, item in enumerate(items):
            y = top + (index + 0.5) * row_h
            label_font = _fit(draw, item["label"], path, round(row_h * 0.38), width * 0.32)
            draw.text((pad, y), item["label"], font=label_font, fill="#fff", anchor="lm")
            x0, x1 = width * 0.38, width - pad
            amount = (x1 - x0) * abs(float(item["value"])) / max_value
            draw.rounded_rectangle((x0, y - row_h * 0.18, x1, y + row_h * 0.18), radius=4, fill="#28435e")
            draw.rounded_rectangle((x0, y - row_h * 0.18, x0 + amount, y + row_h * 0.18), radius=4, fill=accent)
            text = display(float(item["value"]), unit)
            draw.text(
                (x1, y - row_h * 0.25),
                text,
                font=_fit(draw, text, path, round(row_h * 0.33), width * 0.35),
                fill=muted,
                anchor="rs",
            )

    if progress < 1:
        # A left-to-right reveal is deliberate and deterministic; values themselves never animate.
        image.putalpha(Image.new("L", image.size, int(255 * progress)))
    return image
