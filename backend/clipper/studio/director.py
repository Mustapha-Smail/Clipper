"""The director pass: the AI looks at stills of the shots and rewrites the weak ones in beat lines.

Code still draws every frame. The planner writes blind: it never sees what its
lines become on screen. The director does: for each shot, three stills (early,
middle, late) are drawn by the renderer and laid out on contact sheets, a few
shots per sheet, and the writing tier looks at them with the shots' narration
and beat lines. It answers per shot with what a viewer would find weak and,
when beat lines can fix it, the shot written again in the beat language.

A rewrite is kept only if it is safe, the same way an edit by hand is:

- `beats.clean` / `film.clean` must leave every other shot exactly as it was
  (a rewrite that removes something a later shot uses is refused);
- its word numbers stay in the shot's span, and it adds no archive photo;
- in the ink style, the liveliness score (`beats.score`) may not fall by more
  than `SCORE_SLACK` points.

Every verdict, kept or refused, is in the log and in `storyboards.director`
with the reason, and the plan before the pass is kept there for one undo.
Stills show no motion: pacing and how a movement looks are not judged here.
"""

import base64
import hashlib
import io
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from .. import llm
from ..ai_errors import AiUnavailable
from ..db import session_scope
from ..db.models import Storyboard, StudioAsset, utcnow
from ..jobs import Reporter
from ..settings import Settings
from . import animate, beats, compliance, film
from . import places as places_mod
from . import storyboard as storyboard_mod

log = logging.getLogger("clipper.studio")

FRAMES = (0.2, 0.55, 0.92)            # where in each shot its stills are taken, as a share of its length
SHOTS_PER_SHEET = {"short": 4, "long": 6}
FRAME_WIDTH = {"short": 200, "long": 340}
LABEL_HEIGHT = 30
GAP = 6
SCORE_SLACK = 2                       # liveliness points a rewrite may cost (a fix sometimes removes a beat)
WORKERS = 3


class ShotNote(BaseModel):
    shot: int = Field(description="The shot's number, as labelled on the sheet")
    problems: list[str] = Field(default_factory=list,
                                description="What a viewer would find weak in this shot, one short sentence each; "
                                            "empty when it works")
    beats: str = Field("", description="Only for a shot with a problem its beat lines can fix: the whole shot "
                                       "written again (its shot line first, then its beat lines). Empty to keep it")


class Direction(BaseModel):
    shots: list[ShotNote] = Field(description="One note per shot on the sheet")
    new_props: list[storyboard_mod.PropRequest] = Field(
        default_factory=list, description="Every prop your rewrites draw that is not in the library")


DIRECT = """\
You are the director of a narrated explainer video for a faceless YouTube channel. A \
planner wrote its storyboard in a beat language, without seeing anything; code then drew \
it. You are the first to look at the result. For each shot you get its narration (word \
numbers in brackets), its beat lines, and on the contact sheet three stills of it, early, \
middle and late, from left to right, under a label with its number.

Look at each shot as a viewer would, and note what is weak:
- The picture does not show what the narration says then (the wrong object, a prop that \
reads as something else, a label the viewer cannot connect).
- The frame does not read: things overlap or cover a face, sit cut off at an edge, are too \
small to make out, crowd the stage, or the stage is nearly empty.
- Nothing happens: things are put on the page and just sit there; an actor only stands.
- It looks like the shot before it.
The stills show no motion: do not judge pacing, timing or how a movement looks.

Then rewrite only the shots whose beat lines can fix what you saw, and keep the rest. A \
good director changes a few shots, not all of them; most notes on a decent storyboard are \
empty. A rewrite is the whole shot again:
- its shot line first, with the same first word number; every line inside the shot's \
word range;
- keep every name listed as used later (the next shots rely on it), and keep the setting;
- no new archive photos; keep the facts to what the narration says;
- use the beat language and follow the planner's rules below. A prop that is not in the \
library goes in new_props with a description for the illustrator.

The planner's instructions, with the beat language:

{planner}
"""


@dataclass(frozen=True)
class Kit:
    """What the director needs of a storyboard style."""
    parse_plan: Callable
    parse_line: Callable
    clean: Callable
    to_text: Callable
    creates: tuple


KITS = {
    "ink": Kit(lambda text, n: beats.parse_plan(text, n, check=False), beats.parse_line, beats.clean, beats.to_text,
               beats.CREATES),
    "film": Kit(lambda text, n: film.parse_plan(text, n), film.parse_line, film.clean, film.to_text, film.CREATES),
}


def fingerprint(scenes: list) -> str:
    """Identifies a plan, so an undo only replaces the plan the pass left."""
    return hashlib.sha256(json.dumps(scenes, sort_keys=True).encode()).hexdigest()[:16]


def spans(shots: list[dict], word_count: int) -> list[tuple[int, int]]:
    """Each shot's first and last word."""
    return [(sh["start_word"], (shots[i + 1]["start_word"] - 1) if i + 1 < len(shots) else word_count - 1)
            for i, sh in enumerate(shots)]


def used_later(kit: Kit, shots: list[dict], index: int) -> list[str]:
    """Names made in this shot or before that the shots after it mention."""
    made: set[str] = set()
    for sh in shots[:index + 1]:
        for raw in sh["lines"]:
            line = kit.parse_line(raw)
            if line is not None and line.verb in kit.creates:
                made.add(line.id)
    later = {token.strip('"') for sh in shots[index + 1:] for raw in sh["lines"] for token in raw.split()[1:]}
    return sorted(made & later)


def score_of(style: str, shots: list[dict], words: list[dict], duration: float) -> int | None:
    if style != "ink" or not shots:
        return None
    return beats.score(shots, lambda i: words[max(0, min(i, len(words) - 1))]["s"], duration)["score"]


def replace_shot(kit: Kit, shots: list[dict], index: int, text: str, word_count: int
                 ) -> tuple[list[dict] | None, str, list[str]]:
    """The plan with shot `index` written again, cleaned; or None and why the rewrite cannot be used.

    `shots` must be clean already. Returns (plan or None, reason, lines of the rewrite that were dropped)."""
    first, last = spans(shots, word_count)[index]
    parsed = kit.parse_plan(text, word_count)
    lines = [raw for sh in parsed for raw in sh["lines"]]
    if not parsed or not lines:
        return None, "the rewrite had no lines the renderer understands", []
    head = parsed[0]
    kept = []
    for raw in lines:
        line = kit.parse_line(raw)
        if line is None:
            continue
        line.word = max(first, min(line.word, last))
        kept.append(line.render())
    new = {**shots[index], **{k: v for k, v in head.items() if k != "lines"}, "start_word": first, "lines": kept}
    old_photos = sum(1 for raw in shots[index]["lines"] if getattr(kit.parse_line(raw), "verb", "") == "photo")
    if storyboard_mod.limit_photos([new], old_photos, kit.parse_line):
        log.info("director rewrite added a photo; left out")
    candidate = [*shots[:index], new, *shots[index + 1:]]
    cleaned, dropped = kit.clean(candidate, word_count)
    if len(cleaned) != len(shots):
        return None, "it would have changed where shots start", dropped
    others = [j for j in range(len(shots)) if j != index and cleaned[j] != shots[j]]
    if others:
        return None, (f"it would have changed shot {others[0] + 1} (something it uses would be gone)"), dropped
    if not cleaned[index]["lines"]:
        return None, "nothing usable was left of it", dropped
    if cleaned[index] == shots[index]:
        return None, "it came back the same", dropped
    return cleaned, "", dropped


# --- the contact sheets --------------------------------------------------------------------------

def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:          # a Pillow without sized default fonts
        return ImageFont.load_default()


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def times(shots: list[dict], words: list[dict], duration: float) -> list[tuple[float, float]]:
    """When each shot starts and ends, in seconds."""
    starts = [0.0] + [words[min(sh["start_word"], len(words) - 1)]["s"] for sh in shots[1:]]
    return [(a, b) for a, b in zip(starts, [*starts[1:], duration], strict=True)]


def sheet(frames: list[tuple[int, str, list[bytes]]], fmt: str) -> bytes:
    """Rows of stills under labels: [(shot number, label, [png, png, png])] -> one PNG."""
    images = [[Image.open(io.BytesIO(png)).convert("RGB") for png in pngs] for _, _, pngs in frames]
    width = FRAME_WIDTH[fmt]
    height = max(img.height for row in images for img in row)
    row_height = LABEL_HEIGHT + height + GAP
    canvas = Image.new("RGB", (GAP + len(FRAMES) * (width + GAP), GAP + len(frames) * row_height), (40, 40, 44))
    draw = ImageDraw.Draw(canvas)
    font = _font(20)
    for r, ((number, label, _), row) in enumerate(zip(frames, images, strict=True)):
        top = GAP + r * row_height
        draw.text((GAP + 4, top + 4), f"Shot {number}  {label}", fill=(255, 220, 90), font=font)
        for c, img in enumerate(row):
            canvas.paste(img, (GAP + c * (width + GAP), top + LABEL_HEIGHT))
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def _shot_block(kit: Kit, shots: list[dict], index: int, words: list[dict], span: tuple[int, int],
                when: tuple[float, float]) -> str:
    first, last = span
    narration = " ".join(f"[{k}] {words[k]['w']}" for k in range(first, last + 1))
    later = used_later(kit, shots, index)
    return (f'<shot number="{index + 1}" words="{first}-{last}" time="{_clock(when[0])}-{_clock(when[1])}">\n'
            f"<narration>{narration}</narration>\n<beats>\n{kit.to_text([shots[index]])}\n</beats>\n"
            f"<used_later>{', '.join(later) or 'nothing'}</used_later>\n</shot>")


def planner_brief(style: str, fmt: str) -> str:
    if style == "ink":
        known = storyboard_mod.library()
        props = "\n".join(f"- {name}: {desc}" for name, desc in known.items()) or "(empty so far)"
        return storyboard_mod.ink_system(fmt, props)
    return storyboard_mod.FILM_PLAN.format(format_rules=storyboard_mod.FILM_FORMAT_RULES[fmt])


# --- the pass ----------------------------------------------------------------------------------

def run(storyboard_id: int, settings: Settings, rep: Reporter) -> None:
    """Look at every shot, rewrite the weak ones that can be fixed safely, and keep the old plan for an undo."""
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        style, fmt, title = board.style, board.format, board.title
        words, shots = list(board.words), [dict(sh) for sh in board.scenes or []]
        duration = board.end - board.start
        script_id = board.script_id
    if style not in KITS:
        rep.warn("The director works on ink and film storyboards only; this one is in the first (flat) style")
        return
    if not shots or not words:
        rep.warn("The storyboard has no shots to look at yet")
        return
    kit = KITS[style]
    shots, _ = kit.clean(shots, len(words))       # the stored plan, read back the way the guard reads it
    before = [dict(sh) for sh in shots]
    span = spans(shots, len(words))
    when = times(shots, words, duration)
    per = SHOTS_PER_SHEET[fmt]
    groups = [list(range(i, min(i + per, len(shots)))) for i in range(0, len(shots), per)]
    rep.info(f"Drawing stills of {len(shots)} shots on {len(groups)} contact sheet{'s' if len(groups) > 1 else ''}"
             " (three moments per shot)")

    sheets: list[bytes] = []
    for n, group in enumerate(groups):
        rows = []
        for i in group:
            a, b = when[i]
            pngs = [animate.still(storyboard_id, a + (b - a) * f, settings, FRAME_WIDTH[fmt]) for f in FRAMES]
            rows.append((i + 1, f"{_clock(a)}-{_clock(b)}", pngs))
        sheets.append(sheet(rows, fmt))
        rep.progress((n + 1) / len(groups) * 0.3, f"Drew {n + 1} of {len(groups)} contact sheets")

    system = DIRECT.format(planner=planner_brief(style, fmt))
    stop = {"why": ""}       # set when the AI is unavailable: later sheets are not asked

    def ask(n: int) -> Direction | None:
        if stop["why"]:
            return None
        text = "\n\n".join(_shot_block(kit, shots, i, words, span[i], when[i]) for i in groups[n])
        content = [{"type": "text", "text": f"<video_title>{title}</video_title>\n\n{text}\n\n"
                                            f"The contact sheet of shots {groups[n][0] + 1} to {groups[n][-1] + 1}:"},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.standard_b64encode(sheets[n]).decode()}}]
        try:
            return llm.ask("direct", system, content, Direction, effort="medium", cache=True)
        except AiUnavailable as err:
            stop["why"] = str(err)
            return None
        except Exception as err:  # noqa: BLE001 - one sheet's failure leaves its shots as they are
            rep.warn(f"Sheet {n + 1}: the AI's answer could not be used ({str(err)[:200]}); its shots stay as planned")
            return None

    # The first sheet alone writes the prompt cache; the others then read it in parallel.
    answers = [ask(0)]
    rep.progress(0.3 + 0.6 / len(groups), f"Looked at 1 of {len(groups)} sheets")
    if len(groups) > 1:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for answer in pool.map(ask, range(1, len(groups))):
                answers.append(answer)
                rep.progress(0.3 + 0.6 * len(answers) / len(groups),
                             f"Looked at {len(answers)} of {len(groups)} sheets")
    looked = sum(1 for a in answers if a is not None)
    if stop["why"]:
        rep.warn(f"The AI is unavailable ({stop['why']}); "
                 + (f"{len(groups) - looked} sheets were not looked at and their shots stay as planned"
                    if looked else "the plan is unchanged"))
    if not looked:
        if stop["why"]:
            raise RuntimeError(f"The director could not look at the shots: {stop['why']}")
        raise RuntimeError("The director could not use any of the AI's answers; the plan is unchanged")

    notes: dict[int, ShotNote] = {}
    descriptions: dict[str, str] = {}
    for n, answer in enumerate(answers):
        if answer is None:
            continue
        for note in answer.shots:
            if note.shot - 1 in groups[n]:           # a note about a shot that is not on this sheet is ignored
                notes[note.shot - 1] = note
        descriptions.update({p.name: p.description for p in answer.new_props})

    start_score = current_score = score_of(style, shots, words, duration)
    report: list[dict] = []
    for i in sorted(notes):
        note = notes[i]
        problems = [p.strip() for p in note.problems if p.strip()][:5]
        if not problems and not note.beats.strip():
            continue
        entry = {"shot": i + 1, "start_word": shots[i]["start_word"], "problems": problems,
                 "before": kit.to_text([shots[i]]), "after": "", "outcome": "noted", "why": "", "dropped": []}
        report.append(entry)
        if not note.beats.strip():
            entry["why"] = "no rewrite proposed"
            continue
        plan, why, dropped = replace_shot(kit, shots, i, note.beats, len(words))
        entry["dropped"] = dropped[:5]
        if plan is not None and current_score is not None:
            new_score = score_of(style, plan, words, duration)
            if new_score < current_score - SCORE_SLACK:
                plan, why = None, f"liveliness would fall from {current_score} to {new_score}"
        if plan is None:
            entry["outcome"], entry["why"] = "refused", why
            entry["after"] = note.beats.strip()[:2000]
            rep.info(f"Shot {i + 1}: {'; '.join(problems) or 'rewritten'}. Rewrite refused: {why}")
            continue
        shots = plan
        if current_score is not None:
            current_score = score_of(style, shots, words, duration)
        entry["outcome"], entry["after"] = "changed", kit.to_text([shots[i]])
        rep.info(f"Shot {i + 1}: {'; '.join(problems) or 'rewritten'}. Rewritten"
                 + (f" ({len(dropped)} of its lines dropped as unusable)" if dropped else ""))

    changed = [e for e in report if e["outcome"] == "changed"]
    refused = [e for e in report if e["outcome"] == "refused"]
    lines = compliance.lines_changed(before, shots) if changed else 0
    summary = {"at": utcnow().isoformat(), "shots": len(shots), "sheets": len(groups), "looked": looked,
               "changed": len(changed), "refused": len(refused), "lines": lines,
               "score_before": start_score, "score_after": current_score, "notes": report,
               "before": before if changed else None, "after_hash": fingerprint(shots) if changed else None,
               "skipped": stop["why"]}
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        if fingerprint(kit.clean([dict(sh) for sh in board.scenes or []], len(words))[0]) != fingerprint(before):
            raise RuntimeError("The plan was edited while the director was looking; nothing was changed. "
                               "Run the director again.")
        board.director = summary
        if changed:
            board.scenes = shots
            compliance.record(db, script_id, "director", "ai",
                              f"The AI director rewrote {len(changed)} shot{'s' if len(changed) > 1 else ''} "
                              f"after looking at stills ({lines} lines)", lines, storyboard_id)
            names = storyboard_mod.prop_names(style, shots)
            for name in names:
                if db.get(StudioAsset, name) is None:
                    db.add(StudioAsset(name=name, description=descriptions.get(name, name.replace("_", " "))))
    if changed and style == "ink":
        places_mod.register(beats.place_names(shots), {})
    score_line = (f"; liveliness {start_score} → {current_score}" if start_score is not None and changed else "")
    rep.info(f"The director looked at {looked} of {len(groups)} sheets: {len(report)} shots with notes, "
             f"{len(changed)} rewritten, {len(refused)} rewrites refused{score_line}")
    if changed:
        with session_scope() as db:
            todo = [n for n in storyboard_mod.prop_names(style, shots)
                    if (a := db.get(StudioAsset, n)) is not None and a.svg is None]
        if todo:
            storyboard_mod.draw_missing(storyboard_id, rep)
        rep.info("Render again to see the changes. The plan before the pass is kept: Undo puts it back.")


def undo(board: Storyboard) -> bool:
    """Put back the plan from before the last pass, if the plan is still the one the pass left."""
    report = board.director or {}
    if not report.get("before") or not report.get("after_hash"):
        return False
    kit = KITS.get(board.style)
    if kit is None or fingerprint(board.scenes or []) != report["after_hash"]:
        return False
    board.scenes = report["before"]
    board.director = {**report, "before": None, "after_hash": None, "undone_at": utcnow().isoformat()}
    return True


def undoable(board: Storyboard) -> bool:
    report = board.director or {}
    return bool(report.get("before") and report.get("after_hash")
                and fingerprint(board.scenes or []) == report["after_hash"])


def public(report: dict | None) -> dict | None:
    """The report without the saved plan, for the page."""
    if not report:
        return None
    return {k: v for k, v in report.items() if k not in ("before", "after_hash")}
