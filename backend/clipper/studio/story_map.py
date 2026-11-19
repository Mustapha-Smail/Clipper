"""Source-backed story maps: a deterministic contract between research and prose.

The model proposes only the structural fields. Code owns source filtering, loop
integrity, input fingerprints, authorship and timestamps. A map is an input to
writing, not an approval or a score.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .. import llm, prompts
from ..db import session_scope
from ..db.models import Script, utcnow
from ..jobs import Reporter
from . import compliance, plain

Template = Literal["mechanism", "story_loop", "journey", "ranking"]
Purpose = Literal["question", "evidence", "answer", "twist", "payoff", "callback"]
ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class Opening(BaseModel):
    context: str
    lean: str
    turn: str = ""
    promise: str
    first_visual: str
    source_numbers: list[int] = Field(default_factory=list)


class Beat(BaseModel):
    id: str = Field(pattern=ID_PATTERN)
    purpose: Purpose
    summary: str
    source_numbers: list[int] = Field(default_factory=list)
    opens: list[str] = Field(default_factory=list)
    resolves: list[str] = Field(default_factory=list)
    visual_event: str


class Ending(BaseModel):
    payoff: str
    callback: str = ""
    source_numbers: list[int] = Field(default_factory=list)


class Draft(BaseModel):
    template: Template
    opening: Opening
    beats: list[Beat]
    ending: Ending


@dataclass
class Cleaned:
    value: dict
    problems: list[str]
    warnings: list[str]


PROMPT = prompts.Prompt("script_story_map")


def _json_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def input_material(script: Script) -> dict:
    """Every upstream value that can change what this map should say."""
    idea = script.idea
    research = script.research or {}
    notes = research.get("notes", "")
    cited = {int(number) for number in re.findall(r"\[(\d+)]", notes)}
    return {
        "title": script.title,
        "format": script.format,
        "target": {"video": script.video, "minutes": script.target_minutes, "seconds": script.target_seconds},
        "audience": plain.audience(),
        "idea": None if idea is None else {
            "hook": idea.hook,
            "expected": idea.expected,
            "answer": idea.answer,
            "angle": idea.angle,
            "beats": idea.beats,
        },
        "research_notes": notes,
        # Fact checking may append verification-only sources later. Only the sources cited in the research notes
        # informed the map and are legal inputs to it, so those are the ones that affect freshness.
        "research_sources": [source for source in research.get("sources", []) if source.get("n") in cited],
    }


def input_fingerprint(script: Script) -> tuple[str, dict[str, str]]:
    material = input_material(script)
    return _json_hash(material), {key: _json_hash(value) for key, value in material.items()}


STALE_LABELS = {
    "title": "The script title changed.",
    "format": "The script format changed.",
    "target": "The Short/long target or target length changed.",
    "audience": "The audience setting changed.",
    "idea": "The idea hook, expected guess, answer, angle or beats changed.",
    "research_notes": "The research notes changed.",
    "research_sources": "The research source list changed.",
}


def status(script: Script, value: dict | None = None) -> tuple[bool, list[str]]:
    value = value if value is not None else script.story_map
    if not value:
        return False, ["No story map has been made yet."]
    current_hash, current_parts = input_fingerprint(script)
    if value.get("input_hash") == current_hash:
        return True, []
    saved_parts = value.get("input_parts")
    if not isinstance(saved_parts, dict):
        return False, ["The title, format, audience, idea or research changed."]
    reasons = [STALE_LABELS[key] for key in STALE_LABELS if saved_parts.get(key) != current_parts[key]]
    return False, reasons or ["The story-map inputs changed."]


def _safe_loop(value: str) -> bool:
    return bool(re.fullmatch(ID_PATTERN, value or ""))


def clean(raw: dict, source_numbers: set[int]) -> Cleaned:
    """Parse a proposed/editable map, drop unknown citations, and list structural problems."""
    draft = Draft.model_validate(raw)
    value = draft.model_dump()
    warnings: list[str] = []

    def sources_at(where: str, numbers: list[int]) -> list[int]:
        kept, dropped = [], []
        for number in dict.fromkeys(numbers):
            (kept if number in source_numbers else dropped).append(number)
        if dropped:
            warnings.append(f"{where}: dropped unknown source number{'s' if len(dropped) != 1 else ''} "
                            + ", ".join(str(n) for n in dropped))
        return kept

    value["opening"]["source_numbers"] = sources_at("Opening", value["opening"]["source_numbers"])
    for index, beat in enumerate(value["beats"], 1):
        beat["source_numbers"] = sources_at(f"Beat {index} ({beat['id']})", beat["source_numbers"])
    value["ending"]["source_numbers"] = sources_at("Ending", value["ending"]["source_numbers"])

    problems: list[str] = []
    beat_ids: set[str] = set()
    opened: set[str] = set()
    active: set[str] = set()
    for index, beat in enumerate(value["beats"], 1):
        if beat["id"] in beat_ids:
            problems.append(f"Beat {index} repeats beat id '{beat['id']}'.")
        beat_ids.add(beat["id"])
        for loop_id in beat["resolves"]:
            if not _safe_loop(loop_id):
                problems.append(f"Beat {index} has unsafe loop id '{loop_id}'.")
            elif loop_id not in active:
                problems.append(f"Beat {index} resolves loop '{loop_id}' before it is open.")
            else:
                active.remove(loop_id)
        for loop_id in beat["opens"]:
            if not _safe_loop(loop_id):
                problems.append(f"Beat {index} has unsafe loop id '{loop_id}'.")
            elif loop_id in opened:
                problems.append(f"Beat {index} repeats loop id '{loop_id}'.")
            else:
                opened.add(loop_id)
                active.add(loop_id)
        if len(active) > 2:
            problems.append(f"Beat {index} leaves {len(active)} loops open; at most two are allowed.")
    if active:
        problems.append("Unresolved loops: " + ", ".join(sorted(active)) + ".")
    if not value["opening"]["promise"].strip():
        problems.append("The opening promise is empty.")
    if not value["ending"]["payoff"].strip():
        problems.append("The ending does not pay off the opening promise.")
    return Cleaned(value=value, problems=problems, warnings=warnings)


def source_numbers(script: Script) -> set[int]:
    research = script.research or {}
    cited = {int(number) for number in re.findall(r"\[(\d+)]", research.get("notes", ""))}
    available = {int(source["n"]) for source in research.get("sources", []) if "n" in source}
    return cited & available


def problems(script: Script, value: dict | None = None) -> list[str]:
    value = value if value is not None else script.story_map
    if not value:
        return []
    try:
        return clean(value, source_numbers(script)).problems
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return [f"Invalid story map at {where}: {first['msg']}"]


def out(script: Script) -> dict | None:
    if not script.story_map:
        return None
    current, stale_reasons = status(script)
    return {**script.story_map, "current": current, "stale_reasons": stale_reasons,
            "problems": problems(script)}


def prompt_value(script: Script, *, required: bool = False) -> str:
    """Return only the map's structural contract for an AI prompt.

    Metadata is deliberately omitted: the writer needs promises, order, loops and
    citations, not fingerprints. A map that exists is never silently ignored when
    it is stale or malformed.
    """
    if not script.story_map:
        if required:
            raise RuntimeError("Build the story map before writing the script")
        return ""
    current, reasons = status(script)
    if not current:
        raise RuntimeError("The story map is out of date: " + " ".join(reasons))
    invalid = problems(script)
    if invalid:
        raise RuntimeError("The story map has validation problems: " + " ".join(invalid))
    value = {key: script.story_map[key] for key in ("template", "opening", "beats", "ending")}
    return json.dumps(value, ensure_ascii=False, indent=2)


def _prompt(script: Script) -> str:
    from . import script as script_mod

    research = script.research or {}
    sources = "\n".join(f"[{s['n']}] {s.get('title') or s['url']} — {s['url']}" for s in research.get("sources", []))
    return (f"<video>\n{script_mod._brief(script)}\n</video>\n\n"
            f"<research_notes>\n{research.get('notes', '')}\n</research_notes>\n\n"
            f"<sources>\n{sources or '(none)'}\n</sources>")


def build(script: Script, rep: Reporter) -> dict:
    """Ask for and validate one map. Does not write the database."""
    try:
        draft = llm.ask("story_map", PROMPT.format(plain=plain.rules()), _prompt(script), Draft, effort="high")
    except llm.AiUnavailable as err:
        rep.warn(f"Story map unavailable: {err}")
        raise RuntimeError(f"Could not build the story map: {err}") from err
    cleaned = clean(draft.model_dump(), source_numbers(script))
    for warning in cleaned.warnings:
        rep.warn(warning)
    for problem in cleaned.problems:
        rep.warn(problem)
    if cleaned.problems:
        raise RuntimeError("The proposed story map was not structurally valid: " + " ".join(cleaned.problems))
    input_hash, input_parts = input_fingerprint(script)
    return {"version": 1, "input_hash": input_hash, "input_parts": input_parts,
            "made_at": utcnow().isoformat(), "by": "ai", **cleaned.value}


def run(script_id: int, rep: Reporter) -> None:
    """Worker stage: build the map from existing research and store it only after validation."""
    from .script import _load

    script = _load(script_id)
    if not script.research:
        raise RuntimeError("Research the script before building its story map")
    rep.info("Building a source-backed story map from the research")
    value = build(script, rep)
    with session_scope() as db:
        row = db.get(Script, script_id)
        row.story_map = value
        compliance.record(db, script_id, "story_map", "ai", "Built a source-backed story map from the research")
    rep.info(f"Story map ready: {len(value['beats'])} beats, template {value['template'].replace('_', ' ')}")


def user_edit(script: Script, raw: dict) -> tuple[dict, list[str]]:
    """Validate a user edit while keeping the map's original input fingerprint."""
    if not script.story_map:
        raise ValueError("Build the story map before editing it")
    cleaned = clean(raw, source_numbers(script))
    if cleaned.warnings or cleaned.problems:
        raise ValueError(" ".join([*cleaned.warnings, *cleaned.problems]))
    value = {"version": 1, "input_hash": script.story_map.get("input_hash", ""),
             "input_parts": script.story_map.get("input_parts", {}), "made_at": utcnow().isoformat(), "by": "you",
             **cleaned.value}
    return value, cleaned.warnings
