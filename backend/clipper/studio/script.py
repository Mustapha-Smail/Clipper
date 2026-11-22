"""The script studio: research an approved idea, write the narration, check its facts.

Three steps, each run by the worker as a job stage:

  research   Claude searches the web and writes notes, every fact with its
             numbered source.
  write      Claude writes the narration from those notes only, in sections.
  factcheck  Every factual claim in the (possibly edited) script is checked
             against the notes; claims the notes do not cover are searched on
             the web. Each gets a verdict and its sources.

You edit the script between steps; a fact check records a hash of the text it
checked, so the app can tell when the script changed since.
"""

import hashlib
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from .. import llm, prompts
from ..db import session_scope
from ..db.models import Script
from ..jobs import Reporter
from . import plain

WORDS_PER_MINUTE = 150  # brisk narration
MAX_WEB_CHECKS = 12  # claims searched per fact check, beyond the research notes


# --- helpers -------------------------------------------------------------------------


def text_hash(sections: list[dict]) -> str:
    joined = "\n".join(f"{s.get('heading', '')}\n{s.get('text', '')}" for s in sections)
    return hashlib.sha256(joined.encode()).hexdigest()


def word_count(sections: list[dict]) -> int:
    return sum(len(re.findall(r"\S+", s.get("text", ""))) for s in sections)


def numbered(sections: list[dict]) -> str:
    return "\n\n".join(
        f'<section key="{s["key"]}" heading="{s["heading"]}">\n{s["text"]}\n</section>' for s in sections
    )


def plain_text(script: Script) -> str:
    """The script as you would print it; micro-Short sections are visual cue sheets."""
    lines = [script.title.upper(), ""]
    for s in script.sections:
        lines += [f"[{s['heading']}]", s["text"].strip(), ""]
    return "\n".join(lines).strip() + "\n"


def _brief(script: Script) -> str:
    idea = script.idea
    if script.form == "micro_short":
        lines = [
            f"Title: {script.title}",
            f"Channel theme: {script.theme}",
            "Format: a silent vertical micro concept test, not narration and not a normal captioned Short.",
            "Story promise: the same relatable start, one hidden rule and two visibly different outcomes.",
            "Use no more than two lanes and one object or event that works with the sound off.",
            "End by restoring the opening visual for a clean replay loop.",
            f"Target length: exactly {script.target_seconds} seconds including the loop (allowed: 12–18 seconds).",
        ]
    elif script.video == "short":
        words = round(script.target_seconds * WORDS_PER_MINUTE / 60)
        rehooks = "two rehook lines, spaced out" if script.target_seconds > 45 else "one rehook line"
        lines = [
            f"Title: {script.title}",
            f"Channel theme: {script.theme}",
            "Format: a YouTube Short only (vertical, no long video). Write exactly ONE section, built like this:",
            "- Hook: one sentence that names the topic and starts on the action or the surprising claim. "
            'No "Did you know", no scene-setting.',
            "- Lock-in: the next 2 or 3 sentences give the stakes (why it matters to the viewer) and ask "
            "the big question the video answers.",
            f'- Body with {rehooks} in it: a new question or a twist ("But here\'s the strange part") that '
            f"gives a fresh reason to keep watching. The first twist lands within about the first 10 "
            f"seconds ({round(10 * WORDS_PER_MINUTE / 60)} words).",
            "- The payoff line last. No intro, no outro, no call to subscribe.",
            f"Target length: about {script.target_seconds} seconds ({words} words of narration)",
        ]
    else:
        lines = [
            f"Title: {script.title}",
            f"Channel theme: {script.theme}",
            f"Format: {'one topic' if script.format == 'single' else 'a list, one chapter per item'}",
            f"Target length: about {script.target_minutes} minutes "
            f"({script.target_minutes * WORDS_PER_MINUTE} words of narration)",
        ]
    lines.append(f"Audience: {plain.audience()}")
    if idea is not None:
        lines.append(f"Hook: {idea.hook}")
        if idea.expected.strip():
            lines.append(
                f"Expected guess: {idea.expected.strip()} (what most viewers would guess; the lock-in or "
                "the first twist can use it, and the answer overturns it)"
            )
        lines += [f"The surprising answer: {idea.answer}", f"Angle: {idea.angle}", "Beats:"]
        lines += [f"- {b['text']}" for b in idea.beats]
    return "\n".join(lines)


def _load(script_id: int) -> Script:
    with session_scope() as db:
        script = db.get(Script, script_id)
        if script is None:
            raise RuntimeError(f"Script {script_id} no longer exists")
        script.idea  # noqa: B018 - load the relationship before the session closes
        db.expunge(script)
        return script


# --- 1. research ----------------------------------------------------------------------

RESEARCH = prompts.Prompt("script_research")


def research(script_id: int, rep: Reporter) -> None:
    script = _load(script_id)
    rep.info("Searching the web for sources")
    notes, sources, searches = llm.search("research", RESEARCH, f"<video>\n{_brief(script)}\n</video>", max_searches=10)
    rep.info(f"Research done: {searches} searches, {len(sources.items)} sources cited")
    if not sources.items:
        rep.warn("No source was cited. The fact check will have nothing to check against")
    with session_scope() as db:
        row = db.get(Script, script_id)
        row.research = {"notes": notes, "sources": sources.items, "searches": searches}


# --- 2. write ------------------------------------------------------------------------


class Section(BaseModel):
    heading: str = Field(description="A short label: 'Cold open', the chapter's topic, or 'Outro'")
    text: str = Field(description="The narration, exactly as it will be read aloud")
    short: bool = Field(description="True if this section works on its own as a 30 to 60 second Short")


class Draft(BaseModel):
    title: str = Field(description="The video's YouTube title")
    sections: list[Section]


MICRO_CUE_LABELS = ("SETUP", "RULE", "OUTCOME A", "OUTCOME B", "LOOP")


class MicroSection(BaseModel):
    heading: str = Field(description="Use 'Visual loop'")
    text: str = Field(description="Exactly five labelled visual cue lines: SETUP, RULE, OUTCOME A, OUTCOME B, LOOP")
    short: bool = Field(default=True, description="Always true")


class MicroDraft(BaseModel):
    title: str = Field(description="The video's original YouTube title")
    sections: list[MicroSection] = Field(min_length=1, max_length=1)


def micro_cues(sections: list[dict]) -> list[dict]:
    """Validate and return the five designed visual cues of a silent micro-Short.

    Code owns this small contract so a cue track never silently becomes prose
    that downstream code could misdescribe as spoken narration.
    """
    if len(sections) != 1:
        raise ValueError("A micro-Short needs exactly one visual cue section")
    lines = [line.strip() for line in sections[0].get("text", "").splitlines() if line.strip()]
    if len(lines) != len(MICRO_CUE_LABELS):
        raise ValueError("A micro-Short cue sheet needs exactly five lines: " + ", ".join(MICRO_CUE_LABELS))
    out = []
    for expected, line in zip(MICRO_CUE_LABELS, lines, strict=True):
        prefix, separator, text = line.partition(":")
        if not separator or prefix.strip().upper() != expected or not text.strip():
            raise ValueError(f"A micro-Short cue line must be '{expected}: …'")
        out.append({"kind": expected.lower().replace(" ", "_"), "label": expected, "text": text.strip()})
    return out


SPOKEN = prompts.Prompt("script_spoken")


WRITE = prompts.Prompt("script_write")
MICRO_WRITE = prompts.Prompt("script_micro_short")


def write(script_id: int, rep: Reporter) -> None:
    script = _load(script_id)
    if not script.research:
        raise RuntimeError("Research the script first")
    from . import story_map

    # Old queued jobs can still reach this stage without a map. New pipelines and redrafts build one first.
    # When a map exists it must be current and valid; it is never silently omitted.
    map_context = story_map.prompt_value(script)
    prompt = (
        f"<video>\n{_brief(script)}\n</video>\n\n"
        + (f"<story_map>\n{map_context}\n</story_map>\n\n" if map_context else "")
        + f"<research_notes>\n{script.research['notes']}\n</research_notes>"
    )
    micro = script.form == "micro_short"
    rep.info("Designing the silent visual cue sheet" if micro else "Writing the narration")
    output = MicroDraft if micro else Draft
    system = MICRO_WRITE.format(plain=plain.rules()) if micro else WRITE.format(plain=plain.rules())
    draft = llm.ask("script_write", system, prompt, output, effort="high")
    sections = [{"key": uuid.uuid4().hex[:8], **s.model_dump()} for s in draft.sections]
    if micro:
        sections = [{**sections[0], "heading": "Visual loop", "short": True}]
        micro_cues(sections)
    elif script.video == "short":  # a Short is one section, and it is the Short
        sections = [{**sections[0], "text": "\n\n".join(s["text"] for s in sections), "short": True}]
    words = word_count(sections)
    if micro:
        rep.info(f"Visual cue sheet written: five designed cues for {script.target_seconds} seconds")
    else:
        rep.info(
            f"Draft written: {len(sections)} sections, {words} words, about {words / WORDS_PER_MINUTE:.1f} minutes"
        )
    with session_scope() as db:
        row = db.get(Script, script_id)
        # The title is one of the map's promises and one of its hashed inputs. A map-based draft must not silently
        # replace it and make its own plan stale as soon as writing finishes. Legacy direct writes keep old behaviour.
        if not script.story_map:
            row.title = draft.title
        row.sections = sections
        row.claims = None
        row.checked_hash = None
        row.hold = None


class Rewrite(BaseModel):
    text: str


REWRITE = prompts.Prompt("script_rewrite")


def rewrite_section(script: Script, key: str, instruction: str) -> str:
    section = next((s for s in script.sections if s["key"] == key), None)
    if section is None:
        raise ValueError("Section not found")
    from . import story_map

    map_context = story_map.prompt_value(script)
    prompt = (
        f"<research_notes>\n{(script.research or {}).get('notes', '')}\n</research_notes>\n\n"
        + (f"<story_map>\n{map_context}\n</story_map>\n\n" if map_context else "")
        + f"<script>\n{numbered(script.sections)}\n</script>\n\n"
        + f'Rewrite the section with key "{key}" ({section["heading"]}). Instruction: {instruction}'
    )
    return llm.ask("rewrite", REWRITE.format(plain=plain.rules()), prompt, Rewrite, effort="medium").text.strip()


class SpokenSection(BaseModel):
    key: str
    text: str


class SpokenScript(BaseModel):
    sections: list[SpokenSection]


MAKE_SPOKEN = prompts.Prompt("script_make_spoken")


def make_spoken(script_id: int, rep: Reporter) -> None:
    """Rewrite the whole script for the ear in one call, keeping the facts. The old text is kept for undo."""
    script = _load(script_id)
    if not script.sections:
        raise RuntimeError("The script has no text yet")
    from . import story_map

    map_context = story_map.prompt_value(script)
    prompt = (
        f"<story_map>\n{map_context}\n</story_map>\n\n" if map_context else ""
    ) + f"<script>\n{numbered(script.sections)}\n</script>"
    rep.info("Rewriting the script so it sounds spoken")
    result = llm.ask("script_write", MAKE_SPOKEN, prompt, SpokenScript, effort="high")
    by_key = {s.key: s.text.strip() for s in result.sections if s.text.strip()}
    missing = [s["heading"] for s in script.sections if s["key"] not in by_key]
    if missing:
        rep.warn(f"Kept as it was (not returned by the model): {', '.join(missing)}")
    sections = [{**s, "text": by_key.get(s["key"], s["text"])} for s in script.sections]
    before, after = word_count(script.sections), word_count(sections)
    with session_scope() as db:
        row = db.get(Script, script_id)
        row.previous_sections = script.sections
        row.sections = sections
        row.status = "draft"
    rep.info(f"Done: {before} words before, {after} after. Check the facts again before recording.")


MAKE_SIMPLER = prompts.Prompt("script_make_simpler")


def make_simpler(script_id: int, rep: Reporter) -> None:
    """Rewrite the whole script for the audience in one call: plainer words, fewer names and numbers.
    The old text is kept for undo, and the facts must be checked again."""
    script = _load(script_id)
    if not script.sections:
        raise RuntimeError("The script has no text yet")
    before_check = plain.check(script.sections, plain.level(), script.title)
    rep.info(f"Making the script simpler for: {plain.audience()}")
    from . import story_map

    map_context = story_map.prompt_value(script)
    notes = (script.research or {}).get("notes", "")
    prompt = (
        (f"<research_notes>\n{notes}\n</research_notes>\n\n" if notes else "")
        + (f"<story_map>\n{map_context}\n</story_map>\n\n" if map_context else "")
        + f"<script>\n{numbered(script.sections)}\n</script>"
    )
    result = llm.ask("simplify", MAKE_SIMPLER.format(plain=plain.rules()), prompt, SpokenScript, effort="high")
    by_key = {x.key: x.text.strip() for x in result.sections if x.text.strip()}
    missing = [x["heading"] for x in script.sections if x["key"] not in by_key]
    if missing:
        rep.warn(f"Kept as it was (not returned by the model): {', '.join(missing)}")
    sections = [{**x, "text": by_key.get(x["key"], x["text"])} for x in script.sections]
    after_check = plain.check(sections, plain.level(), script.title)
    with session_scope() as db:
        row = db.get(Script, script_id)
        row.previous_sections = script.sections
        row.sections = sections
        row.status = "draft"
    words = f"{word_count(script.sections)} words before, {word_count(sections)} after"
    if before_check and after_check:
        rep.info(
            f"Done: {words}; plain-language check {before_check['score']} -> {after_check['score']}. "
            "Check the facts again before recording."
        )
    else:
        rep.info(f"Done: {words}. Check the facts again before recording.")


# --- 3. fact check -------------------------------------------------------------------

Verdict = Literal["supported", "contradicted", "unclear", "joke"]


class Claim(BaseModel):
    section: str = Field(description="The key of the section the claim is in")
    claim: str = Field(description="The factual statement, quoted or closely paraphrased from the script")
    verdict: Literal["supported", "contradicted", "not_in_notes", "joke"] = Field(
        description="supported: the notes back it. contradicted: the notes say otherwise. "
        "not_in_notes: a real factual claim the notes do not cover. "
        "joke: exaggeration, opinion or comic aside nobody would take literally"
    )
    sources: list[int] = Field(description="Source numbers from the notes that back or contradict it")
    note: str = Field(description="One sentence: what the notes say, or what is wrong")


class Claims(BaseModel):
    claims: list[Claim]


CHECK = prompts.Prompt("script_factcheck")


class WebVerdict(BaseModel):
    index: int = Field(description="The claim's number in the list you were given")
    verdict: Literal["supported", "contradicted", "unclear"]
    sources: list[int] = Field(description="Source numbers from the notes that back or contradict it")
    note: str


class WebVerdicts(BaseModel):
    verdicts: list[WebVerdict]


VERIFY = prompts.Prompt("script_verify")

READ_VERDICTS = prompts.Prompt("script_read_verdicts")


def factcheck(script_id: int, rep: Reporter) -> None:
    script = _load(script_id)
    if not script.sections:
        raise RuntimeError("The script has no text to check")
    research_notes = (script.research or {}).get("notes", "(no research notes)")
    sources = llm.Sources((script.research or {}).get("sources", []))

    rep.info("Listing the claims and checking them against the research")
    found = llm.ask(
        "factcheck",
        CHECK,
        f"<research_notes>\n{research_notes}\n</research_notes>\n\n<script>\n{numbered(script.sections)}\n</script>",
        Claims,
        effort="high",
    ).claims
    claims = [c.model_dump() for c in found]

    open_claims = [c for c in claims if c["verdict"] == "not_in_notes"]
    if len(open_claims) > MAX_WEB_CHECKS:
        rep.warn(f"{len(open_claims)} claims are not in the research; searching the first {MAX_WEB_CHECKS}")
    to_search = open_claims[:MAX_WEB_CHECKS]
    if to_search:
        rep.info(f"Searching the web for {len(to_search)} claims the research does not cover")
        listing = "\n".join(f"{i}. {c['claim']}" for i, c in enumerate(to_search, 1))
        notes, sources, searches = llm.search(
            "verify",
            VERIFY,
            f"<claims>\n{listing}\n</claims>",
            max_searches=min(2 * len(to_search), 15),
            sources=sources,
        )
        verdicts = llm.ask(
            "verdicts",
            READ_VERDICTS,
            f"<claims>\n{listing}\n</claims>\n\n<notes>\n{notes}\n</notes>",
            WebVerdicts,
            effort="low",
        ).verdicts
        by_index = {v.index: v for v in verdicts}
        for i, claim in enumerate(to_search, 1):
            v = by_index.get(i)
            claim.update(
                verdict=v.verdict if v else "unclear",
                sources=v.sources if v else [],
                note=v.note if v else "No verdict came back for this claim",
            )
        rep.info(f"Web check done: {searches} searches")
    for claim in claims:
        if claim["verdict"] == "not_in_notes":
            claim.update(verdict="unclear", note="Not covered by the research and not searched: " + claim["note"])

    counts = {v: sum(c["verdict"] == v for c in claims) for v in ("supported", "contradicted", "unclear")}
    rep.info(
        f"{len(claims)} claims: {counts['supported']} supported, {counts['contradicted']} contradicted, "
        f"{counts['unclear']} unclear"
    )
    if counts["contradicted"]:
        rep.warn(f"{counts['contradicted']} claims are contradicted by the sources: fix them before recording")
    with session_scope() as db:
        row = db.get(Script, script_id)
        if text_hash(row.sections) != text_hash(script.sections):
            rep.warn("The script was edited while it was being checked; run the check again")
        row.claims = claims
        row.checked_hash = text_hash(script.sections)
        row.research = {**(row.research or {"notes": "", "searches": 0}), "sources": sources.items}
