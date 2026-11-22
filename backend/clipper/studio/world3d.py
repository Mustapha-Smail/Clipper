"""Validated beat language for the opt-in three-dimensional Studio style.

The model and the scene editor write only this line-oriented language.  It has
no coordinates, paths, URLs, code, materials or Blender expressions.  This
module is the trust boundary: it parses, validates referential integrity,
bounds complexity and emits one canonical spelling before a future renderer
may see a plan.

Object state deliberately survives ``shot`` lines.  Only ``remove`` and
``clear`` remove objects, which lets one miniature world accumulate changes as
the narration explains a mechanism.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

SLOTS = ("left", "center", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right")
SIZES = ("small", "medium", "large")
ROTATIONS = ("left", "right", "half", "reset")
CAMERAS = ("wide", "push", "pull", "orbit", "follow", "close", "cut")
PRESENTATION = ("show", "hide", "highlight", "dim")

# V1 is deliberately a small, code-owned pack.  Values are human descriptions
# for the planner; asset resolution and filesystem paths belong to the future
# trusted Blender bridge, never to the plan.
ASSETS = {
    "plank": "a simple timber plank",
    "nail": "a steel construction nail",
    "hammer": "a hand hammer",
    "pile": "a long foundation pile",
    "ram": "the moving impact weight in a pile hammer",
    "anvil": "the impact block above a pile",
    "cylinder": "a simple industrial cylinder",
    "guide_rods": "parallel guide rods",
    "crane": "a simplified crane proxy",
    "pile_hammer": "a simplified pile-hammer mechanism",
    "house": "a simplified house proxy",
    "wall": "a masonry wall segment",
    "fence": "a fence segment",
    "bridge": "a small bridge",
    "pump": "a simple pump",
    "pipe": "a pipe segment",
    "water_plane": "a flat water surface",
    "ground": "a ground platform",
    "arrow": "a code-owned three-dimensional arrow",
}

CAPABILITIES = {
    "pile_hammer": {"explode", "cutaway", "section", "cycle"},
    "cylinder": {"cutaway", "section", "cycle"},
    "pump": {"explode", "cutaway", "section", "cycle"},
    "house": {"cutaway", "section"},
    "wall": {"cutaway", "section"},
    "bridge": {"explode", "section"},
    "pipe": {"cutaway", "section"},
}

# An assembly declaration names the code-owned mechanism first, then existing
# component object IDs.  Required asset kinds prevent a model from claiming
# that arbitrary objects form a supported mechanism.
ASSEMBLIES = {
    "pile_hammer": {"pile", "ram", "anvil", "cylinder", "guide_rods"},
}

MAX_LIVE = 12
MAX_LABELS = 8
MAX_CYCLES = 3
MAX_CYCLE_REPETITIONS = 9
MAX_SIMULTANEOUS_MOTION = 3
MAX_CAMERA_MOVES = 24
MAX_SHOTS = 40
MAX_TEXT = 64
MAX_NOTE = 140
MAX_SCALE = 100.0

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TOKEN = re.compile(r'"[^"\n]*"|\S+')
MOTION = {"assemble", "explode", "move", "rotate", "scale", "lift", "drop", "strike", "cycle", "camera"}
CREATES = {"add", "duplicate", "assemble"}
VERBS = {
    "shot", "clear", "hold", "add", "remove", "duplicate", "assemble", "explode", "cutaway",
    "move", "rotate", "scale", "lift", "drop", "strike", "cycle", "label", "arrow", "measure",
    "section", "camera", *PRESENTATION,
}

# Renderer-facing format contracts contain semantic slots only.  There is no
# model-controlled coordinate in either aspect ratio.
FRAME_CONTRACTS = {
    "short": {"aspect": "9:16", "safe_slots": list(SLOTS), "caption_area": "bottom_third"},
    "long": {"aspect": "16:9", "safe_slots": list(SLOTS), "caption_area": "none"},
}


@dataclass
class Line:
    """One syntactically valid operation in canonical, renderer-neutral form."""

    word: int
    verb: str
    target: str = ""
    target2: str = ""
    asset: str = ""
    ident: str = ""
    slot: str = ""
    size: str = ""
    mode: str = ""
    text: str = ""
    count: int = 0
    scale: float | None = None
    parts: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [str(self.word), self.verb]
        if self.verb == "shot":
            parts.append(_quote(self.text))
        elif self.verb in {"clear", "hold"}:
            pass
        elif self.verb == "add":
            parts += [self.asset, self.slot, self.size, "as", self.ident]
        elif self.verb == "duplicate":
            parts += [self.target, self.slot, "as", self.ident]
        elif self.verb == "assemble":
            parts += [self.asset, *self.parts, "as", self.ident]
        elif self.verb == "move":
            parts += [self.target, self.slot]
        elif self.verb == "rotate":
            parts += [self.target, self.mode]
        elif self.verb == "scale":
            value = self.size or _number(self.scale or 1)
            parts += [self.target, value]
        elif self.verb in {"drop", "strike"}:
            parts.append(self.target)
            if self.target2:
                parts.append(self.target2)
        elif self.verb == "cycle":
            parts += [self.target, str(self.count)]
        elif self.verb == "label":
            parts += [self.target, _quote(self.text)]
        elif self.verb in {"arrow", "measure"}:
            parts += [self.target, self.target2]
            if self.text:
                parts.append(_quote(self.text))
        elif self.verb == "camera":
            parts.append(self.mode)
            if self.target:
                parts.append(self.target)
        else:
            if self.target:
                parts.append(self.target)
        return " ".join(parts)


@dataclass
class _Object:
    asset: str
    visible: bool = True


def _quote(value: str) -> str:
    return '"' + value.replace('"', "'") + '"'


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


def _text(token: str, limit: int) -> tuple[str, str]:
    if not (token.startswith('"') and token.endswith('"')):
        return "", "text must be in double quotes"
    value = " ".join(token[1:-1].split())
    if not value:
        return "", "text is empty"
    if len(value) > limit:
        return "", f"text is longer than {limit} characters"
    low = value.lower()
    unsafe = (
        any(ord(ch) < 32 for ch in value)
        or any(mark in value for mark in ("<", ">", "{", "}", "`", "\\"))
        or re.search(r"(?:https?://|www\.|file:|/etc/|/tmp/|\.\./|\b(?:bpy|import|exec|eval|__\w+__)\b)", low)
    )
    return ("", "text contains code, a path, a URL or unsafe markup") if unsafe else (value, "")


def _named(value: str, what: str = "object ID") -> str:
    return "" if NAME.fullmatch(value) else f"{what} '{value}' is not safe snake_case"


def _split(raw: str) -> tuple[list[str], str]:
    value = raw.strip().strip("`").strip().replace("“", '"').replace("”", '"').replace("’", "'")
    value = re.sub(r"^\[(\d+)]", r"\1", value)
    if value.count('"') % 2:
        return [], "unclosed double quote"
    tokens = TOKEN.findall(value)
    if len(tokens) < 2 or not tokens[0].isdigit():
        return [], "a line must start with a narration word number and a verb"
    return tokens, ""


def parse(raw: str) -> tuple[Line | None, str]:
    """Parse one line and return either a line or a precise syntax error."""
    tokens, error = _split(raw)
    if error:
        return None, error
    word, verb, args = int(tokens[0]), tokens[1].lower(), tokens[2:]
    if verb not in VERBS:
        return None, f"unknown verb '{verb}'"
    line = Line(word=word, verb=verb)

    def exact(count: int, usage: str) -> str:
        return "" if len(args) == count else f"use: N {usage}"

    if verb == "shot":
        if err := exact(1, 'shot "what the viewer sees"'):
            return None, err
        line.text, error = _text(args[0], MAX_NOTE)
        return (None, error) if error else (line, "")
    if verb in {"clear", "hold"}:
        return ((line, "") if not args else (None, f"{verb} takes no arguments"))
    if verb == "add":
        if len(args) not in (4, 5) or args[-2].lower() != "as":
            return None, "use: N add ASSET SLOT [small|medium|large] as ID"
        line.asset, line.slot, line.ident = args[0].lower(), args[1].lower(), args[-1].lower()
        line.size = args[2].lower() if len(args) == 5 else "medium"
        if line.asset not in ASSETS:
            return None, f"unknown asset '{line.asset}'"
        if line.slot not in SLOTS:
            return None, f"unknown semantic slot '{line.slot}'"
        if line.size not in SIZES:
            return None, f"unknown size '{line.size}'"
        error = _named(line.ident)
        return (None, error) if error else (line, "")
    if verb == "duplicate":
        if len(args) != 4 or args[2].lower() != "as":
            return None, "use: N duplicate TARGET SLOT as ID"
        line.target, line.slot, line.ident = (x.lower() for x in (args[0], args[1], args[3]))
        error = _named(line.target, "target") or _named(line.ident)
        if line.slot not in SLOTS:
            error = error or f"unknown semantic slot '{line.slot}'"
        return (None, error) if error else (line, "")
    if verb == "assemble":
        if len(args) < 5 or args[-2].lower() != "as":
            return None, "use: N assemble MECHANISM PART PART [...] as ID"
        line.asset = args[0].lower()
        line.parts = [x.lower() for x in args[1:-2]]
        line.ident = args[-1].lower()
        if line.asset not in ASSEMBLIES:
            return None, f"unknown code-owned mechanism '{line.asset}'"
        error = _named(line.ident) or next((_named(x, "part") for x in line.parts if _named(x, "part")), "")
        return (None, error) if error else (line, "")
    if verb in {"remove", "explode", "cutaway", "lift", "section", *PRESENTATION}:
        if err := exact(1, f"{verb} TARGET"):
            return None, err
        line.target = args[0].lower()
        error = _named(line.target, "target")
        return (None, error) if error else (line, "")
    if verb == "move":
        if err := exact(2, "move TARGET SLOT"):
            return None, err
        line.target, line.slot = args[0].lower(), args[1].lower()
        error = _named(line.target, "target")
        if line.slot not in SLOTS:
            error = error or f"unknown semantic slot '{line.slot}'"
        return (None, error) if error else (line, "")
    if verb == "rotate":
        if err := exact(2, "rotate TARGET left|right|half|reset"):
            return None, err
        line.target, line.mode = args[0].lower(), args[1].lower()
        error = _named(line.target, "target")
        if line.mode not in ROTATIONS:
            error = error or f"unknown semantic rotation '{line.mode}'"
        return (None, error) if error else (line, "")
    if verb == "scale":
        if err := exact(2, "scale TARGET small|medium|large|FACTOR"):
            return None, err
        line.target, value = args[0].lower(), args[1].lower()
        error = _named(line.target, "target")
        if value in SIZES:
            line.size = value
        elif re.fullmatch(r"\d+(?:\.\d+)?", value):
            line.scale = float(value)
            if not math.isfinite(line.scale):
                error = "scale factor is not finite"
        else:
            error = error or f"scale '{value}' is not a semantic size or numeric factor"
        return (None, error) if error else (line, "")
    if verb in {"drop", "strike"}:
        allowed = (1, 2) if verb == "drop" else (2,)
        if len(args) not in allowed:
            return None, f"use: N {verb} TARGET" + (" [ONTO]" if verb == "drop" else " OTHER_TARGET")
        line.target = args[0].lower()
        line.target2 = args[1].lower() if len(args) == 2 else ""
        error = _named(line.target, "target") or (_named(line.target2, "target") if line.target2 else "")
        return (None, error) if error else (line, "")
    if verb == "cycle":
        if err := exact(2, "cycle TARGET COUNT"):
            return None, err
        line.target = args[0].lower()
        if not args[1].isdigit():
            return None, "cycle count must be a whole number"
        line.count = int(args[1])
        error = _named(line.target, "target")
        return (None, error) if error else (line, "")
    if verb == "label":
        if err := exact(2, 'label TARGET "TEXT"'):
            return None, err
        line.target = args[0].lower()
        line.text, error = _text(args[1], MAX_TEXT)
        error = _named(line.target, "target") or error
        return (None, error) if error else (line, "")
    if verb in {"arrow", "measure"}:
        minimum = 2 if verb == "arrow" else 3
        if len(args) not in ({2, 3} if verb == "arrow" else {3}):
            return None, f'use: N {verb} FROM TO' + (' ["TEXT"]' if verb == "arrow" else ' "TEXT"')
        line.target, line.target2 = args[0].lower(), args[1].lower()
        error = _named(line.target, "target") or _named(line.target2, "target")
        if len(args) >= minimum and len(args) == 3:
            line.text, text_error = _text(args[2], MAX_TEXT)
            error = error or text_error
        return (None, error) if error else (line, "")
    if verb == "camera":
        if len(args) not in (1, 2):
            return None, "use: N camera wide|push|pull|orbit|follow|close|cut [TARGET]"
        line.mode = args[0].lower()
        line.target = args[1].lower() if len(args) == 2 else ""
        if line.mode not in CAMERAS:
            return None, f"unknown camera move '{line.mode}'"
        error = _named(line.target, "target") if line.target else ""
        return (None, error) if error else (line, "")
    return None, f"unknown verb '{verb}'"  # defensive: VERBS and the parser must stay in step


def parse_line(raw: str) -> Line | None:
    """One syntactically valid line, or ``None`` when it is not understood."""
    return parse(raw)[0]


def _reason(line_number: int, raw: object, why: str) -> str:
    value = str(raw).strip()
    return f"line {line_number}: {value}  ({why})"


def _references(line: Line) -> list[str]:
    if line.verb == "assemble":
        return list(line.parts)
    if line.verb in {"arrow", "measure", "strike"}:
        return [line.target, line.target2]
    if line.verb == "drop" and line.target2:
        return [line.target, line.target2]
    if line.target:
        return [line.target]
    return []


def clean(shots: list[dict], word_count: int) -> tuple[list[dict], list[str]]:
    """Return a canonical safe plan and exact reasons for every dropped or bounded line.

    The same function handles model output and user edits.  API callers reject
    any reasons; the planner may retain bounded lines while reporting them.
    """
    last_word = max(0, word_count - 1)
    issues: list[str] = []
    prepared: list[tuple[int, int, dict]] = []
    for index, raw in enumerate(shots or [], 1):
        if len(prepared) >= MAX_SHOTS:
            issues.append(f"shot {index}: dropped because a plan may have at most {MAX_SHOTS} shots")
            continue
        try:
            original = int(raw.get("start_word", 0))
        except (TypeError, ValueError, AttributeError):
            issues.append(f"shot {index}: invalid start word; bounded to 0")
            original = 0
        start = max(0, min(original, last_word))
        if start != original:
            issues.append(f"shot {index}: word {original} bounded to {start}")
        note = str(raw.get("note", "")) if isinstance(raw, dict) else ""
        _, note_error = _text(_quote(note), MAX_NOTE) if note else ("", "")
        if note_error:
            issues.append(f"shot {index}: {note_error}; note removed")
            note = ""
        prepared.append((start, index, {"start_word": start, "note": note, "lines": list(raw.get("lines") or [])}))
    prepared.sort(key=lambda item: (item[0], item[1]))
    ordered: list[dict] = []
    for start, index, shot in prepared:
        if ordered and start <= ordered[-1]["start_word"]:
            issues.append(f"shot {index}: start word {start} duplicates the previous shot; its lines were merged")
            ordered[-1]["lines"].extend(shot["lines"])
            continue
        ordered.append(shot)
    if ordered and ordered[0]["start_word"] != 0:
        issues.append(f"shot 1: first start word {ordered[0]['start_word']} bounded to 0")
        ordered[0]["start_word"] = 0

    live: dict[str, _Object] = {}
    seen: set[str] = set()
    labels = camera_moves = cycle_repetitions = 0
    motions: dict[int, int] = {}
    line_number = 0
    for shot_index, shot in enumerate(ordered):
        end = ordered[shot_index + 1]["start_word"] - 1 if shot_index + 1 < len(ordered) else last_word
        parsed: list[tuple[int, Line, object]] = []
        previous_word = shot["start_word"]
        for raw in shot["lines"]:
            line_number += 1
            line, error = parse(str(raw))
            if line is None:
                issues.append(_reason(line_number, raw, error))
                continue
            if line.verb == "shot":
                issues.append(_reason(line_number, raw, "shot lines belong at the top level, not inside a shot"))
                continue
            original = line.word
            line.word = max(shot["start_word"], min(line.word, end))
            if line.word != original:
                issues.append(_reason(line_number, raw, f"word {original} bounded to this shot's word {line.word}"))
            if line.word < previous_word:
                issues.append(_reason(line_number, raw, "out-of-order word number was canonicalized"))
            previous_word = max(previous_word, line.word)
            parsed.append((line_number, line, raw))
        parsed.sort(key=lambda item: item[1].word)
        kept: list[Line] = []
        for number, line, raw in parsed:
            refs = _references(line)
            missing = next((ref for ref in refs if ref not in live), "")
            if missing:
                issues.append(_reason(number, raw, f"target '{missing}' is used before it is created or after removal"))
                continue
            if line.verb in {"explode", "cutaway", "section", "cycle"}:
                asset = live[line.target].asset
                capability = "section" if line.verb == "section" else line.verb
                if capability not in CAPABILITIES.get(asset, set()):
                    issues.append(_reason(number, raw, f"asset '{asset}' does not support {line.verb}"))
                    continue
            if line.verb == "assemble":
                kinds = {live[ref].asset for ref in line.parts}
                missing_kinds = ASSEMBLIES[line.asset] - kinds
                if missing_kinds:
                    issues.append(
                        _reason(number, raw, "mechanism is missing components: " + ", ".join(sorted(missing_kinds)))
                    )
                    continue
                if len(set(line.parts)) != len(line.parts):
                    issues.append(_reason(number, raw, "an assembly cannot repeat a component ID"))
                    continue
            if line.verb == "camera":
                if line.mode in {"push", "orbit", "follow", "close"} and not line.target:
                    issues.append(_reason(number, raw, f"camera {line.mode} needs a visible target"))
                    continue
                if line.target and not live[line.target].visible:
                    issues.append(_reason(number, raw, f"camera target '{line.target}' is hidden"))
                    continue
                if camera_moves >= MAX_CAMERA_MOVES:
                    issues.append(_reason(number, raw, f"more than {MAX_CAMERA_MOVES} camera moves in one plan"))
                    continue
            if line.verb in {"highlight", "dim"} and not live[line.target].visible:
                issues.append(_reason(number, raw, f"target '{line.target}' is hidden"))
                continue
            if line.verb in MOTION:
                count = motions.get(line.word, 0)
                if count >= MAX_SIMULTANEOUS_MOTION:
                    issues.append(_reason(number, raw, f"more than {MAX_SIMULTANEOUS_MOTION} motions start together"))
                    continue
                motions[line.word] = count + 1
            if line.verb in CREATES:
                if line.ident in seen:
                    issues.append(_reason(number, raw, f"object ID '{line.ident}' already exists in this plan"))
                    continue
                if len(live) >= MAX_LIVE:
                    issues.append(_reason(number, raw, f"more than {MAX_LIVE} objects would be live"))
                    continue
                if line.verb == "assemble":
                    asset = line.asset
                else:
                    asset = line.asset if line.verb == "add" else live[line.target].asset
                live[line.ident] = _Object(asset=asset)
                seen.add(line.ident)
            elif line.verb == "remove":
                live.pop(line.target)
            elif line.verb == "clear":
                live.clear()
            if line.verb == "cycle":
                bounded = max(1, min(line.count, MAX_CYCLES))
                if bounded != line.count:
                    issues.append(_reason(number, raw, f"cycle count {line.count} bounded to {bounded}"))
                    line.count = bounded
                remaining = MAX_CYCLE_REPETITIONS - cycle_repetitions
                if remaining <= 0:
                    issues.append(
                        _reason(number, raw, f"more than {MAX_CYCLE_REPETITIONS} cycle repetitions in one plan")
                    )
                    continue
                if line.count > remaining:
                    issues.append(
                        _reason(number, raw, f"cycle count {line.count} bounded to remaining budget {remaining}")
                    )
                    line.count = remaining
                cycle_repetitions += line.count
            if line.verb == "scale" and line.scale is not None:
                bounded_scale = max(1.0, min(line.scale, MAX_SCALE))
                if bounded_scale != line.scale:
                    issues.append(
                        _reason(
                            number,
                            raw,
                            f"scale factor {_number(line.scale)} bounded to {_number(bounded_scale)}",
                        )
                    )
                    line.scale = bounded_scale
            if line.verb == "label":
                if labels >= MAX_LABELS:
                    issues.append(_reason(number, raw, f"more than {MAX_LABELS} labels in one plan"))
                    continue
                labels += 1
            if line.verb == "camera":
                camera_moves += 1
            if line.verb == "show":
                live[line.target].visible = True
            elif line.verb == "hide":
                live[line.target].visible = False
            kept.append(line)
        shot["lines"] = [line.render() for line in kept]
    return ordered, issues


def parse_plan(text: str, word_count: int, offset: int = 0, check: bool = True) -> tuple[list[dict], list[str]]:
    """Parse planner text into shots, shifting chapter-local word numbers by ``offset``."""
    shots: list[dict] = []
    issues: list[str] = []
    source_line = 0
    for raw in text.splitlines():
        source_line += 1
        value = raw.strip().strip("`").strip()
        if not value or value.startswith("#"):
            continue
        line, error = parse(value)
        if line is None:
            issues.append(_reason(source_line, value, error))
            continue
        line.word += offset
        if line.verb == "shot":
            shots.append({"start_word": line.word, "note": line.text, "lines": []})
        elif not shots:
            issues.append(_reason(source_line, value, "an operation needs a shot line before it"))
        else:
            shots[-1]["lines"].append(line.render())
    if check:
        shots, clean_issues = clean(shots, word_count)
        issues.extend(clean_issues)
    return shots, issues


def parsed(shot: dict) -> list[Line]:
    return [line for line in (parse_line(str(raw)) for raw in shot.get("lines", [])) if line is not None]


def to_text(shots: list[dict]) -> str:
    """Canonical serialization for an editor, planner continuation or renderer input."""
    out: list[str] = []
    for shot in shots:
        out.append(f'{int(shot.get("start_word", 0))} shot {_quote(str(shot.get("note", "")))}')
        out.extend(str(line) for line in shot.get("lines", []))
    return "\n".join(out)


def complexity(shots: list[dict]) -> dict:
    """Arithmetic complexity facts; this is workload, never an aesthetic score."""
    objects = labels = camera_moves = cycles = cycle_repetitions = motions = live = peak_live = 0
    for shot in shots:
        for line in parsed(shot):
            if line.verb in CREATES:
                objects += 1
                live += 1
                peak_live = max(peak_live, live)
            elif line.verb == "remove":
                live = max(0, live - 1)
            elif line.verb == "clear":
                live = 0
            labels += int(line.verb == "label")
            camera_moves += int(line.verb == "camera")
            cycles += int(line.verb == "cycle")
            cycle_repetitions += line.count if line.verb == "cycle" else 0
            motions += int(line.verb in MOTION)
    lines = [
        {"label": "Objects", "count": objects, "weight": 2, "units": objects * 2},
        {"label": "Labels", "count": labels, "weight": 2, "units": labels * 2},
        {"label": "Camera moves", "count": camera_moves, "weight": 1, "units": camera_moves},
        {"label": "Motion actions", "count": motions, "weight": 1, "units": motions},
        {"label": "Cycle repetitions", "count": cycle_repetitions, "weight": 2, "units": cycle_repetitions * 2},
    ]
    units = sum(line["units"] for line in lines)
    band = "simple" if units <= 30 else "moderate" if units <= 60 else "dense"
    return {
        "units": units,
        "band": band,
        "lines": lines,
        "shots": len(shots),
        "objects": objects,
        "max_live_objects": peak_live,
        "labels": labels,
        "camera_moves": camera_moves,
        "cycles": cycles,
        "cycle_repetitions": cycle_repetitions,
        "motions": motions,
    }


def score(shots: list[dict]) -> dict:
    """Compatibility name for callers seeking the arithmetic plan report."""
    return complexity(shots)
