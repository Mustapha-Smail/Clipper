"""Renderer-independent semantic lane geometry.

Plans describe *what* kind of composition a scene has.  This module owns the
small amount of geometry every renderer needs, so ink, flat and film agree on
where lane content and captions may go.  A missing composition is deliberately
the legacy single frame.
"""

from dataclasses import dataclass
from typing import Literal

Format = Literal["short", "long"]
Kind = Literal["single", "split"]
Divider = Literal["none", "line", "vs"]

SIZES = {"short": (1080, 1920), "long": (1920, 1080)}
# These are the established flat-stage fractions.  Single geometry returns the
# same values; split geometry transforms them into a lane viewport.
SLOTS = {
    "short": {"top_left": (0.26, 0.16), "top": (0.5, 0.16), "top_right": (0.74, 0.16),
              "left": (0.26, 0.39), "center": (0.5, 0.39), "right": (0.74, 0.39),
              "bottom_left": (0.26, 0.58), "bottom": (0.5, 0.58), "bottom_right": (0.74, 0.58)},
    "long": {"top_left": (0.2, 0.22), "top": (0.5, 0.21), "top_right": (0.8, 0.22),
             "left": (0.22, 0.56), "center": (0.5, 0.56), "right": (0.78, 0.56),
             "bottom_left": (0.22, 0.85), "bottom": (0.5, 0.85), "bottom_right": (0.78, 0.85)},
}


@dataclass(frozen=True)
class Viewport:
    x0: float
    y0: float
    x1: float
    y1: float
    lane: str = ""

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class Geometry:
    format: Format
    kind: Kind
    divider: Divider
    width: int
    height: int
    caption_top: float
    lanes: tuple[Viewport, ...]

    @property
    def single(self) -> bool:
        return self.kind == "single"

    def viewport(self, lane: str = "") -> Viewport:
        if self.single or not lane:
            return self.lanes[0]
        return next((view for view in self.lanes if view.lane == lane), self.lanes[0])


def normalize(value: dict | None) -> dict:
    """Validate a scene contract and return stable JSON-friendly values.

    ``lanes`` is accepted as an old spelling for ``split`` so hand-edited
    plans remain readable; all new output uses ``split``.
    """
    value = value or {}
    kind = str(value.get("kind") or "single").lower()
    if kind == "lanes":
        kind = "split"
    if kind not in {"single", "split"}:
        kind = "single"
    divider = str(value.get("divider") or "none").lower()
    if divider not in {"none", "line", "vs"}:
        divider = "none"
    lanes = [str(x).lower() for x in (value.get("lanes") or []) if str(x).lower() in {"a", "b"}]
    if kind == "split":
        lanes = ["a", "b"]
    else:
        lanes, divider = [], "none"
    return {"kind": kind, "lanes": lanes, "divider": divider}


def geometry(format: Format, value: dict | None = None, captions: bool = False) -> Geometry:
    """Return deterministic frame and lane bounds in output pixels.

    Caption-safe space is the lower 33.5% of a Short, matching the established
    short layout. Long videos reserve the same proportional band only when
    captions are explicitly enabled; default long composition is unchanged.
    """
    width, height = SIZES[format]
    spec = normalize(value)
    caption_top = height * (0.665 if format == "short" else (0.82 if captions else 1.0))
    if spec["kind"] == "single":
        return Geometry(format, "single", "none", width, height, caption_top,
                        (Viewport(0, 0, width, caption_top, ""),))
    gap = max(8.0, round(min(width, height) * 0.012))
    if format == "long":
        half = width / 2
        views = (Viewport(0, 0, half - gap / 2, caption_top, "a"),
                 Viewport(half + gap / 2, 0, width, caption_top, "b"))
    else:
        half = caption_top / 2
        views = (Viewport(0, 0, width, half - gap / 2, "a"),
                 Viewport(0, half + gap / 2, width, caption_top, "b"))
    return Geometry(format, "split", spec["divider"], width, height, caption_top, views)


def slot(format: Format, name: str, view: Viewport) -> tuple[float, float]:
    """Transform a legacy local slot into a viewport's absolute pixel centre."""
    fx, fy = SLOTS[format].get(name or "center", SLOTS[format]["center"])
    return view.x0 + fx * view.width, view.y0 + fy * view.height


def contains(view: Viewport, box: tuple[float, float, float, float], margin: float = 0) -> bool:
    """Whether a rendered box stays inside its lane viewport."""
    x0, y0, x1, y1 = box
    return x0 >= view.x0 + margin and y0 >= view.y0 + margin and x1 <= view.x1 - margin and y1 <= view.y1 - margin
