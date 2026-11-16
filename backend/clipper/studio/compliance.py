"""Before you publish: who made what, how close it is to your other videos, and two pieces of advice.

Everything here advises. Nothing approves, blocks or posts: the decisions in
YouTube Studio (made for kids, altered content) stay yours, and every number
comes with the lines that make it up, like the idea and liveliness scores.

  authorship   a log of each step (by you, by the AI, or by the app) and a summary
  similarity   arithmetic against your recent videos: same look, music, voice, props,
               title words, opening words; YouTube does not pay for mass-produced,
               repetitive uploads
  kids         readability (Flesch-Kincaid grade) and topic words, toward an
               answer to YouTube's "made for kids" question
  disclosure   whether YouTube's altered or synthetic content label applies
"""

import difflib
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Idea, Script, Storyboard, StudioEvent, VoiceTake, as_utc
from . import beats, film

BY = ("you", "ai", "app")
LOG_STARTED = datetime(2026, 11, 13, tzinfo=UTC)     # steps before this day were not recorded

# What each finished worker job adds to the log.
JOB_EVENTS = {
    "script": ("research", "ai", "Researched the topic with web sources and wrote the first draft"),
    "redraft": ("draft", "ai", "Wrote the script again from the research"),
    "factcheck": ("factcheck", "ai", "Checked the facts against the sources"),
    "spoken": ("spoken", "ai", "Rewrote the script to sound spoken"),
    "simpler": ("spoken", "ai", "Rewrote the script in plainer words for the audience"),
    "voice_ai": ("voice", "ai", "Generated an AI narration"),
    "voice_rec": ("voice", "you", "Recorded the narration in your own voice"),
    "storyboard": ("plan", "ai", "Planned the scenes"),
    "animate": ("render", "app", "Rendered the video"),
    "thumbnails": ("thumbnail", "ai", "Planned and drew thumbnails for the A/B test"),
}


def record(db: Session, script_id: int, kind: str, by: str, summary: str, amount: int = 0,
           storyboard_id: int | None = None) -> None:
    db.add(StudioEvent(script_id=script_id, storyboard_id=storyboard_id, kind=kind[:24], by=by,
                       summary=summary[:500], amount=amount))


def record_job(db: Session, kind: str, params: dict) -> None:
    """A worker job finished: note what it made."""
    known = JOB_EVENTS.get(kind)
    script_id = params.get("script_id")
    if not known or not script_id or db.get(Script, script_id) is None:
        return
    event, by, summary = known
    take = db.get(VoiceTake, params["take_id"]) if params.get("take_id") else None
    if take is not None and kind == "voice_ai":
        summary += f" ({take.source}, voice {take.voice})"
    record(db, script_id, event, by, summary, storyboard_id=params.get("storyboard_id"))


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def words_changed(before: str, after: str) -> int:
    """How many words were typed, replaced or removed between two texts."""
    a, b = _words(before), _words(after)
    changed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed


def lines_changed(before: list[dict], after: list[dict]) -> int:
    """Beat lines added, removed or changed between two versions of a storyboard."""
    text = film.to_text if any("camera" not in shot for shot in [*before, *after]) else beats.to_text
    a, b = text(before).splitlines(), text(after).splitlines()
    changed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "equal":
            changed += max(i2 - i1, j2 - j1)
    return changed


def sections_text(sections: list[dict]) -> str:
    return "\n".join(s.get("text", "") for s in sections or [])


# --- authorship ---------------------------------------------------------------------------------

def _events(db: Session, board: Storyboard) -> list[StudioEvent]:
    rows = db.scalars(select(StudioEvent).where(StudioEvent.script_id == board.script_id)
                      .order_by(StudioEvent.created_at, StudioEvent.id)).all()
    return [e for e in rows if e.storyboard_id in (None, board.id)]


def authorship(db: Session, board: Storyboard) -> dict:
    """The log for this video and a plain summary of what was yours."""
    script = board.script
    events = _events(db, board)
    idea = db.get(Idea, script.idea_id) if script.idea_id else None
    take = board.take
    lines: list[dict] = []

    def line(part: str, text: str, yours: bool) -> None:
        lines.append({"part": part, "text": text, "yours": yours})

    if idea is None:
        line("Idea", "No idea on file (the script was started directly)", False)
    elif idea.origin == "you":
        line("Idea", "Your own idea", True)
    else:
        line("Idea", "Suggested by the generator, chosen by you", False)

    edits = [e for e in events if e.kind == "script_edit" and e.by == "you"]
    words = sum(e.amount for e in edits)
    total = max(1, len(_words(sections_text(script.sections))))
    if edits:
        line("Script", f"Written with AI from research; you changed {words} words in {len(edits)} "
                       f"edit{'s' if len(edits) != 1 else ''} (about {min(100, round(100 * words / total))}% "
                       f"of {total})", words >= 0.1 * total)
    else:
        line("Script", "Written by the AI from research; not edited by you yet", False)
    asked = [e for e in events if e.kind == "rewrite"]
    if asked and edits:
        lines[-1]["text"] += ", counting AI rewrites you kept"
    if asked:
        line("Script", f"{len(asked)} section rewrite{'s' if len(asked) != 1 else ''} on your instructions", True)

    if take is None:
        line("Voice", "No narration chosen", False)
    elif take.source == "recording":
        line("Voice", "Your own recorded voice", True)
    else:
        line("Voice", f"AI voice ({take.voice}), chosen by you", False)

    plan_edits = [e for e in events if e.kind == "beats_edit" and e.by == "you"]
    if plan_edits:
        n = sum(e.amount for e in plan_edits)
        line("Visuals", f"Planned by the AI; you changed {n} beat line{'s' if n != 1 else ''} in "
                        f"{len(plan_edits)} edit{'s' if len(plan_edits) != 1 else ''}", n >= 5)
    elif board.style in ("ink", "film"):
        line("Visuals", "Planned by the AI; not edited by you yet", False)
    if any(e.kind == "look" for e in events) or board.look not in ("ink", "wall"):
        line("Visuals", f"Look: {board.look}, chosen by you", True)
    swaps = [e for e in events if e.kind == "photo"]
    if swaps:
        line("Visuals", f"You swapped {len(swaps)} archive picture{'s' if len(swaps) != 1 else ''}", True)

    music = [e for e in events if e.kind == "music"]
    if board.music_id:
        line("Music", "Chosen by you" if music and music[-1].by == "you" else "Suggested by the AI", bool(music))
    if board.youtube:
        own = [e for e in events if e.kind == "description_edit"]
        line("Description", "Drafted by the AI, edited by you" if own else "Drafted by the AI, not edited yet",
             bool(own))
    reviewed = next((e for e in reversed(events) if e.kind == "review"), None)
    if board.review != "pending":
        when = reviewed.created_at.strftime("%d %b %Y") if reviewed else ""
        line("Review", f"Watched and {board.review} by you {when}".strip(), True)
    else:
        line("Review", "Not reviewed yet", False)

    yours = sum(1 for x in lines if x["yours"])
    advice = []
    if as_utc(script.created_at) < LOG_STARTED:
        advice.append("This script was made before the log began (13 Nov 2026): edits from before then are "
                      "not counted here.")
    if not any(x["yours"] for x in lines if x["part"] in ("Script", "Voice")):
        advice.append("The words and the voice are all AI so far. Putting the script in your own words "
                      "(or recording it yourself) is the strongest sign of your own work.")
    if not plan_edits and board.style in ("ink", "film"):
        advice.append("Changing some scenes to your taste makes the video less like a template.")
    return {"lines": lines, "yours": yours, "parts": len(lines), "advice": advice,
            "events": [{"at": as_utc(e.created_at).isoformat(), "kind": e.kind, "by": e.by, "summary": e.summary,
                        "amount": e.amount} for e in events]}


def authorship_text(db: Session, board: Storyboard) -> str:
    """The log as plain text, to keep with the video (useful if the channel is ever reviewed)."""
    data = authorship(db, board)
    out = [f"Authorship log: {board.title}", f"Storyboard {board.id}, script {board.script_id}", ""]
    out += [f"{x['part']}: {x['text']}" for x in data["lines"]]
    out += ["", "Steps:"]
    for e in data["events"]:
        when = datetime.fromisoformat(e["at"]).strftime("%Y-%m-%d %H:%M")
        out.append(f"{when}  {e['by']:<3}  {e['summary']}")
    return "\n".join(out) + "\n"


# --- similarity to your recent videos --------------------------------------------------------------

RECENT = 12
STOP = set("a an and are as at be by for from how i in is it its of on or that the this to was what when "
           "where which who why will with you your".split())


def _title_words(title: str) -> set[str]:
    return {w for w in _words(title.split(" · ")[0]) if w not in STOP and not w.isdigit()}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _opening(board: Storyboard) -> list[str]:
    return [w["w"].lower().strip(".,!?;:\"'") for w in (board.words or [])[:15]]


def _props(board: Storyboard) -> set[str]:
    return beats.prop_names(board.scenes) if board.style == "ink" and board.scenes else set()


def compare(a: Storyboard, b: Storyboard) -> tuple[int, list[dict]]:
    """How alike two videos are, 0 to 100, with each point explained."""
    lines: list[dict] = []

    def add(points: int, text: str) -> None:
        if points:
            lines.append({"points": points, "text": text})

    if a.style == b.style and (a.style == "flat" or a.look == b.look):
        add(10, f"Same look ({a.style if a.style == 'flat' else a.look})")
    if a.music_id and a.music_id == b.music_id:
        add(20, "Same music track")
    ta, tb = a.take, b.take
    if ta and tb and ta.source != "recording" and (ta.source, ta.voice) == (tb.source, tb.voice):
        add(10, f"Same AI voice ({ta.voice})")
    j = _jaccard(_props(a), _props(b))
    add(round(25 * j), f"{round(100 * j)}% of the props are the same")
    j = _jaccard(_title_words(a.title), _title_words(b.title))
    add(round(20 * j), f"{round(100 * j)}% of the title words are the same")
    r = difflib.SequenceMatcher(None, _opening(a), _opening(b), autojunk=False).ratio() if a.words and b.words else 0
    add(round(15 * r), f"The first words are {round(100 * r)}% alike")
    return min(100, sum(x["points"] for x in lines)), lines


def similarity(db: Session, board: Storyboard) -> dict:
    """The closest of your recent videos (from other scripts), and how varied the recent ones are."""
    recent = db.scalars(select(Storyboard).where(Storyboard.script_id != board.script_id, Storyboard.id != board.id)
                        .order_by(Storyboard.created_at.desc(), Storyboard.id.desc()).limit(RECENT)).all()
    if not recent:
        return {"score": 0, "verdict": "first", "lines": [], "nearest": None, "compared": 0,
                "note": "Nothing to compare with yet: this is your first video from a script."}
    scored = [(compare(board, other), other) for other in recent]
    (score, lines), nearest = max(scored, key=lambda x: x[0][0])
    verdict = "close" if score >= 60 else "similar" if score >= 35 else "different"
    note = {"close": "Very close to a recent video. Change the look, the music or the voice, or rework the "
                     "scenes: YouTube does not pay for uploads that look mass-produced.",
            "similar": "Some likeness to a recent video. Fine now and then; vary the look or the music if it "
                       "keeps happening.",
            "different": "Different enough from your recent videos."}[verdict]
    looks = {b.style if b.style == "flat" else b.look for b in [board, *recent]}
    tracks = {b.music_id for b in [board, *recent] if b.music_id}
    return {"score": score, "verdict": verdict, "lines": lines, "compared": len(recent), "note": note,
            "nearest": {"id": nearest.id, "title": nearest.title, "script_id": nearest.script_id},
            "variety": f"Your last {len(recent) + 1} videos use {len(looks)} look{'s' if len(looks) != 1 else ''} "
                       f"and {len(tracks)} music track{'s' if len(tracks) != 1 else ''}."}


# --- made for kids ------------------------------------------------------------------------------

KID_WORDS = {"kid", "kids", "child", "children", "toddler", "toddlers", "preschool", "nursery", "toy", "toys",
             "abc", "alphabet", "colours", "colors", "counting", "bedtime", "lullaby", "cartoon", "puppy",
             "kitten", "dinosaur", "dinosaurs", "princess", "fairy", "playground", "classroom"}
ADULT_WORDS = {"war", "wars", "death", "deaths", "died", "kill", "killed", "killing", "murder", "violence",
               "weapon", "weapons", "bomb", "crime", "prison", "drug", "drugs", "alcohol", "tax", "taxes",
               "mortgage", "invest", "investing", "stocks", "salary", "career", "politics", "election",
               "government", "economy", "inflation", "debt", "loan", "disease", "pandemic", "genocide"}


def syllables(word: str) -> int:
    word = word.lower().strip("'")
    if len(word) <= 3:
        return 1
    word = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", word)
    word = re.sub(r"^y", "", word)
    return max(1, len(re.findall(r"[aeiouy]{1,2}", word)))


def grade(text: str) -> float:
    """Flesch-Kincaid grade level: about the school year a reader needs."""
    sentences = max(1, len(re.findall(r"[.!?]+(?:\s|$)", text)))
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return 0.0
    syl = sum(syllables(w) for w in words)
    return round(0.39 * len(words) / sentences + 11.8 * syl / len(words) - 15.59, 1)


def kids(board: Storyboard) -> dict:
    """Advice on YouTube's 'made for kids' setting. You answer it in YouTube Studio; this only helps you think."""
    script = board.script
    text = sections_text(script.sections)
    if board.section_key:
        text = next((s["text"] for s in script.sections if s["key"] == board.section_key), text)
    words = _words(text)
    lines: list[dict] = []
    g = grade(text)
    # narration is kept simple on purpose (script.SPOKEN), so simple words alone say little
    if g < 3:
        lines.append({"points": 2, "text": f"Very simple language (reading grade {g})"})
    elif g < 5:
        lines.append({"points": 1, "text": f"Simple language (reading grade {g})"})
    elif g > 8:
        lines.append({"points": -1, "text": f"Grown-up language (reading grade {g})"})
    else:
        lines.append({"points": 0, "text": f"Everyday language (reading grade {g})"})
    found = sorted({w for w in words if w in KID_WORDS})
    if found:
        lines.append({"points": min(6, 2 * len(found)), "text": "Words aimed at children: " + ", ".join(found[:6])})
    grown = sorted({w for w in words if w in ADULT_WORDS})
    if grown:
        lines.append({"points": -min(6, 2 * len(grown)), "text": "Grown-up topics: " + ", ".join(grown[:6])})
    lines.append({"points": 1, "text": "Animated characters (one of the signs YouTube lists, not enough alone)"})
    score = sum(x["points"] for x in lines)
    if score >= 5:
        verdict, advice = "kids", "It may well count as made for kids. If children are the audience you mean, " \
                                  "say Yes: comments and personalised ads are then turned off."
    elif score >= 3:
        verdict, advice = "unsure", "Borderline. Ask yourself who you made it for: if mainly children, say Yes."
    else:
        verdict, advice = "general", "Looks made for a general audience: 'No, it's not made for kids' fits."
    return {"score": score, "verdict": verdict, "lines": lines, "advice": advice,
            "note": "You choose this in YouTube Studio for every upload; the law (COPPA) holds the channel to it."}


# --- altered or synthetic content -----------------------------------------------------------------

def disclosure(db: Session, board: Storyboard) -> dict:
    """Whether YouTube's 'altered or synthetic content' label applies, with the reason for each part."""
    lines: list[dict] = [{"needs": False, "text": "Drawn animation is clearly not real footage, so it needs no label."}]
    take = board.take
    if take is None or take.source == "recording":
        lines.append({"needs": False, "text": "The narration is your own voice."})
    elif take.source == "kokoro":
        lines.append({"needs": False, "text": f"The AI voice ({take.voice}) is a synthetic narrator that imitates "
                                              "no real person."})
    elif take.source == "chatterbox":
        lines.append({"needs": False, "text": f"The AI voice (Chatterbox, {take.voice}) is a synthetic narrator that "
                                              "imitates no real person; its audio carries an inaudible AI watermark."})
    else:
        lines.append({"needs": None, "text": f"ElevenLabs voice ({take.voice}): if it is a clone of a real "
                                             "person's voice, tick the label (and have their permission)."})
    people = []
    if board.style == "ink":
        for sh in board.scenes or []:
            for line in beats.parsed(sh):
                if line.verb == "who" and line.text not in people:
                    people.append(line.text)
    if people:
        lines.append({"needs": False, "text": "Real people are shown as drawn characters (" + ", ".join(people[:5])
                      + "), which no one would take for footage. Keep what they 'say' to what they really said."})
    if board.style == "film" and film.recreations(board.scenes or []):
        lines.append({"needs": False, "text": "Typeset pages (" + ", ".join(film.recreations(board.scenes)[:3])
                      + ") are drawn recreations, marked RECREATION on the page. Quote only words the document "
                        "really says."})
    if film.photo_searches(board.style, board.scenes or []):
        lines.append({"needs": False, "text": "Archive photos are real and only framed and toned, which needs no "
                                              "label. Never use one to show an event it is not from."})
    needs = any(x["needs"] for x in lines)
    unsure = any(x["needs"] is None for x in lines)
    verdict = "label" if needs else "check" if unsure else "none"
    advice = {"label": "Tick 'Altered or synthetic content' when you upload.",
              "check": "Probably no label, but check the point marked below.",
              "none": "No label needed: answer No to 'Altered or synthetic content'."}[verdict]
    return {"verdict": verdict, "advice": advice, "lines": lines}


# --- everything together ----------------------------------------------------------------------------

def before_publish(db: Session, board: Storyboard) -> dict:
    script = board.script
    from . import script as script_mod

    checked = script.checked_hash == script_mod.text_hash(script.sections) if script.checked_hash else False
    flagged = [c for c in (script.claims or []) if c.get("verdict") in ("contradicted", "unclear", "not_in_notes")]
    sim = similarity(db, board)
    who = authorship(db, board)
    checklist = [
        {"done": board.review == "approved", "text": "You watched the whole video and approved it"},
        {"done": checked, "text": "The facts were checked on the current script"
         + (f" ({len(flagged)} claim{'s' if len(flagged) != 1 else ''} to look at)" if checked and flagged else "")},
        {"done": sim["verdict"] != "close", "text": "Not a near copy of a recent video"},
        {"done": who["yours"] >= 3, "text": f"Your own input is on record ({who['yours']} of {who['parts']} parts)"},
        {"done": bool(board.youtube), "text": "The YouTube description is written, with its sources and credits"},
        {"done": None, "text": "Answer 'made for kids' and 'altered content' in YouTube Studio"},
    ]
    return {"checklist": checklist, "authorship": who, "similarity": sim, "kids": kids(board),
            "disclosure": disclosure(db, board)}
