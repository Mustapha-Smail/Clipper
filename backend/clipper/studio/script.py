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

from .. import llm
from ..db import session_scope
from ..db.models import Script
from ..jobs import Reporter
from . import plain

WORDS_PER_MINUTE = 150      # brisk narration
MAX_WEB_CHECKS = 12         # claims searched per fact check, beyond the research notes


# --- helpers -------------------------------------------------------------------------

def text_hash(sections: list[dict]) -> str:
    joined = "\n".join(f"{s.get('heading', '')}\n{s.get('text', '')}" for s in sections)
    return hashlib.sha256(joined.encode()).hexdigest()


def word_count(sections: list[dict]) -> int:
    return sum(len(re.findall(r"\S+", s.get("text", ""))) for s in sections)


def numbered(sections: list[dict]) -> str:
    return "\n\n".join(f"<section key=\"{s['key']}\" heading=\"{s['heading']}\">\n{s['text']}\n</section>"
                       for s in sections)


def plain_text(script: Script) -> str:
    """The script as you would print it to read aloud."""
    lines = [script.title.upper(), ""]
    for s in script.sections:
        lines += [f"[{s['heading']}]", s["text"].strip(), ""]
    return "\n".join(lines).strip() + "\n"


def _brief(script: Script) -> str:
    idea = script.idea
    if script.video == "short":
        words = round(script.target_seconds * WORDS_PER_MINUTE / 60)
        lines = [f"Title: {script.title}", f"Channel theme: {script.theme}",
                 "Format: a YouTube Short only (vertical, no long video). Write exactly ONE section: the hook in "
                 "the first two seconds, then the surprising answer, ending on the payoff line. No intro, no "
                 "outro, no call to subscribe.",
                 f"Target length: about {script.target_seconds} seconds ({words} words of narration)"]
    else:
        lines = [f"Title: {script.title}", f"Channel theme: {script.theme}",
                 f"Format: {'one topic' if script.format == 'single' else 'a list, one chapter per item'}",
                 f"Target length: about {script.target_minutes} minutes "
                 f"({script.target_minutes * WORDS_PER_MINUTE} words of narration)"]
    lines.append(f"Audience: {plain.audience()}")
    if idea is not None:
        lines += [f"Hook: {idea.hook}", f"The surprising answer: {idea.answer}", f"Angle: {idea.angle}", "Beats:"]
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

RESEARCH = """\
You research one video for a faceless animated YouTube channel (simple drawn characters, \
deadpan comic narration). A writer will script the video from your notes alone, so the \
notes must hold every fact the video needs, and nothing you could not source.

Search the web. Prefer primary and reference sources: manufacturers and standards \
bodies, universities, museums, encyclopedias, established science outlets. Avoid \
content farms and other YouTube channels.

Start with "The one thing to understand": the single idea the video must leave the \
viewer with, in one plain sentence the audience would get.

Then write the notes as short bullet points grouped under one heading per beat, then:
- "Simple explanations": everyday comparisons that make the mechanism easy to picture for \
the audience, the best first.
- "Plain words": every technical term the topic needs, each with a plain replacement or a \
one-line everyday explanation.
- "Surprising details": numbers, names, dates and odd facts a viewer would repeat. Put the \
three to five best first; mark the rest "(optional)": the writer leaves out what the \
story does not need.
- "Myths and disputes": popular claims on this topic that are false or contested, and \
what is actually known. The script must not repeat a myth as fact.

Every factual bullet must be backed by a source you found. If you cannot confirm \
something, leave it out or list it under Myths and disputes as unconfirmed."""


def research(script_id: int, rep: Reporter) -> None:
    script = _load(script_id)
    rep.info("Searching the web for sources")
    notes, sources, searches = llm.search("research", RESEARCH, f"<video>\n{_brief(script)}\n</video>",
                                             max_searches=10)
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


SPOKEN = """\
Write for the ear, as a person talking to one viewer, not as an article read aloud:
- Talk to the viewer: "you", "your", and now and then a rhetorical question.
- Contractions everywhere a person would use them (it's, you'd, that's, doesn't).
- Vary the rhythm: some two-word sentences, some long ones that run on with commas and "and".
- Signpost like someone explaining across a table: "So.", "Now,", "Here's the thing:", "Okay, but".
- Mark a beat with an ellipsis (…) before a punchline, and put asides between em dashes.
- At most one light filler per paragraph ("well,", "honestly,", "basically"); never "um" or "uh".
- One idea per paragraph; a new paragraph is where the narrator takes a breath."""


WRITE = """\
You write narration for a faceless animated YouTube channel: simple drawn characters on \
screen, one narrator. The voice is friendly and lightly funny, with jokes that come from \
the facts themselves and quick asides, never from mocking the viewer. No stage \
directions, no music cues, no source markers, no emojis.

{plain}

""" + SPOKEN + """

Structure:
- A cold open of at most 20 seconds that states the hook and makes the viewer need the answer.
- The body, in sections: for a list video, one section per item; for a single topic, \
3 to 6 sections that each move the explanation forward and end on a small payoff.
- A short outro that lands the final surprise. One light line asking to subscribe is \
fine; no begging.

Use only facts from the research notes, and only those the one idea needs: leaving a \
true detail out is better than crowding the viewer. Do not add numbers, dates, names or \
claims that the notes do not contain, and never present an item from "Myths and disputes" as true; \
the myth itself can be a joke or a setup if the script corrects it. Hit the target length \
within about 10 percent."""


def write(script_id: int, rep: Reporter) -> None:
    script = _load(script_id)
    if not script.research:
        raise RuntimeError("Research the script first")
    prompt = (f"<video>\n{_brief(script)}\n</video>\n\n"
              f"<research_notes>\n{script.research['notes']}\n</research_notes>")
    rep.info("Writing the narration")
    draft = llm.ask("script_write", WRITE.format(plain=plain.rules()), prompt, Draft, effort="high")
    sections = [{"key": uuid.uuid4().hex[:8], **s.model_dump()} for s in draft.sections]
    if script.video == "short":       # a Short is one section, and it is the Short
        sections = [{**sections[0], "text": "\n\n".join(s["text"] for s in sections), "short": True}]
    words = word_count(sections)
    rep.info(f"Draft written: {len(sections)} sections, {words} words, about {words / WORDS_PER_MINUTE:.1f} minutes")
    with session_scope() as db:
        row = db.get(Script, script_id)
        row.title = draft.title
        row.sections = sections
        row.claims = None
        row.checked_hash = None


class Rewrite(BaseModel):
    text: str


REWRITE = WRITE + """

You are rewriting one section of an existing script following the creator's instruction. \
Keep its facts, keep the voice of the rest of the script, and return only the new \
narration for that section."""


def rewrite_section(script: Script, key: str, instruction: str) -> str:
    section = next((s for s in script.sections if s["key"] == key), None)
    if section is None:
        raise ValueError("Section not found")
    prompt = (f"<research_notes>\n{(script.research or {}).get('notes', '')}\n</research_notes>\n\n"
              f"<script>\n{numbered(script.sections)}\n</script>\n\n"
              f"Rewrite the section with key \"{key}\" ({section['heading']}). Instruction: {instruction}")
    return llm.ask("rewrite", REWRITE.format(plain=plain.rules()), prompt, Rewrite, effort="medium").text.strip()


class SpokenSection(BaseModel):
    key: str
    text: str


class SpokenScript(BaseModel):
    sections: list[SpokenSection]


MAKE_SPOKEN = """\
You turn an existing narration, written to be read, into narration written to be spoken \
by the friendly, lightly funny narrator of a faceless animated YouTube channel.

""" + SPOKEN + """

Keep every fact exactly as it is: no new facts, numbers, names or claims, none dropped. \
Keep the jokes and keep each section's job. Return every section with its key, in order; \
the length may grow by at most 15 percent."""


def make_spoken(script_id: int, rep: Reporter) -> None:
    """Rewrite the whole script for the ear in one call, keeping the facts. The old text is kept for undo."""
    script = _load(script_id)
    if not script.sections:
        raise RuntimeError("The script has no text yet")
    rep.info("Rewriting the script so it sounds spoken")
    result = llm.ask("script_write", MAKE_SPOKEN, f"<script>\n{numbered(script.sections)}\n</script>",
                     SpokenScript, effort="high")
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


MAKE_SIMPLER = """\
You make an existing narration easy to follow for the audience of a faceless animated \
YouTube channel, without changing what it says is true.

{plain}

""" + SPOKEN + """

Every fact you keep stays exactly as true as it was: no new facts, numbers, names or \
claims. You may leave out names, dates, numbers and details the main idea does not need, \
replace a term with plain words, round a number, and add an everyday comparison (a \
comparison is not a fact: "like a spreadsheet"). Keep each section's job and the jokes \
that land for everyone. Return every section with its key, in order; it may get shorter, \
and grow by at most 10 percent."""


def make_simpler(script_id: int, rep: Reporter) -> None:
    """Rewrite the whole script for the audience in one call: plainer words, fewer names and numbers.
    The old text is kept for undo, and the facts must be checked again."""
    script = _load(script_id)
    if not script.sections:
        raise RuntimeError("The script has no text yet")
    before_check = plain.check(script.sections, plain.level(), script.title)
    rep.info(f"Making the script simpler for: {plain.audience()}")
    notes = (script.research or {}).get("notes", "")
    prompt = (f"<research_notes>\n{notes}\n</research_notes>\n\n" if notes else "") + \
        f"<script>\n{numbered(script.sections)}\n</script>"
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
        rep.info(f"Done: {words}; plain-language check {before_check['score']} -> {after_check['score']}. "
                 "Check the facts again before recording.")
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
                    "joke: exaggeration, opinion or comic aside nobody would take literally")
    sources: list[int] = Field(description="Source numbers from the notes that back or contradict it")
    note: str = Field(description="One sentence: what the notes say, or what is wrong")


class Claims(BaseModel):
    claims: list[Claim]


CHECK = """\
You fact-check the narration of an educational YouTube video against research notes \
whose facts carry numbered sources like [3].

List every factual claim in the script: numbers, dates, names, causes, mechanisms, \
"firsts", superlatives and anything a viewer could repeat as true. Skip pure jokes \
unless they could be mistaken for fact. For each claim give its verdict and the source \
numbers from the notes. Be strict: a claim that goes beyond what the notes say (a bigger \
number, a stronger word like "always" or "invented") is not supported."""


class WebVerdict(BaseModel):
    index: int = Field(description="The claim's number in the list you were given")
    verdict: Literal["supported", "contradicted", "unclear"]
    sources: list[int] = Field(description="Source numbers from the notes that back or contradict it")
    note: str


class WebVerdicts(BaseModel):
    verdicts: list[WebVerdict]


VERIFY = """\
You verify factual claims from the script of an educational YouTube video. Search the \
web for each claim. Prefer primary and reference sources. For each numbered claim, say \
whether reliable sources support it, contradict it, or leave it unclear, and cite them."""

READ_VERDICTS = """\
You turn fact-check notes into one verdict per numbered claim. Use only what the notes \
say; when they do not settle a claim, its verdict is unclear."""


def factcheck(script_id: int, rep: Reporter) -> None:
    script = _load(script_id)
    if not script.sections:
        raise RuntimeError("The script has no text to check")
    research_notes = (script.research or {}).get("notes", "(no research notes)")
    sources = llm.Sources((script.research or {}).get("sources", []))

    rep.info("Listing the claims and checking them against the research")
    found = llm.ask("factcheck", CHECK, f"<research_notes>\n{research_notes}\n</research_notes>\n\n"
                                     f"<script>\n{numbered(script.sections)}\n</script>",
                       Claims, effort="high").claims
    claims = [c.model_dump() for c in found]

    open_claims = [c for c in claims if c["verdict"] == "not_in_notes"]
    if len(open_claims) > MAX_WEB_CHECKS:
        rep.warn(f"{len(open_claims)} claims are not in the research; searching the first {MAX_WEB_CHECKS}")
    to_search = open_claims[:MAX_WEB_CHECKS]
    if to_search:
        rep.info(f"Searching the web for {len(to_search)} claims the research does not cover")
        listing = "\n".join(f"{i}. {c['claim']}" for i, c in enumerate(to_search, 1))
        notes, sources, searches = llm.search("verify", VERIFY, f"<claims>\n{listing}\n</claims>",
                                                 max_searches=min(2 * len(to_search), 15), sources=sources)
        verdicts = llm.ask("verdicts", READ_VERDICTS, f"<claims>\n{listing}\n</claims>\n\n<notes>\n{notes}\n</notes>",
                              WebVerdicts, effort="low").verdicts
        by_index = {v.index: v for v in verdicts}
        for i, claim in enumerate(to_search, 1):
            v = by_index.get(i)
            claim.update(verdict=v.verdict if v else "unclear", sources=v.sources if v else [],
                         note=v.note if v else "No verdict came back for this claim")
        rep.info(f"Web check done: {searches} searches")
    for claim in claims:
        if claim["verdict"] == "not_in_notes":
            claim.update(verdict="unclear", note="Not covered by the research and not searched: " + claim["note"])

    counts = {v: sum(c["verdict"] == v for c in claims) for v in ("supported", "contradicted", "unclear")}
    rep.info(f"{len(claims)} claims: {counts['supported']} supported, {counts['contradicted']} contradicted, "
             f"{counts['unclear']} unclear")
    if counts["contradicted"]:
        rep.warn(f"{counts['contradicted']} claims are contradicted by the sources: fix them before recording")
    with session_scope() as db:
        row = db.get(Script, script_id)
        if text_hash(row.sections) != text_hash(script.sections):
            rep.warn("The script was edited while it was being checked; run the check again")
        row.claims = claims
        row.checked_hash = text_hash(script.sections)
        row.research = {**(row.research or {"notes": "", "searches": 0}), "sources": sources.items}
