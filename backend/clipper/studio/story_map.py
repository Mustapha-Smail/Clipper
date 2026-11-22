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
from . import compliance, data_graphics, plain

Template = Literal["mechanism", "story_loop", "journey", "ranking"]
StoryShape = Literal["parallel", "explain", "scorecard", "investigation"]
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
    # v2 parallel maps scope beats to a lane and a matched checkpoint. They are
    # optional so maps written before story shapes remain readable unchanged.
    lane: str | None = None
    checkpoint: str | None = None


class Ending(BaseModel):
    payoff: str
    callback: str = ""
    source_numbers: list[int] = Field(default_factory=list)


class ParallelLane(BaseModel):
    # Keep parsing permissive so clean() can report the exact unsafe ID in its
    # structural-problems list instead of raising a generic Pydantic error.
    id: str
    label: str
    rule: str
    promised_consequence: str


class Parallel(BaseModel):
    shared_start: str
    shared_clock: str
    lanes: list[ParallelLane]


class Draft(BaseModel):
    template: Template
    # None is the compatibility value for a legacy story map. New maps built by
    # this module always carry one of the four canonical shapes.
    story_shape: StoryShape | None = None
    parallel: Parallel | None = None
    opening: Opening
    beats: list[Beat]
    ending: Ending
    # Omitted on legacy maps. New maps may offer fixed numbers for the
    # storyboard, but never coordinates or an unsourced display value.
    data_cards: list[data_graphics.Card] | None = Field(
        default=None,
        description="Optional fixed graphics for exact sourced values. Every card cites retained research source "
                    "numbers; never invent or infer a displayed value.",
    )


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
        "form": script.form,
        "story_shape": script.story_shape,
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
    "form": "The script form (long, Short or micro-Short) changed.",
    "story_shape": "The script story shape changed.",
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


def clean(raw: dict, source_numbers: set[int], expected_shape: str | None = None) -> Cleaned:
    """Parse a proposed/editable map, drop unknown citations, and list structural problems."""
    draft = Draft.model_validate(raw)
    # Excluding None is important for legacy maps: optional v2 fields must not
    # be materialised as nulls when a user merely opens and saves a v1 map.
    value = draft.model_dump(exclude_none=True)
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

    resolved_cards = data_graphics.resolve(value.get("data_cards"), source_numbers)
    # Keep the authored cards in the source-backed contract so editors and
    # fact-check/export paths can see their cited values. Invalid cards make
    # the map technically invalid rather than disappearing silently.
    card_problems = list(resolved_cards.omissions)

    problems: list[str] = card_problems
    shape = value.get("story_shape")
    if expected_shape and shape != expected_shape:
        problems.append(f"Story shape is '{shape or 'legacy'}', but the script requires '{expected_shape}'.")
    # Lane/checkpoint fields are meaningful only for a parallel map. This is a
    # hard contract: silently ignoring them would make a requested comparison
    # disappear from the eventual narration.
    has_parallel_metadata = value.get("parallel") is not None or any(
        beat.get("lane") is not None or beat.get("checkpoint") is not None for beat in value["beats"])
    if shape != "parallel":
        if has_parallel_metadata:
            problems.append("Lane and checkpoint metadata is only allowed for the parallel story shape.")
    else:
        parallel = value.get("parallel")
        if parallel is None:
            problems.append("Parallel story shape requires shared_start, shared_clock and two lanes.")
        else:
            if not parallel["shared_start"].strip():
                problems.append("Parallel shared_start is empty.")
            if not parallel["shared_clock"].strip():
                problems.append("Parallel shared_clock is empty.")
            lanes = parallel["lanes"]
            if len(lanes) != 2:
                problems.append(f"Parallel stories require exactly two lanes; got {len(lanes)}.")
            lane_ids = [lane["id"] for lane in lanes]
            if len(set(lane_ids)) != len(lane_ids):
                problems.append("Parallel lanes must have distinct safe IDs.")
            for lane in lanes:
                if not _safe_loop(lane["id"]):
                    problems.append(f"Parallel lane has unsafe id '{lane['id']}'.")
                if not lane["label"].strip() or not lane["rule"].strip() or not lane["promised_consequence"].strip():
                    problems.append(f"Parallel lane '{lane['id']}' needs a label, rule and promised consequence.")
            known_lanes = set(lane_ids)
            checkpoints: dict[str, set[str]] = {lane_id: set() for lane_id in known_lanes}
            for index, beat in enumerate(value["beats"], 1):
                lane_id, checkpoint = beat.get("lane"), beat.get("checkpoint")
                if not lane_id:
                    problems.append(f"Beat {index} is missing a lane for the parallel story shape.")
                elif not _safe_loop(lane_id):
                    problems.append(f"Beat {index} has unsafe lane id '{lane_id}'.")
                elif lane_id not in known_lanes:
                    problems.append(f"Beat {index} names unknown lane '{lane_id}'.")
                if not checkpoint:
                    problems.append(f"Beat {index} is missing a checkpoint for the parallel story shape.")
                elif not _safe_loop(checkpoint):
                    problems.append(f"Beat {index} has unsafe checkpoint id '{checkpoint}'.")
                elif lane_id in checkpoints:
                    checkpoints[lane_id].add(checkpoint)
            if len(checkpoints) == 2:
                shared = set.intersection(*checkpoints.values()) if checkpoints else set()
                if len(shared) < 2:
                    missing = sorted(set.union(*checkpoints.values()) - shared) if checkpoints else []
                    detail = f"; unmatched checkpoints: {', '.join(missing)}" if missing else ""
                    problems.append("Parallel stories need at least two checkpoints represented in both lanes"
                                    + detail + ".")
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
    keys = ("template", "opening", "beats", "ending", "data_cards")
    value = {key: script.story_map[key] for key in keys if key in script.story_map}
    # v2 fields are deliberately passed through as structured JSON. Legacy
    # maps have neither key and therefore render exactly as they did before.
    for key in ("story_shape", "parallel"):
        if key in script.story_map:
            value[key] = script.story_map[key]
    return json.dumps(value, ensure_ascii=False, indent=2)


def _prompt(script: Script) -> str:
    from . import script as script_mod

    research = script.research or {}
    sources = "\n".join(f"[{s['n']}] {s.get('title') or s['url']} — {s['url']}" for s in research.get("sources", []))
    return (f"<video>\n{script_mod._brief(script)}\n</video>\n\n"
            f"<story_shape>{script.story_shape or 'choose one canonical shape'}</story_shape>\n"
            f"<research_notes>\n{research.get('notes', '')}\n</research_notes>\n\n"
            f"<sources>\n{sources or '(none)'}\n</sources>")


def _shape_prompt(script: Script) -> str:
    """Load the editable, shape-specific rules for a new map."""
    shape = script.story_shape or "explain"
    if shape not in {"parallel", "explain", "scorecard", "investigation"}:
        shape = "explain"
    return prompts.render(f"story_shape_{shape}")


def _clean_ai_draft(draft: Draft, script: Script) -> tuple[Cleaned, bool]:
    """Clean one model proposal, dropping schema-only parallel fields when they were not requested.

    The structured schema has to expose lane/checkpoint fields so a Parallel map can use them. Some
    providers fill those optional fields for every shape. They carry no meaning in a non-parallel map,
    so removing them at this AI boundary is safer than blocking the complete script pipeline. User edits
    still go through ``clean`` unchanged and continue to reject cross-shape metadata explicitly.
    """
    raw = draft.model_dump()
    shape = script.story_shape or raw.get("story_shape") or "explain"
    removed = False
    if shape != "parallel":
        if raw.get("parallel") is not None:
            raw["parallel"] = None
            removed = True
        for beat in raw.get("beats", []):
            for field in ("lane", "checkpoint"):
                if beat.get(field) is not None:
                    beat[field] = None
                    removed = True
    return clean(raw, source_numbers(script), expected_shape=script.story_shape), removed


def _repair_prompt(script: Script, draft: Draft, problems: list[str]) -> str:
    """Ask for one complete structural correction without permitting new research claims."""
    errors = "\n".join(f"- {problem}" for problem in problems)
    proposed = json.dumps(draft.model_dump(exclude_none=True), ensure_ascii=False, indent=2)
    return (
        _prompt(script)
        + "\n\n<proposed_story_map>\n"
        + proposed
        + "\n</proposed_story_map>\n\n<validation_errors>\n"
        + errors
        + "\n</validation_errors>\n\n"
        "Return one complete corrected story map. Correct every validation error while preserving the supplied "
        "research claims and source numbers. Do not add facts, sources or drama. For a non-parallel story omit "
        "parallel, lane and checkpoint metadata. Resolve every opened loop in a later beat's resolves list; the "
        "ending cannot resolve a loop implicitly."
    )


def build(script: Script, rep: Reporter) -> dict:
    """Ask for and validate one map. Does not write the database."""
    try:
        system = PROMPT.format(plain=plain.rules()) + "\n\n" + _shape_prompt(script)
        content = _prompt(script)
        draft = llm.ask("story_map", system, content, Draft, effort="high")
    except llm.AiUnavailable as err:
        rep.warn(f"Story map unavailable: {err}")
        raise RuntimeError(f"Could not build the story map: {err}") from err
    cleaned, removed = _clean_ai_draft(draft, script)
    if removed:
        rep.info("Ignored parallel-only fields that the model supplied for a non-parallel story map")
    if cleaned.problems:
        first_problems = list(cleaned.problems)
        rep.info("Repairing the proposed story map from its structural validation errors")
        try:
            draft = llm.ask(
                "story_map",
                system,
                _repair_prompt(script, draft, first_problems),
                Draft,
                effort="high",
            )
        except llm.AiUnavailable as err:
            rep.warn(f"Story map repair unavailable: {err}")
            raise RuntimeError(f"Could not repair the story map: {err}") from err
        cleaned, removed = _clean_ai_draft(draft, script)
        if removed:
            rep.info("Ignored parallel-only fields in the repaired non-parallel story map")
    for warning in cleaned.warnings:
        rep.warn(warning)
    for problem in cleaned.problems:
        rep.warn(problem)
    if cleaned.problems:
        raise RuntimeError("The proposed story map was not structurally valid: " + " ".join(cleaned.problems))
    input_hash, input_parts = input_fingerprint(script)
    # An older prompt override may not know about story_shape. Its output is
    # accepted for non-parallel scripts and receives the explicit v2 default;
    # parallel scripts still fail above because their required metadata cannot
    # be inferred safely.
    has_shape = "story_shape" in draft.model_fields_set
    if has_shape or script.story_shape:
        cleaned.value.setdefault("story_shape", script.story_shape or "explain")
    # A pre-v2 prompt override which omits story_shape remains a readable v1
    # map. New model responses and any script with an explicit shape are v2.
    version = 2 if has_shape or script.story_shape else 1
    return {"version": version, "input_hash": input_hash, "input_parts": input_parts,
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
    # A v2 map's shape is part of its editable contract. Keep it when an old
    # client sends the legacy body without the new optional field.
    if raw.get("story_shape") is None and script.story_map.get("story_shape") is not None:
        raw = {**raw, "story_shape": script.story_map["story_shape"]}
    cleaned = clean(raw, source_numbers(script), expected_shape=script.story_shape if script.story_shape else None)
    if cleaned.warnings or cleaned.problems:
        raise ValueError(" ".join([*cleaned.warnings, *cleaned.problems]))
    value = {"version": max(2, int(script.story_map.get("version", 1))),
             "input_hash": script.story_map.get("input_hash", ""),
             "input_parts": script.story_map.get("input_parts", {}), "made_at": utcnow().isoformat(), "by": "you",
             **cleaned.value}
    return value, cleaned.warnings
