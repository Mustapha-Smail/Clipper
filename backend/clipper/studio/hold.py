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

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.exc import DetachedInstanceError

from .. import llm, prompts
from ..ai_errors import AiUnavailable
from ..db import session_scope
from ..db.models import Script, VoiceTake
from ..jobs import Reporter
from . import plain
from . import script as script_mod

ROLES = ("hook", "context", "stakes", "question", "twist", "rehook", "fact", "payoff", "callback", "filler")
TURNS = ("question", "twist", "rehook")          # a new reason to keep watching

# Thresholds, one row per kind of video. Seconds are read at the narration speed (150 words a minute).
TARGETS = {
    #         first twist by, longest gap, points per second over (twist, gap), stakes within this share of words
    "short": {"twist": 10, "gap": 30, "twist_per_s": 2.0, "gap_per_s": 1.0, "stakes": 0.2},
    "long": {"twist": 30, "gap": 60, "twist_per_s": 0.5, "gap_per_s": 0.5, "stakes": 0.2},
}
CAP = 20                 # most points one line can take
FILLER_POINTS, FILLER_CAP = 3, 15
REPEAT_POINTS, REPEAT_CAP = 5, 10
ENDING_POINTS = 10
STAKES_POINTS = 10
TOPIC_PER_S = 3          # points per second of narration before the topic is named
PROMISE_MISSING, PROMISE_PARTIAL = 15, 8
LOOP_POINTS, LOOP_CAP = 4, 12
EMPTY_REHOOK_POINTS, EMPTY_REHOOK_CAP = 5, 15
CONCURRENT_POINTS, CONCURRENT_CAP = 4, 8
PROMISE_CAP = 30         # all new promise/loop/rehook deductions together

FILLER = re.compile(r"\b(?:moving on|let'?s talk about|next up|without further ado|let'?s dive in|"
                    r"let'?s get (?:started|into it)|in (?:this|today'?s) video|stay tuned|"
                    r"before we (?:begin|start))\b",
                    re.I)
STOP = set("""a an the of in on at to for and or but is are was were be been it its it's this that these those
why how what when where who which do does did you your yours can could would should will not no yes with from by
about as into than then so very really just ever never most more less much many some any all""".split())


# --- the sentences ----------------------------------------------------------------------

def sentences(sections: list[dict]) -> list[dict]:
    """Every sentence numbered from 1 across the sections, with its word count."""
    out = []
    for sec in sections:
        for text in plain.sentences(sec.get("text", "")):
            out.append({"n": len(out) + 1, "section": sec.get("key", ""), "text": text,
                        "words": len(re.findall(r"\S+", text))})
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


MARKED = ("twist", "rehook", "payoff")      # the sentences a storyboard shows as events


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


def listing(script_title: str, video: str, sections: list[dict], story_map: str = "") -> str:
    """What the model reads: the title, the kind of video and the numbered sentences under their headings."""
    lines = [f"<title>{script_title}</title>",
             f"<video>{'a YouTube Short' if video == 'short' else 'a long YouTube video'}</video>"]
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
    role: Literal["hook", "context", "stakes", "question", "twist", "rehook", "fact", "payoff", "callback",
                  "filler"]
    title_question: bool = Field(False, description="True only for a question sentence that asks the title's "
                                                    "own question")


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


class Promise(BaseModel):
    verdict: Literal["paid", "partial", "missing"] = "paid"
    answer_sentence: int | None = None
    reason: str = ""


PROMPT = prompts.Prompt("script_hold")


def label(script: Script) -> tuple[dict, int]:
    """Ask the quick tier for the roles. Returns what to store and how many labels were dropped."""
    count = len(sentences(script.sections))
    from . import story_map

    try:
        map_text = story_map.prompt_value(script)
    except RuntimeError:       # stale maps are not evidence; audit the title and narration instead
        map_text = ""
    answer = llm.ask("hold", PROMPT, listing(script.title, script.video, script.sections, map_text), Labels,
                     effort="low")
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
        if not 1 <= item.opener_sentence <= count or (item.resolver_sentence is not None and
                                                      not item.opener_sentence <= item.resolver_sentence <= count):
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
    promise = answer.promise.model_dump()
    if promise["answer_sentence"] is not None and not 1 <= promise["answer_sentence"] <= count:
        promise["answer_sentence"] = None
        dropped += 1
    return {"hash": script_mod.text_hash(script.sections), "roles": roles, "topic": answer.topic.strip(),
            "loops": loops, "rehooks": rehooks, "promise": promise,
            "dropped": dropped, "checked_at": datetime.now(UTC).isoformat(timespec="seconds")}, dropped


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
    if (take is not None and take.script_hash == current_hash and (aligned_hash is None or aligned_hash == current_hash)
            and isinstance(wpm, int | float) and wpm > 0):
        return round(wpm), "measured"
    return script_mod.WORDS_PER_MINUTE, "estimated"


def score_script(script: Script) -> dict | None:
    """Score one ORM script with measured chosen-take timing when it is safe to use."""
    wpm, timing_kind = timing(script)
    return score(script.sections or [], script.hold, script.video, wpm=wpm, timing=timing_kind)


def run(script_id: int, rep: Reporter) -> bool:
    """The job stage. An AI failure is logged and the job goes on: the page shows 'not checked'."""
    script = script_mod._load(script_id)
    if not script.sections:
        rep.warn("No text to check how it holds")
        return False
    rep.info("Labelling each sentence to check how the script holds attention")
    try:
        stored, dropped = label(script)
    except AiUnavailable as err:
        rep.warn(f"Hold check skipped, the AI is unavailable: {err}")
        return False
    except Exception as err:      # an advisory check never fails the writing job it follows
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
    return {f for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", topic) if w.lower() not in STOP and len(w) > 2
            for f in _forms(w)}


def names_topic(text: str, words: set[str]) -> bool:
    return any(_forms(w) & words for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", text))


def score(sections: list[dict], stored: dict | None, video: str = "long",
          wpm: int = script_mod.WORDS_PER_MINUTE,
          timing: Literal["estimated", "measured"] = "estimated") -> dict | None:
    """The check from stored labels and the current text, 0 to 100, every deduction a line.

    None when there is no check; `{"current": False, ...}` when the text changed since the labels were made
    (the sentence numbers no longer line up, so no score is computed)."""
    if not stored:
        return None
    if stored.get("hash") != script_mod.text_hash(sections):
        return {"current": False, "checked_at": stored.get("checked_at"), "score": None,
                "previous_score": stored.get("score")}
    said = sentences(sections)
    if not said:
        return None
    target = TARGETS["short" if video == "short" else "long"]
    roles = {r["n"]: r for r in stored.get("roles", []) if 1 <= r["n"] <= len(said)}
    start, total = [], 0             # words before each sentence
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
    at = next((i for i, s in enumerate(said) if role[i] in ("hook", "context") and names_topic(s["text"], words)),
              None)
    topic = stored.get("topic", "") or "the topic"
    if at is None:
        line(CAP, f"The topic ({topic}) is never named in a hook or context sentence",
             "Name what the video is about in the very first sentence.")
    elif at == 0:
        line(0, "Topic named in sentence 1", f"The opening names {topic} straight away.")
    else:
        line(secs[at] * TOPIC_PER_S, f"Topic named only in sentence {at + 1}, after {secs[at]:.0f} s",
             f"{start[at]} words come before the first sentence that names {topic}; start on it.")

    # First twist
    turns = [i for i, r in enumerate(role) if r in TURNS]
    twists = [i for i in turns if role[i] in ("twist", "rehook")]
    if not twists:
        line(CAP, "No twist or rehook anywhere", "Add a turn the viewer did not see coming "
             f"within the first {target['twist']} s.")
    else:
        first = secs[twists[0]]
        over = first - target["twist"]
        line(max(0.0, over) * target["twist_per_s"], f"First twist at {first:.0f} s (sentence {twists[0] + 1})",
             f"Aim for {target['twist']} s or sooner." if over > 0 else f"Within {target['twist']} s.")

    # Longest gap without a new question, twist or rehook (the payoff closes the last one)
    marks = turns + [i for i, r in enumerate(role) if r == "payoff"][:1]
    marks = sorted(set(marks))
    gaps = [(secs[b] - secs[a], a, b) for a, b in zip(marks, marks[1:], strict=False)]
    if gaps:
        gap, a, b = max(gaps)
        over_count = sum(1 for g, _, _ in gaps if g > target["gap"])
        over = gap - target["gap"]
        detail = (f"Between sentences {a + 1} and {b + 1}. Aim for a new question or twist every "
                  f"{target['gap']} s or sooner" + (f"; {over_count} stretches run longer." if over_count > 1 else "."))
        line(max(0.0, over) * target["gap_per_s"], f"Longest stretch without a new question: {gap:.0f} s", detail)
    elif len(marks) <= 1 and seconds > target["gap"]:
        line(CAP / 2, "Only one turn in the whole script",
             f"The viewer gets one reason to stay for {seconds:.0f} s; add a rehook every {target['gap']} s.")

    # Filler
    labelled = [i for i, r in enumerate(role) if r == "filler"]
    phrases = [i for i, s in enumerate(said) if FILLER.search(s["text"])]
    filler = sorted(set(labelled) | set(phrases))
    if filler:
        shown = ", ".join(str(i + 1) for i in filler[:8])
        line(len(filler) * FILLER_POINTS, f"{len(filler)} filler sentence{'s' if len(filler) > 1 else ''}",
             f"Sentences {shown}: {len(labelled)} labelled filler, {len(phrases)} with a stock transition "
             "(\"moving on\", \"next up\"…). Cut them or make them move the story.", FILLER_CAP)

    # The title's question, asked more than once
    asked = [n for n, r in roles.items() if r.get("title_question")]
    if len(asked) > 1:
        line((len(asked) - 1) * REPEAT_POINTS, f"The title's question is asked {len(asked)} times",
             f"Sentences {', '.join(str(n) for n in sorted(asked))}. Ask it once, then answer it.", REPEAT_CAP)

    # Ending
    if role[-1] not in ("payoff", "callback"):
        line(ENDING_POINTS, f"Ends on {role[-1] or 'an unlabelled sentence'}, not the payoff",
             "Finish on the answer or on a callback to the opening.")

    # Stakes early
    limit = total * target["stakes"]
    if not any(r == "stakes" and start[i] < limit for i, r in enumerate(role)):
        line(STAKES_POINTS, f"No stakes in the first {round(target['stakes'] * 100)}% of the words",
             "Say early why it matters to the viewer, or what it cost someone.")

    # Promise integrity. These deductions share one cap so the new audit cannot overwhelm the established score.
    promise_used = 0

    def promise_line(points: int, label: str, detail: str) -> None:
        nonlocal promise_used
        charged = min(points, max(0, PROMISE_CAP - promise_used))
        promise_used += charged
        if charged < points:
            detail += f" The combined promise deductions are capped at {PROMISE_CAP} points."
        lines.append({"points": -charged, "label": label, "detail": detail})

    promise = stored.get("promise") or {"verdict": "paid", "answer_sentence": None, "reason": ""}
    if promise.get("verdict") == "missing":
        promise_line(PROMISE_MISSING, "The title promise is not paid",
                     promise.get("reason") or "Answer the title's main promise plainly before the ending.")
    elif promise.get("verdict") == "partial":
        promise_line(PROMISE_PARTIAL, "The title promise is only partly paid",
                     promise.get("reason") or "Finish the answer the title committed to give.")

    loops = [item for item in stored.get("loops", [])
             if isinstance(item.get("opener_sentence"), int) and 1 <= item["opener_sentence"] <= len(said)
             and (item.get("resolver_sentence") is None or
                  isinstance(item.get("resolver_sentence"), int)
                  and item["opener_sentence"] <= item["resolver_sentence"] <= len(said))]
    unresolved = [item for item in loops if item.get("resolver_sentence") is None and not item.get("main")]
    if unresolved:
        raw = min(LOOP_CAP, len(unresolved) * LOOP_POINTS)
        ids = ", ".join(str(item.get("id", "loop")) for item in unresolved)
        promise_line(raw, f"{len(unresolved)} secondary loop{'s' if len(unresolved) != 1 else ''} left open",
                     f"Unresolved: {ids}. Resolve each concrete question after it has delivered value.")

    empty_rehooks = [item for item in stored.get("rehooks", [])
                     if not item.get("delivers_new_information") and not item.get("opens_loop")]
    if empty_rehooks:
        raw = min(EMPTY_REHOOK_CAP, len(empty_rehooks) * EMPTY_REHOOK_POINTS)
        numbered = ", ".join(str(item.get("sentence")) for item in empty_rehooks)
        promise_line(raw, f"{len(empty_rehooks)} empty rehook{'s' if len(empty_rehooks) != 1 else ''}",
                     f"Sentences {numbered} deliver no new information and open no concrete question.")

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
        promise_line(raw, f"{maximum} loops open at once",
                     "Keep no more than two concrete questions open at the same time.")

    unlabelled = sum(1 for r in role if r is None)
    if unlabelled or stored.get("dropped"):
        line(0, f"{unlabelled} sentences without a label, {stored.get('dropped', 0)} labels dropped",
             "Labels that pointed at no sentence were dropped; unlabelled sentences count as plain facts.")

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
        "stats": {"seconds": round(seconds), "first_twist": round(secs[twists[0]]) if twists else None,
                  "longest_gap": round(max(gaps)[0]) if gaps else None,
                  "topic_at": at + 1 if at is not None else None, "timing": timing, "wpm": round(wpm)},
        "sentences": [{"n": s["n"], "section": s["section"], "text": s["text"], "role": role[i],
                       "at": round(secs[i]), "title_question": bool(roles.get(s["n"], {}).get("title_question"))}
                      for i, s in enumerate(said)],
    }
