"""The hold check: does the narration keep giving the viewer a reason to stay?

The quick tier labels every sentence with one role (hook, stakes, twist, ...),
names the topic it took the title to be about, and audits the script's promise,
open loops and rehooks. Everything else is plain arithmetic on those labels and
the word counts (`score`): how long before the topic is named, how long before
the first twist, the longest stretch without a new question, filler, the ending,
the stakes and whether promises are paid. Every deduction is a line.

It advises; it never rewrites or blocks. The labels are stored on the script
with the hash of the text they were made on (`scripts.hold`), so an edit makes
the check out of date instead of wrong. Not to be confused with
`studio/retention.py`, which deletes old renders.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm.exc import DetachedInstanceError

from .. import llm, prompts
from ..ai_errors import AiUnavailable
from ..db import session_scope
from ..db.models import Script, VoiceTake
from ..jobs import Reporter
from . import plain
from . import script as script_mod

ROLES = (
    "hook",
    "context",
    "stakes",
    "question",
    "twist",
    "rehook",
    "fact",
    "consequence",
    "payoff",
    "callback",
    "filler",
)
TURNS = ("question", "twist", "rehook")  # a new reason to keep watching

# Thresholds, one row per kind of video. Seconds are read at the narration speed (150 words a minute).
TARGETS = {
    #         first twist by, longest gap, points per second over (twist, gap), stakes within this share of words
    "short": {"twist": 10, "gap": 30, "twist_per_s": 2.0, "gap_per_s": 1.0, "stakes": 0.2},
    "long": {"twist": 30, "gap": 60, "twist_per_s": 0.5, "gap_per_s": 0.5, "stakes": 0.2},
}
CAP = 20  # most points one line can take
FILLER_POINTS, FILLER_CAP = 3, 15
REPEAT_POINTS, REPEAT_CAP = 5, 10
ENDING_POINTS = 10
STAKES_POINTS = 10
TOPIC_PER_S = 3  # points per second of narration before the topic is named
PROMISE_MISSING, PROMISE_PARTIAL = 15, 8
LOOP_POINTS, LOOP_CAP = 4, 12
EMPTY_REHOOK_POINTS, EMPTY_REHOOK_CAP = 5, 15
CONCURRENT_POINTS, CONCURRENT_CAP = 4, 8
PROMISE_CAP = 30  # all new promise/loop/rehook deductions together

FILLER = re.compile(
    r"\b(?:moving on|let'?s talk about|next up|without further ado|let'?s dive in|"
    r"let'?s get (?:started|into it)|in (?:this|today'?s) video|stay tuned|"
    r"before we (?:begin|start))\b",
    re.I,
)
STOP = set(
    """a an the of in on at to for and or but is are was were be been it its it's this that these those
why how what when where who which do does did you your yours can could would should will not no yes with from by
about as into than then so very really just ever never most more less much many some any all""".split()
)


# --- the sentences ----------------------------------------------------------------------


def sentences(sections: list[dict]) -> list[dict]:
    """Every sentence numbered from 1 across the sections, with its word count."""
    out = []
    for sec in sections:
        for text in plain.sentences(sec.get("text", "")):
            out.append(
                {"n": len(out) + 1, "section": sec.get("key", ""), "text": text, "words": len(re.findall(r"\S+", text))}
            )
    return out


def sentence_starts(sections: list[dict]) -> list[tuple[str, int]]:
    """Where each numbered sentence starts: (section key, number of the word within its section, counted the way
    the voice timeline counts them, `text.split()`). Found by position in the text, so it holds even when the
    sentence splitter and the word splitter disagree (an ellipsis inside a sentence, "U.S.")."""
    out = []
    for sec in sections:
        text = sec.get("text", "")
        tokens = [m.start() for m in re.finditer(r"\S+", text)]
        cursor = 0
        for sentence in plain.sentences(text):
            at = text.find(sentence, cursor)
            if at < 0:
                at = cursor
            cursor = at + len(sentence)
            out.append((sec.get("key", ""), max(0, sum(1 for t in tokens if t <= at) - 1)))
    return out


MARKED = ("twist", "rehook", "payoff")  # the sentences a storyboard shows as events


def turns(sections: list[dict], stored: dict | None) -> list[dict] | None:
    """The twist, rehook and payoff sentences with where they start ({section, index, role, text}), or None
    when the check is missing or out of date (then nothing is planned on it)."""
    if not stored or stored.get("hash") != script_mod.text_hash(sections):
        return None
    starts = sentence_starts(sections)
    said = sentences(sections)
    roles = {r["n"]: r["role"] for r in stored.get("roles", [])}
    out = []
    for s, (key, index) in zip(said, starts, strict=True):
        role = roles.get(s["n"])
        if role in MARKED:
            out.append({"section": key, "index": index, "role": role, "text": s["text"]})
    return out


def texts_with(sections: list[dict], stored: dict | None, roles: tuple[str, ...]) -> list[str] | None:
    """The text of every sentence labelled with one of `roles`, or None when the check is missing or stale."""
    if not stored or stored.get("hash") != script_mod.text_hash(sections):
        return None
    labelled = {r["n"]: r["role"] for r in stored.get("roles", [])}
    return [s["text"] for s in sentences(sections) if labelled.get(s["n"]) in roles]


def checkpoints(sections: list[dict], stored: dict | None, words: list[dict]) -> list[dict]:
    """Return immutable render-time promise/consequence/turn evidence.

    Sentence numbers are the stable hold contract; this function resolves them
    to the chosen take's word times once, so analytics never recomputes timing
    from an edited script. Checkpoints outside a storyboard's selected section
    are omitted because that render did not contain their evidence.
    """
    if not stored or stored.get("hash") != script_mod.text_hash(sections):
        return []
    said = sentences(sections)
    promise, _, _ = _promise_data(
        stored.get("promise"),
        len(said),
        story_shape=stored.get("story_shape"),
        lane_ids=set(stored.get("lane_ids") or []) or None,
    )
    starts = sentence_starts(sections)
    by_section: dict[str, list[int]] = {}
    for index, word in enumerate(words or []):
        by_section.setdefault(word.get("section", ""), []).append(index)
    # Legacy/fixture word lists sometimes omit section labels. In that case
    # sentence word offsets are global, matching the whole-script storyboard.
    has_sections = any(word.get("section") for word in words or [])
    out: list[dict] = []
    seen: set[tuple[str, int, str | None]] = set()

    def add(kind: str, number: int | None, lane: str | None = None):
        if not isinstance(number, int) or not 1 <= number <= len(said):
            return
        key, local = starts[number - 1]
        candidates = by_section.get(key, []) if has_sections else list(range(len(words or [])))
        if not candidates or local >= len(candidates):
            return
        word_number = candidates[local]
        word = words[word_number]
        marker = (kind, number, lane)
        if marker in seen:
            return
        seen.add(marker)
        at = round(float(word.get("s", 0.0)), 3)
        text = said[number - 1]["text"]
        out.append(
            {
                "kind": kind,
                "role": kind,
                "sentence": number,
                "lane": lane,
                "word": word_number,
                "word_number": word_number + 1,
                "time_s": at,
                "seconds": at,
                "time": at,
                "at": at,
                "evidence": text,
                "sentence_text": text,
                "text": text,
            }
        )

    add("promise", promise.get("promise_sentence"))
    for item in promise.get("consequence_sentences", []):
        add("consequence", item.get("sentence"), item.get("lane"))
    add("payoff", promise.get("payoff_sentence"))
    roles = {item.get("n"): item.get("role") for item in stored.get("roles", [])}
    for number, role in roles.items():
        if role in ("twist", "rehook"):
            add(role, number)
    # Old rows have no explicit payoff checkpoint; retain their role evidence in
    # the render snapshot without changing the old stored JSON.
    if not any(item["kind"] == "payoff" for item in out):
        add("payoff", next((number for number, role in roles.items() if role == "payoff"), None))
    return sorted(out, key=lambda item: (item["time_s"], item["sentence"], item["kind"], item["lane"] or ""))


def listing(script_title: str, video: str, sections: list[dict], story_map: str = "") -> str:
    """What the model reads: the title, the kind of video and the numbered sentences under their headings."""
    lines = [
        f"<title>{script_title}</title>",
        f"<video>{'a YouTube Short' if video == 'short' else 'a long YouTube video'}</video>",
    ]
    if story_map:
        lines += ["<story_map>", story_map, "</story_map>"]
    lines.append("<script>")
    numbered = sentences(sections)
    for sec in sections:
        lines.append(f"[{sec.get('heading', '')}]")
        lines += [f"{s['n']}. {s['text']}" for s in numbered if s["section"] == sec.get("key", "")]
    lines.append("</script>")
    return "\n".join(lines)


# --- the model's labels -----------------------------------------------------------------


class Label(BaseModel):
    n: int = Field(description="The sentence's number")
    role: Literal[
        "hook",
        "context",
        "stakes",
        "question",
        "twist",
        "rehook",
        "fact",
        "consequence",
        "payoff",
        "callback",
        "filler",
    ]
    title_question: bool = Field(
        False, description="True only for a question sentence that asks the title's own question"
    )


class Labels(BaseModel):
    topic: str = Field(description="The topic the title is about, as one word or a short phrase")
    sentences: list[Label]
    loops: list["Loop"] = Field(default_factory=list)
    rehooks: list["Rehook"] = Field(default_factory=list)
    promise: "Promise" = Field(default_factory=lambda: Promise())


class Loop(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    question: str
    opener_sentence: int
    resolver_sentence: int | None = None
    main: bool = False


class Rehook(BaseModel):
    sentence: int
    delivers_new_information: bool = False
    opens_loop: str | None = None
    reason: str = ""


class Consequence(BaseModel):
    """A promised consequence, optionally tied to one lane of a parallel story."""

    sentence: int
    lane: str | None = None

    @field_validator("lane", mode="before")
    @classmethod
    def clean_lane(cls, value):
        return value.strip() if isinstance(value, str) and value.strip() else None


class Promise(BaseModel):
    verdict: Literal["paid", "partial", "missing"] = "paid"
    # `answer_sentence` is the legacy 2.6.0 name. New rows use explicit
    # promise/consequence/payoff checkpoints, while retaining it for readers.
    answer_sentence: int | None = None
    promise_sentence: int | None = None
    consequence_sentence: int | None = None
    consequence_sentences: list[Consequence] = Field(default_factory=list)
    payoff_sentence: int | None = None
    reason: str = ""

    @field_validator("consequence_sentences", mode="before")
    @classmethod
    def coerce_consequences(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        return [item if isinstance(item, dict) else {"sentence": item} for item in value]


PROMPT = prompts.Prompt("script_hold")


def _promise_data(
    value: dict | Promise | None, count: int, *, story_shape: str | None = None, lane_ids: set[str] | None = None
) -> tuple[dict, int, list[str]]:
    """Keep only usable promise checkpoints and explain every dropped reference.

    This is deliberately tolerant of old rows and model responses. A scalar
    consequence is promoted to the list form, and `answer_sentence` remains an
    alias for the old hold contract. Ordering is checked after sentence
    existence, so one bad reference cannot erase the other useful evidence.
    """
    raw = value.model_dump() if isinstance(value, Promise) else dict(value or {})
    dropped, warnings = 0, []

    def ref(name: str, fallback: int | None = None) -> int | None:
        nonlocal dropped
        number = raw.get(name, fallback)
        if number is None:
            return None
        if not isinstance(number, int) or not 1 <= number <= count:
            dropped += 1
            warnings.append(f"{name} {number!r} does not refer to a sentence")
            return None
        return number

    promise_sentence = ref("promise_sentence")
    explicit_payoff = raw.get("payoff_sentence")
    legacy_answer = raw.get("answer_sentence")
    if explicit_payoff is not None:
        payoff_sentence = ref("payoff_sentence")
        answer_sentence = ref("answer_sentence") if legacy_answer is not None else None
    else:
        # A legacy answer is the payoff checkpoint, but validate it only once.
        payoff_sentence = ref("answer_sentence") if legacy_answer is not None else None
        answer_sentence = payoff_sentence

    raw_consequences = raw.get("consequence_sentences")
    if raw_consequences is None:
        raw_consequences = []
    elif isinstance(raw_consequences, dict) and "sentence" not in raw_consequences and "n" not in raw_consequences:
        # Accept the compact lane -> sentence shape from early experiments.
        raw_consequences = [{"lane": lane, "sentence": number} for lane, number in raw_consequences.items()]
    if not isinstance(raw_consequences, list):
        raw_consequences = [raw_consequences]
    scalar = raw.get("consequence_sentence")
    if scalar is not None:
        raw_consequences = [{"sentence": scalar}, *raw_consequences]
    consequences: list[dict] = []
    seen: set[tuple[int, str | None]] = set()
    for item in raw_consequences:
        if isinstance(item, int):
            item = {"sentence": item}
        if not isinstance(item, dict):
            dropped += 1
            warnings.append(f"consequence {item!r} is not a sentence reference")
            continue
        number = item.get("sentence", item.get("n"))
        if not isinstance(number, int) or not 1 <= number <= count:
            dropped += 1
            warnings.append(f"consequence sentence {number!r} does not refer to a sentence")
            continue
        lane = item.get("lane")
        lane = lane.strip() if isinstance(lane, str) and lane.strip() else None
        if story_shape == "parallel" and lane_ids is not None:
            if lane not in lane_ids:
                dropped += 1
                warnings.append(f"consequence sentence {number} names unknown lane {lane!r}")
                continue
        elif story_shape and lane is not None:
            dropped += 1
            warnings.append(f"consequence sentence {number} names lane {lane!r} in a non-parallel story")
            continue
        key = (number, lane)
        if key in seen:
            dropped += 1
            warnings.append(f"duplicate consequence sentence {number} was dropped")
            continue
        seen.add(key)
        consequences.append({"sentence": number, "lane": lane})

    def ordered(number: int | None, name: str, *, after: int | None = None, before: int | None = None) -> int | None:
        nonlocal dropped
        if number is None:
            return None
        if after is not None and number <= after:
            dropped += 1
            warnings.append(f"{name} {number} is out of order (must follow sentence {after})")
            return None
        if before is not None and number >= before:
            dropped += 1
            warnings.append(f"{name} {number} is out of order (must precede sentence {before})")
            return None
        return number

    consequences = [
        item
        for item in consequences
        if ordered(item["sentence"], "consequence sentence", after=promise_sentence, before=payoff_sentence) is not None
    ]
    payoff_sentence = ordered(payoff_sentence, "payoff_sentence", after=promise_sentence)
    # If a legacy answer was invalid but the explicit payoff was valid, keep the
    # explicit value and do not let the alias affect current evidence.
    if answer_sentence is not None and answer_sentence != payoff_sentence:
        answer_sentence = payoff_sentence
    result = {
        "verdict": raw.get("verdict", "paid"),
        "promise_sentence": promise_sentence,
        "consequence_sentence": (consequences[0]["sentence"] if len(consequences) == 1 else None),
        "consequence_sentences": consequences,
        "payoff_sentence": payoff_sentence,
        "answer_sentence": answer_sentence,
        "reason": str(raw.get("reason", "") or ""),
    }
    return result, dropped, warnings


def _lane_ids(script: Script) -> set[str]:
    """The safe lane IDs from the current map, if this is a parallel script."""
    value = script.story_map or {}
    parallel = value.get("parallel") if isinstance(value, dict) else None
    return {
        str(item.get("id")) for item in (parallel or {}).get("lanes", []) if isinstance(item, dict) and item.get("id")
    }


def _consequence_status(promise: dict, story_shape: str | None, lane_ids: set[str]) -> str | None:
    """Return the independent lane-consequence verdict for parallel scripts."""
    if story_shape != "parallel":
        return None
    marked = {item.get("lane") for item in promise.get("consequence_sentences", []) if item.get("lane")}
    if lane_ids and marked >= lane_ids:
        return "paid"
    return "partial" if marked else "missing"


def label(script: Script) -> tuple[dict, int]:
    """Ask the quick tier for the roles. Returns what to store and how many labels were dropped."""
    count = len(sentences(script.sections))
    from . import story_map

    try:
        map_text = story_map.prompt_value(script)
    except RuntimeError:  # stale maps are not evidence; audit the title and narration instead
        map_text = ""
    answer = llm.ask(
        "hold", PROMPT, listing(script.title, script.video, script.sections, map_text), Labels, effort="low"
    )
    roles, seen, dropped = [], set(), 0
    for item in answer.sentences:
        if not 1 <= item.n <= count or item.n in seen:
            dropped += 1
            continue
        seen.add(item.n)
        roles.append({"n": item.n, "role": item.role, "title_question": item.title_question})
    roles.sort(key=lambda r: r["n"])
    loops = []
    for item in answer.loops:
        if not 1 <= item.opener_sentence <= count or (
            item.resolver_sentence is not None and not item.opener_sentence <= item.resolver_sentence <= count
        ):
            dropped += 1
            continue
        loops.append(item.model_dump())
    loop_ids = {item["id"] for item in loops}
    rehooks = []
    for item in answer.rehooks:
        if not 1 <= item.sentence <= count:
            dropped += 1
            continue
        value = item.model_dump()
        if value["opens_loop"] not in loop_ids:
            value["opens_loop"] = None
        rehooks.append(value)
    map_shape = (script.story_map or {}).get("story_shape") if isinstance(script.story_map, dict) else None
    shape = script.story_shape or map_shape
    promise, promise_dropped, promise_warnings = _promise_data(
        answer.promise, count, story_shape=shape, lane_ids=_lane_ids(script) if shape == "parallel" else None
    )
    consequence_status = _consequence_status(promise, shape, _lane_ids(script))
    if consequence_status:
        promise["consequence_verdict"] = consequence_status
    dropped += promise_dropped
    warnings = promise_warnings
    return {
        "hash": script_mod.text_hash(script.sections),
        "roles": roles,
        "topic": answer.topic.strip(),
        "loops": loops,
        "rehooks": rehooks,
        "promise": promise,
        "story_shape": shape,
        "lane_ids": sorted(_lane_ids(script)) if shape == "parallel" else [],
        "warnings": warnings,
        "dropped": dropped,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }, dropped


def timing(script: Script) -> tuple[int, Literal["estimated", "measured"]]:
    """Average pace for the advisory timing arithmetic. Exact word times elsewhere stay exact."""
    current_hash = script_mod.text_hash(script.sections)
    try:
        take = next((item for item in script.takes if item.chosen), None)
    except DetachedInstanceError:
        # Worker stage loaders deliberately return detached rows. Read only the chosen take in a short session.
        with session_scope() as db:
            take = db.scalar(select(VoiceTake).where(VoiceTake.script_id == script.id, VoiceTake.chosen.is_(True)))
    alignment = take.alignment if take is not None else None
    aligned_hash = alignment.get("script_hash") if alignment else None
    wpm = alignment.get("wpm") if alignment else None
    if (
        take is not None
        and take.script_hash == current_hash
        and (aligned_hash is None or aligned_hash == current_hash)
        and isinstance(wpm, int | float)
        and wpm > 0
    ):
        return round(wpm), "measured"
    return script_mod.WORDS_PER_MINUTE, "estimated"


def score_script(script: Script) -> dict | None:
    """Score one ORM script with measured chosen-take timing when it is safe to use."""
    if script.form == "micro_short":
        return micro_score(script)
    wpm, timing_kind = timing(script)
    return score(script.sections or [], script.hold, script.video, wpm=wpm, timing=timing_kind)


def micro_label(script: Script) -> dict:
    """Code-owned visual checkpoints for a silent cue sheet; no narration model or WPM."""
    cues = script_mod.micro_cues(script.sections)
    starts = (0.0, 0.18, 0.36, 0.58, 0.78)
    checkpoints = [
        {"kind": kind, "cue": cue["kind"], "at_fraction": starts[index], "evidence": cue["text"]}
        for index, (kind, cue) in enumerate(
            zip(("first_event", "rule", "reveal", "consequence", "payoff"), cues, strict=True)
        )
    ]
    return {
        "hash": script_mod.text_hash(script.sections),
        "mode": "designed_cues",
        "checkpoints": checkpoints,
        "score": 100,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def micro_score(script: Script) -> dict | None:
    """A compatible advisory result for the visual loop, never narration arithmetic."""
    stored = script.hold
    if not stored or stored.get("mode") != "designed_cues":
        return None
    current = stored.get("hash") == script_mod.text_hash(script.sections)
    if not current:
        return {
            "current": False,
            "checked_at": stored.get("checked_at"),
            "score": None,
            "previous_score": stored.get("score"),
        }
    duration = float(script.target_seconds)
    checkpoints = [
        {**item, "time_s": round(duration * float(item["at_fraction"]), 3)} for item in stored.get("checkpoints", [])
    ]
    return {
        "current": True,
        "checked_at": stored.get("checked_at"),
        "mode": "designed_cues",
        "score": stored.get("score", 100),
        "verdict": "visual loop ready",
        "topic": "silent visual loop",
        "lines": [
            {
                "points": 0,
                "label": "Five designed visual checkpoints",
                "detail": "Setup, hidden rule, two outcomes and the return to the opening are timed without speech.",
            }
        ],
        "visual_checkpoints": checkpoints,
        "stats": {
            "seconds": round(duration),
            "timing": "designed",
            "wpm": None,
            "first_twist": None,
            "longest_gap": None,
            "topic_at": None,
        },
        "sentences": [],
    }


def run(script_id: int, rep: Reporter) -> bool:
    """The job stage. An AI failure is logged and the job goes on: the page shows 'not checked'."""
    script = script_mod._load(script_id)
    if not script.sections:
        rep.warn("No text to check how it holds")
        return False
    if script.form == "micro_short":
        rep.info("Checking the five silent visual cues and replay loop")
        try:
            stored = micro_label(script)
        except ValueError as err:
            rep.warn(f"Visual loop check skipped: {err}")
            return False
        with session_scope() as db:
            row = db.get(Script, script_id)
            if script_mod.text_hash(row.sections) != stored["hash"]:
                rep.warn("The cue sheet was edited while it was being checked; run the check again")
            row.hold = stored
        rep.info("Visual loop check: setup, rule, both outcomes and loop return are present; no WPM measured")
        return True
    rep.info("Labelling each sentence to check how the script holds attention")
    try:
        stored, dropped = label(script)
    except AiUnavailable as err:
        rep.warn(f"Hold check skipped, the AI is unavailable: {err}")
        return False
    except Exception as err:  # an advisory check never fails the writing job it follows
        rep.warn(f"Hold check skipped, the AI's answer could not be used: {err}")
        return False
    script.hold = stored
    result = score_script(script)
    stored["score"] = result["score"] if result else None
    with session_scope() as db:
        row = db.get(Script, script_id)
        if script_mod.text_hash(row.sections) != stored["hash"]:
            rep.warn("The script was edited while it was being checked; run the check again")
        row.hold = stored
    if dropped:
        rep.warn(f"{dropped} labels pointed at sentences that do not exist and were dropped")
    for warning in stored.get("warnings", []):
        rep.warn(f"Hold checkpoint dropped: {warning}")
    if result:
        worst = sorted(result["lines"], key=lambda x: x["points"])[:2]
        tail = "; ".join(x["label"] for x in worst if x["points"] < 0)
        rep.info(f"Hold check: {result['score']}/100, {result['verdict']}" + (f" ({tail})" if tail else ""))
    return True


# --- the arithmetic ---------------------------------------------------------------------


def _forms(word: str) -> set[str]:
    """A word and its likely singulars, so "fridges", "fridge" and "fridge's" match."""
    word = re.sub(r"['’]s$", "", word.lower())
    forms = {word}
    if word.endswith("ies") and len(word) > 4:
        forms.add(word[:-3] + "y")
    if word.endswith("es") and len(word) > 4:
        forms.add(word[:-2])
    if word.endswith("s") and len(word) > 3:
        forms.add(word[:-1])
    return forms


def topic_words(topic: str) -> set[str]:
    return {
        f for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", topic) if w.lower() not in STOP and len(w) > 2 for f in _forms(w)
    }


def names_topic(text: str, words: set[str]) -> bool:
    return any(_forms(w) & words for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", text))


def score(
    sections: list[dict],
    stored: dict | None,
    video: str = "long",
    wpm: int = script_mod.WORDS_PER_MINUTE,
    timing: Literal["estimated", "measured"] = "estimated",
) -> dict | None:
    """The check from stored labels and the current text, 0 to 100, every deduction a line.

    None when there is no check; `{"current": False, ...}` when the text changed since the labels were made
    (the sentence numbers no longer line up, so no score is computed)."""
    if not stored:
        return None
    if stored.get("hash") != script_mod.text_hash(sections):
        return {
            "current": False,
            "checked_at": stored.get("checked_at"),
            "score": None,
            "previous_score": stored.get("score"),
        }
    said = sentences(sections)
    if not said:
        return None
    target = TARGETS["short" if video == "short" else "long"]
    roles = {r["n"]: r for r in stored.get("roles", []) if 1 <= r["n"] <= len(said)}
    start, total = [], 0  # words before each sentence
    for s in said:
        start.append(total)
        total += s["words"]
    secs = [w * 60 / wpm for w in start]
    seconds = total * 60 / wpm
    role = [roles.get(s["n"], {}).get("role") for s in said]
    lines: list[dict] = []

    def line(points: float, label: str, detail: str, cap: int = CAP) -> None:
        pts = min(cap, round(points))
        lines.append({"points": -pts if pts > 0 else 0, "label": label, "detail": detail})

    # Topic named
    words = topic_words(stored.get("topic", ""))
    at = next((i for i, s in enumerate(said) if role[i] in ("hook", "context") and names_topic(s["text"], words)), None)
    topic = stored.get("topic", "") or "the topic"
    if at is None:
        line(
            CAP,
            f"The topic ({topic}) is never named in a hook or context sentence",
            "Name what the video is about in the very first sentence.",
        )
    elif at == 0:
        line(0, "Topic named in sentence 1", f"The opening names {topic} straight away.")
    else:
        line(
            secs[at] * TOPIC_PER_S,
            f"Topic named only in sentence {at + 1}, after {secs[at]:.0f} s",
            f"{start[at]} words come before the first sentence that names {topic}; start on it.",
        )

    # First twist
    turns = [i for i, r in enumerate(role) if r in TURNS]
    twists = [i for i in turns if role[i] in ("twist", "rehook")]
    if not twists:
        line(
            CAP,
            "No twist or rehook anywhere",
            f"Add a turn the viewer did not see coming within the first {target['twist']} s.",
        )
    else:
        first = secs[twists[0]]
        over = first - target["twist"]
        line(
            max(0.0, over) * target["twist_per_s"],
            f"First twist at {first:.0f} s (sentence {twists[0] + 1})",
            f"Aim for {target['twist']} s or sooner." if over > 0 else f"Within {target['twist']} s.",
        )

    # Longest gap without a new question, twist or rehook (the payoff closes the last one)
    marks = turns + [i for i, r in enumerate(role) if r == "payoff"][:1]
    marks = sorted(set(marks))
    gaps = [(secs[b] - secs[a], a, b) for a, b in zip(marks, marks[1:], strict=False)]
    if gaps:
        gap, a, b = max(gaps)
        over_count = sum(1 for g, _, _ in gaps if g > target["gap"])
        over = gap - target["gap"]
        detail = (
            f"Between sentences {a + 1} and {b + 1}. Aim for a new question or twist every "
            f"{target['gap']} s or sooner" + (f"; {over_count} stretches run longer." if over_count > 1 else ".")
        )
        line(max(0.0, over) * target["gap_per_s"], f"Longest stretch without a new question: {gap:.0f} s", detail)
    elif len(marks) <= 1 and seconds > target["gap"]:
        line(
            CAP / 2,
            "Only one turn in the whole script",
            f"The viewer gets one reason to stay for {seconds:.0f} s; add a rehook every {target['gap']} s.",
        )

    # Filler
    labelled = [i for i, r in enumerate(role) if r == "filler"]
    phrases = [i for i, s in enumerate(said) if FILLER.search(s["text"])]
    filler = sorted(set(labelled) | set(phrases))
    if filler:
        shown = ", ".join(str(i + 1) for i in filler[:8])
        line(
            len(filler) * FILLER_POINTS,
            f"{len(filler)} filler sentence{'s' if len(filler) > 1 else ''}",
            f"Sentences {shown}: {len(labelled)} labelled filler, {len(phrases)} with a stock transition "
            '("moving on", "next up"…). Cut them or make them move the story.',
            FILLER_CAP,
        )

    # The title's question, asked more than once
    asked = [n for n, r in roles.items() if r.get("title_question")]
    if len(asked) > 1:
        line(
            (len(asked) - 1) * REPEAT_POINTS,
            f"The title's question is asked {len(asked)} times",
            f"Sentences {', '.join(str(n) for n in sorted(asked))}. Ask it once, then answer it.",
            REPEAT_CAP,
        )

    # Ending
    if role[-1] not in ("payoff", "callback"):
        line(
            ENDING_POINTS,
            f"Ends on {role[-1] or 'an unlabelled sentence'}, not the payoff",
            "Finish on the answer or on a callback to the opening.",
        )

    # Stakes early
    limit = total * target["stakes"]
    if not any(r == "stakes" and start[i] < limit for i, r in enumerate(role)):
        line(
            STAKES_POINTS,
            f"No stakes in the first {round(target['stakes'] * 100)}% of the words",
            "Say early why it matters to the viewer, or what it cost someone.",
        )

    # Promise integrity. These deductions share one cap so the new audit cannot overwhelm the established score.
    promise_used = 0

    def promise_line(points: int, label: str, detail: str) -> None:
        nonlocal promise_used
        charged = min(points, max(0, PROMISE_CAP - promise_used))
        promise_used += charged
        if charged < points:
            detail += f" The combined promise deductions are capped at {PROMISE_CAP} points."
        lines.append({"points": -charged, "label": label, "detail": detail})

    promise, promise_dropped, promise_warnings = _promise_data(
        stored.get("promise"),
        len(said),
        story_shape=stored.get("story_shape"),
        lane_ids=set(stored.get("lane_ids") or []) or None,
    )
    if promise.get("verdict") == "missing":
        promise_line(
            PROMISE_MISSING,
            "The title promise is not paid",
            promise.get("reason") or "Answer the title's main promise plainly before the ending.",
        )
    elif promise.get("verdict") == "partial":
        promise_line(
            PROMISE_PARTIAL,
            "The title promise is only partly paid",
            promise.get("reason") or "Finish the answer the title committed to give.",
        )

    # A parallel story has two independently promised outcomes. Never let a
    # model's overall `paid` verdict hide a missing lane consequence.
    consequence_status = _consequence_status(promise, stored.get("story_shape"), set(stored.get("lane_ids") or []))
    if consequence_status:
        lane_ids = set(stored.get("lane_ids") or [])
        marked = {item.get("lane") for item in promise.get("consequence_sentences", []) if item.get("lane")}
        promise["consequence_verdict"] = consequence_status
        if consequence_status == "missing":
            promise_line(
                PROMISE_MISSING,
                "No lane consequence is marked as paid",
                "Mark the consequence for each named lane before the payoff.",
            )
        elif consequence_status == "partial":
            promise_line(
                PROMISE_PARTIAL,
                "Only some lane consequences are marked as paid",
                f"Marked lanes: {', '.join(sorted(marked))}; expected: {', '.join(sorted(lane_ids))}.",
            )

    if promise_dropped or promise_warnings:
        lines.append(
            {
                "points": 0,
                "label": f"{promise_dropped} promise checkpoint references dropped",
                "detail": "; ".join(promise_warnings),
            }
        )

    loops = [
        item
        for item in stored.get("loops", [])
        if isinstance(item.get("opener_sentence"), int)
        and 1 <= item["opener_sentence"] <= len(said)
        and (
            item.get("resolver_sentence") is None
            or isinstance(item.get("resolver_sentence"), int)
            and item["opener_sentence"] <= item["resolver_sentence"] <= len(said)
        )
    ]
    unresolved = [item for item in loops if item.get("resolver_sentence") is None and not item.get("main")]
    if unresolved:
        raw = min(LOOP_CAP, len(unresolved) * LOOP_POINTS)
        ids = ", ".join(str(item.get("id", "loop")) for item in unresolved)
        promise_line(
            raw,
            f"{len(unresolved)} secondary loop{'s' if len(unresolved) != 1 else ''} left open",
            f"Unresolved: {ids}. Resolve each concrete question after it has delivered value.",
        )

    empty_rehooks = [
        item
        for item in stored.get("rehooks", [])
        if not item.get("delivers_new_information") and not item.get("opens_loop")
    ]
    if empty_rehooks:
        raw = min(EMPTY_REHOOK_CAP, len(empty_rehooks) * EMPTY_REHOOK_POINTS)
        numbered = ", ".join(str(item.get("sentence")) for item in empty_rehooks)
        promise_line(
            raw,
            f"{len(empty_rehooks)} empty rehook{'s' if len(empty_rehooks) != 1 else ''}",
            f"Sentences {numbered} deliver no new information and open no concrete question.",
        )

    opened: set[str] = set()
    maximum = 0
    for n in range(1, len(said) + 1):
        # A sentence may answer one question before opening the next; resolution happens first.
        for item in loops:
            if item.get("resolver_sentence") == n:
                opened.discard(str(item.get("id", "")))
        for item in loops:
            if item.get("opener_sentence") == n:
                opened.add(str(item.get("id", "")))
        maximum = max(maximum, len(opened))
    if maximum > 2:
        raw = min(CONCURRENT_CAP, (maximum - 2) * CONCURRENT_POINTS)
        promise_line(
            raw, f"{maximum} loops open at once", "Keep no more than two concrete questions open at the same time."
        )

    unlabelled = sum(1 for r in role if r is None)
    if unlabelled or stored.get("dropped"):
        line(
            0,
            f"{unlabelled} sentences without a label, {stored.get('dropped', 0)} labels dropped",
            "Labels that pointed at no sentence were dropped; unlabelled sentences count as plain facts.",
        )

    total_points = sum(x["points"] for x in lines)
    result = max(0, 100 + total_points)
    return {
        "current": True,
        "checked_at": stored.get("checked_at"),
        "score": result,
        "verdict": "holds well" if result >= 85 else "some slack" if result >= 65 else "likely to lose people",
        "topic": stored.get("topic", ""),
        "promise": promise,
        "loops": loops,
        "rehooks": stored.get("rehooks", []),
        "lines": lines,
        "stats": {
            "seconds": round(seconds),
            "first_twist": round(secs[twists[0]]) if twists else None,
            "longest_gap": round(max(gaps)[0]) if gaps else None,
            "topic_at": at + 1 if at is not None else None,
            "timing": timing,
            "wpm": round(wpm),
        },
        "sentences": [
            {
                "n": s["n"],
                "section": s["section"],
                "text": s["text"],
                "role": role[i],
                "at": round(secs[i]),
                "title_question": bool(roles.get(s["n"], {}).get("title_question")),
            }
            for i, s in enumerate(said)
        ],
    }
