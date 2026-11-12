"""The beat language of the ink style: one short line per thing that happens.

A storyboard in the ink style is a list of shots. Each shot is a spot on one
long page that the camera travels across, and holds lines like:

    12 actor narrator left deadpan talk
    15 draw microwave center large "1947"
    18 do narrator push microwave
    21 write "2.45 GHz" top big warm as freq
    23 circle freq bad
    26 arrow microwave narrator

The number is the narration word on which it happens (never a timestamp). A
line costs the planner about a tenth of the tokens of the same thing in JSON,
which is most of what a storyboard used to cost, and it is easy to edit by
hand. `clean()` makes any plan or edit safe to render: it drops lines it cannot
understand and says how many.
"""

import re
from dataclasses import dataclass, field

from . import art, geo, ink

SLOTS = ("left", "center", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right")
SIZES = {"small": "small", "medium": "medium", "large": "large", "s": "small", "m": "medium", "l": "large"}
COLORS = ("warm", "bad", "good", "gold", "green", "ink")
EXPRESSIONS = art.EXPRESSIONS
STILL_POSES = ("stand", "point", "hold", "push", "pull", "carry", "drop", "shrug", "think", "facepalm", "cheer", "wave",
               "surprised", "sad", "crossed", "hips", "present", "scared")
# recorded moves (motion capture): played once, then back to the pose before
ACTIONS = ("jump", "kick", "throw", "pickup", "stumble", "laugh", "dance", "cartwheel", "jacks", "signal")
POSES = STILL_POSES + ACTIONS
GAITS = ("walk", "run", "march", "zombie")             # how a `move` gets there
TARGETED = {"point", "hold", "push", "pull", "carry"}  # poses that reach for something
CAMERAS = ("wide", "push", "drift", "close")
MARKS = ("circle", "underline", "cross", "tick")
MOTIONS = ("shake", "bounce", "pulse", "spin", "grow", "shrink")
CREATES = ("actor", "draw", "write", "flag", "map", "timeline")
VERBS = (*CREATES, "do", "face", "move", "arrow", *MARKS, *MOTIONS, "erase", "look", "who")
SHOWN = ("flag", "map", "timeline")          # things drawn from data, not from the prop library
MAX_LIVE = 6                  # things on stage at once; older ones are erased to make room
MAX_TEXT_WORDS = 6
NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
TOKEN = re.compile(r'"[^"]*"|\S+')


@dataclass
class Line:
    word: int
    verb: str
    who: str = ""            # actor name, prop name, or the target of an action
    target: str = ""         # second reference: what an actor reaches for, where an arrow points
    slot: str = ""
    size: str = ""
    pose: str = ""
    expression: str = ""
    color: str = ""
    text: str = ""
    ident: str = ""          # `as <id>`
    talk: bool = False
    big: bool = False
    extra: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        """The name other lines use for what this line creates."""
        if self.ident:
            return self.ident
        if self.verb == "write":
            return _slug(self.text)
        if self.verb == "flag":
            return f"flag_{self.who}"
        if self.verb in ("map", "timeline"):
            return self.verb
        return self.who

    def render(self) -> str:
        """The canonical text of the line."""
        parts = [str(self.word), self.verb]
        if self.verb in ("flag", "map", "timeline", "who"):
            if self.verb == "flag":
                parts.append(self.who)
            elif self.verb == "map":
                parts += self.extra
            elif self.verb == "timeline":
                parts += [_quote(x) for x in self.extra]
            else:
                parts += [self.who, _quote(self.text), *self.extra]
            parts += [v for v in (self.slot, self.size, self.color) if v]
            if self.ident:
                parts += ["as", self.ident]
            return " ".join(parts)
        if self.verb == "write":
            parts.append(_quote(self.text))
        elif self.who:
            parts.append(self.who)
        if self.pose:                     # "do narrator push box" reads better than "do narrator box push"
            parts.append(self.pose)
        if self.target:
            parts.append(self.target)
        for value in (self.slot, self.expression, self.size, self.color):
            if value:
                parts.append(value)
        if self.verb == "draw" and self.text:
            parts.append(_quote(self.text))
        if self.big:
            parts.append("big")
        if self.talk:
            parts.append("talk")
        if self.ident:
            parts += ["as", self.ident]
        return " ".join(parts)


def _quote(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "text"


def _name(token: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", token.lower()).strip("_")[:40]


def parse_line(raw: str) -> Line | None:
    """One line of the language, or None when it is not understood."""
    raw = raw.strip().replace("“", '"').replace("”", '"').replace("’", "'")
    raw = re.sub(r"^\[(\d+)\]", r"\1", raw)          # "[12] draw ..." works too
    tokens = TOKEN.findall(raw)
    if len(tokens) < 2 or not tokens[0].isdigit():
        return None
    verb = tokens[1].lower()
    if verb not in VERBS:
        return None
    line = Line(word=int(tokens[0]), verb=verb)
    rest = tokens[2:]
    if "as" in [t.lower() for t in rest]:
        k = [t.lower() for t in rest].index("as")
        if k + 1 < len(rest) and NAME.match(_name(rest[k + 1])):
            line.ident = _name(rest[k + 1])
        rest = rest[:k] + rest[k + 2:]
    if verb in ("flag", "map", "timeline", "who"):
        return _parse_shown(line, rest)
    refs: list[str] = []
    for token in rest:
        low = token.lower()
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            if line.text:
                refs.append(_slug(token[1:-1]))     # a second quoted thing is a reference
            else:
                line.text = token[1:-1].strip()
        elif low in SLOTS and not line.slot and verb not in ("arrow",):
            line.slot = low
        elif low in SIZES and not line.size:
            line.size = SIZES[low]
        elif low in POSES and verb in ("actor", "do") and not line.pose:
            line.pose = low
        elif low in GAITS and verb == "move" and not line.pose:
            line.pose = low
        elif low in EXPRESSIONS and verb in ("actor", "do", "face") and not line.expression:
            line.expression = low
        elif (low in POSES or low in EXPRESSIONS) and verb in ("actor", "do"):
            continue                       # a second pose or face in one line: the first one wins
        elif low in COLORS and not line.color:
            line.color = low
        elif low in ("big",):
            line.big = True
        elif low in ("talk", "talks", "talking"):
            line.talk = True
        elif low in CAMERAS and verb == "look":
            refs.append(low)
        else:
            refs.append(_name(token))
    refs = [r for r in refs if r]
    if verb == "look":
        refs = [r for r in refs if r not in ("close", "push", "drift")]
    if verb == "write":
        if not line.text:
            return None
        if refs:
            line.extra = refs
    elif verb == "arrow":
        if line.text:          # an arrow to a quoted text
            refs.append(_slug(line.text))
            line.text = ""
        if len(refs) < 2:
            # an arrow may point at a slot: those were not taken as slots above
            return None
        line.who, line.target = refs[0], refs[1]
    else:
        if verb == "draw" and line.text:
            line.text = " ".join(line.text.split()[:3])          # a label: three words at most
        if line.text and verb not in ("draw",):
            refs.insert(0, _slug(line.text))
            line.text = ""
        if not refs:
            if verb in ("erase", "look"):
                refs = ["all"]
            else:
                return None
        line.who = refs[0]
        if len(refs) > 1:
            line.target = refs[1]
    if line.verb == "write":
        line.text = " ".join(line.text.split()[:MAX_TEXT_WORDS])
    return line


def _parse_shown(line: Line, rest: list[str]) -> Line | None:
    """flag CODE, map CODE CODE..., timeline "a" "b"..., who ID "Name" TRAIT...: the order of the rest is free."""
    words: list[str] = []
    quoted: list[str] = []
    for token in rest:
        low = token.lower()
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            quoted.append(token[1:-1].strip())
        elif low in SLOTS and not line.slot:
            line.slot = low
        elif low in SIZES and not line.size:
            line.size = SIZES[low]
        elif low in COLORS and not line.color:
            line.color = low
        else:
            words.append(_name(token))
    words = [w for w in words if w]
    if line.verb == "flag":
        codes = [c for c in (geo.code(w) for w in words) if c]
        if not codes or codes[0] not in geo.flags():
            return None
        line.who = codes[0]
    elif line.verb == "map":
        codes = []
        for w in words:
            c = geo.code(w)
            if c and c in geo.countries() and c not in codes:
                codes.append(c)
        if not codes:
            return None
        line.who, line.extra = codes[0], codes[:6]
    elif line.verb == "timeline":
        labels = [" ".join(q.split()[:3]) for q in quoted if q][:7]
        if len(labels) < 2:
            return None
        line.extra = labels
    else:   # who
        if not words or not NAME.match(words[0]) or not quoted:
            return None
        line.who, line.text = words[0], quoted[0][:60]
        line.extra = [w for w in words[1:] if w in ink.TRAITS]
    return line


# --- shots --------------------------------------------------------------------------------

def shot(start_word: int, note: str = "", camera: str = "wide", page: bool = False,
         lines: list[str] | None = None, cut: bool = False) -> dict:
    """A shot. `cut`: the camera jumps to it (a hard cut) instead of gliding across the page."""
    return {"start_word": start_word, "note": note, "camera": camera if camera in CAMERAS else "wide",
            "page": bool(page), "cut": bool(cut), "lines": list(lines or [])}


SHOT = re.compile(r'^\[?(\d+)\]?\s+shot\b(.*)$', re.I)


def parse_plan(text: str, word_count: int, offset: int = 0, check: bool = True) -> list[dict]:
    """The planner's answer (shot lines and beat lines) as shots, word numbers shifted by `offset`.

    With `check`, the shots are cleaned too; leave it off when chapters are put
    together first, so an actor from an earlier chapter is still known.
    """
    shots: list[dict] = []
    for raw in text.splitlines():
        raw = raw.strip().strip("`").strip()
        if not raw or raw.startswith("#"):
            continue
        head = SHOT.match(raw.replace("“", '"').replace("”", '"'))
        if head:
            word = int(head.group(1)) + offset
            rest = head.group(2)
            note = (re.search(r'"([^"]*)"', rest) or [None, ""])[1]
            words = re.sub(r'"[^"]*"', " ", rest).lower().split()
            camera = next((w for w in words if w in CAMERAS), "wide")
            shots.append(shot(word, note, camera, "page" in words, cut="cut" in words))
            continue
        line = parse_line(raw)
        if line is None:
            continue
        line.word += offset
        if not shots:
            shots.append(shot(offset))
        shots[-1]["lines"].append(line.render())
    if not check:
        return shots
    return clean(shots, word_count)[0] if shots else []


def clean(shots: list[dict], word_count: int) -> tuple[list[dict], list[str]]:
    """Make shots safe to render. Returns them and the lines that were dropped, each with the reason.

    Shots are ordered by their first word and never share it; each line is
    re-read, kept in its shot's span of words, and kept only if what it refers
    to exists by then. Anything still on stage at the end of a shot can be
    used in the next one: it comes along with the camera; whatever the next
    shot does not mention stays behind. When too many things are on stage, the
    oldest prop or text is erased first.
    """
    dropped: list[str] = []
    last = word_count - 1
    ordered = []
    for raw in sorted(shots, key=lambda x: int(x.get("start_word", 0))):
        start = max(0, min(int(raw.get("start_word", 0)), last))
        if ordered and start <= ordered[-1]["start_word"]:
            # same first word as the shot before: its lines join that shot
            ordered[-1]["lines"] += list(raw.get("lines") or [])
            continue
        ordered.append(shot(start, str(raw.get("note", ""))[:200], raw.get("camera", "wide"), raw.get("page", False),
                            list(raw.get("lines") or []), raw.get("cut", False)))
    if ordered:
        ordered[0]["start_word"] = 0

    previous: dict[str, Line] = {}     # still on stage at the end of the shot before
    personas: set[str] = set()         # people defined with `who`, usable as actors from then on
    for i, sh in enumerate(ordered):
        end = ordered[i + 1]["start_word"] - 1 if i + 1 < len(ordered) else last
        parsed = []
        for raw in sh["lines"]:
            line = raw if isinstance(raw, Line) else parse_line(str(raw))
            if line is None:
                dropped.append(f"{raw}  (not understood)")
                continue
            line.word = max(sh["start_word"], min(line.word, end))
            parsed.append(line)
        parsed.sort(key=lambda x: x.word)

        live: dict[str, Line] = {}       # id -> the line that created it
        order: list[str] = []
        kept: list[Line] = []
        carried = dict(previous)
        for line in parsed:
            if line.verb == "who":
                personas.add(line.who)
                kept.append(line)
                continue
            if line.verb in CREATES:
                if line.verb == "actor":
                    if line.who not in art.CHARACTERS and line.who not in personas:
                        line.who = "person"
                    if line.id in live and live[line.id].verb == "actor":
                        # the same actor again: that is a change of pose or place, not a second actor
                        if line.pose or line.expression:
                            kept.append(Line(word=line.word, verb="do", who=line.id, pose=line.pose,
                                             expression=line.expression))
                        if line.slot and line.slot != live[line.id].slot:
                            kept.append(Line(word=line.word, verb="move", who=line.id, slot=line.slot))
                        continue
                    carried.pop(line.id, None)
                elif line.verb == "draw":
                    line.who = _name(line.who)
                    if not NAME.match(line.who):
                        dropped.append(f"{line.render()}  (not a prop name)")
                        continue
                if line.id in live:          # the same thing twice: give it its own id
                    n = 2
                    while f"{line.id}_{n}" in live:
                        n += 1
                    line.ident = f"{line.id}_{n}"
                crowd = [x for x in order if live[x].verb != "actor"]
                if len(live) >= MAX_LIVE and crowd:
                    oldest = crowd[0]
                    kept.append(Line(word=line.word, verb="erase", who=oldest))
                    order.remove(oldest)
                    live.pop(oldest)
                live[line.id] = line
                order.append(line.id)
                kept.append(line)
                continue
            if line.verb == "erase" and line.who == "all":
                kept.append(line)
                live = {k: v for k, v in live.items() if v.verb == "actor"}
                order = [x for x in order if x in live]
                continue
            if line.verb == "look" and line.who in ("all", "wide"):
                line.who = "all"
                kept.append(line)
                continue
            refs = [line.who] + ([line.target] if line.target and line.verb in ("arrow", "do", "move") else [])
            why = ""
            for ref in refs:
                if ref in live or ref in SLOTS:
                    continue
                if ref in carried:           # from the shot before: it comes along
                    live[ref] = carried.pop(ref)
                    order.append(ref)
                    continue
                if (ref in art.CHARACTERS or ref in personas) and ref not in live:
                    # a cast member used before being brought on: bring them on, rather than lose the line
                    taken = {x.slot for x in live.values()}
                    slot = next((s for s in ("left", "right", "center") if s not in taken), "left")
                    actor = Line(word=line.word, verb="actor", who=ref, slot=slot)
                    kept.append(actor)
                    live[ref] = actor
                    order.append(ref)
                    continue
                why = f"nothing called {ref} is on stage then"
                break
            if not why and line.verb in ("do", "face") and live[line.who].verb != "actor":
                why = f"{line.who} is not an actor"
            if not why and line.verb == "move" and not line.slot and not line.target:
                why = "move needs a place, or something to go to"
            if not why and line.verb == "do" and not (line.pose or line.expression):
                why = "do needs a pose or a face"
            if why:
                dropped.append(f"{line.render()}  ({why})")
                continue
            if line.verb == "erase":
                live.pop(line.who, None)
                if line.who in order:
                    order.remove(line.who)
            kept.append(line)
        previous = dict(live)
        sh["lines"] = [x.render() for x in kept]
    return ordered, dropped


def parsed(sh: dict) -> list[Line]:
    """A cleaned shot's lines, read back."""
    return [line for line in (parse_line(x) for x in sh["lines"]) if line is not None]


def prop_names(shots: list[dict]) -> set[str]:
    return {line.who for sh in shots for line in parsed(sh) if line.verb == "draw"}


def to_text(shots: list[dict]) -> str:
    """The whole storyboard as text, the way the planner writes it."""
    out = []
    for sh in shots:
        options = [sh["camera"]] if sh["camera"] != "wide" else []
        if sh.get("page"):
            options.append("page")
        if sh.get("cut"):
            options.append("cut")
        out.append(" ".join([str(sh["start_word"]), "shot", _quote(sh["note"]), *options]).strip())
        out += sh["lines"]
    return "\n".join(out)


# --- the slideshow score ----------------------------------------------------------------------

def score(shots: list[dict], word_time, duration: float) -> dict:
    """How lively the storyboard is, out of 100, with every deduction on its own line.

    Arithmetic only, so it can be explained: a video loses points where nothing
    changes for long, where a shot only puts things on the page without anything
    happening to them, where words do the work pictures should, and where shots
    repeat the same pattern.
    """
    lines: list[dict] = []
    changes = sorted({0.0, *[word_time(sh["start_word"]) for sh in shots],
                      *[word_time(x.word) for sh in shots for x in parsed(sh)]} | {duration})
    gaps = [(b - a, a) for a, b in zip(changes, changes[1:], strict=False) if b - a > 4.0]
    minutes = max(duration, 1.0) / 60
    if gaps:
        still = sum(g - 4.0 for g, _ in gaps) / minutes        # seconds of stillness past 4 s, per minute
        loss = min(30, round(still * 3))
        longest = max(gaps)
        if loss:
            lines.append({"label": "Still stretches", "points": -loss,
                          "detail": f"{len(gaps)} stretch{'es' if len(gaps) > 1 else ''} over 4 s with nothing new "
                                    f"on screen ({still:.1f} s too still per minute, 3 points each); the longest is "
                                    f"{longest[0]:.1f} s at {_clock(longest[1])}"})
    flat = 0
    idle = 0
    pattern_repeats = 0
    texts = elements = 0
    previous = None
    for i, sh in enumerate(shots):
        items = parsed(sh)
        end = word_time(shots[i + 1]["start_word"]) if i + 1 < len(shots) else duration
        length = end - (word_time(sh["start_word"]) if i else 0.0)
        verbs = [x.verb for x in items if x.verb != "who"]
        acting = [v for v in verbs if v not in CREATES]
        if items and not acting and length > 3:
            flat += 1
        actors = {x.id for x in items if x.verb == "actor"}
        busy = {x.who for x in items if x.verb in ("do", "face", "move", *MOTIONS)}
        if actors - busy and length > 5:
            idle += 1
        texts += sum(1 for v in verbs if v == "write")
        elements += sum(1 for v in verbs if v in CREATES)
        signature = tuple(verbs)
        if signature and signature == previous:
            pattern_repeats += 1
        previous = signature
    count = max(1, len(shots))
    if flat and round(flat / count * 60):
        lines.append({"label": "Nothing happens", "points": -min(18, round(flat / count * 60)),
                      "detail": f"{flat} shot{'s' if flat > 1 else ''} only put things on the page; nothing acts, "
                                "moves or gets marked"})
    if idle and round(idle / count * 50):
        lines.append({"label": "Idle characters", "points": -min(15, round(idle / count * 50)),
                      "detail": f"{idle} shot{'s' if idle > 1 else ''} with a character who just stands there"})
    if elements:
        share = texts / elements
        if share > 0.35:
            lines.append({"label": "Words over pictures", "points": -min(15, round((share - 0.35) * 50)),
                          "detail": f"{share:.0%} of what appears is writing"})
    if pattern_repeats and round(pattern_repeats / count * 40):
        lines.append({"label": "Repeated pattern", "points": -min(12, round(pattern_repeats / count * 40)),
                      "detail": f"{pattern_repeats} shot{'s' if pattern_repeats > 1 else ''} repeat the shot "
                                "before line for line"})
    pace = duration / max(1, len(changes) - 1)
    if pace > 3.5:
        lines.append({"label": "Slow pace", "points": -min(15, round((pace - 3.5) * 5)),
                      "detail": f"something new every {pace:.1f} s on average; 2 to 3.5 s keeps attention"})
    total = max(0, 100 + sum(x["points"] for x in lines))
    verdict = "lively" if total >= 80 else "some slides" if total >= 60 else "slideshow risk"
    return {"score": total, "verdict": verdict, "lines": lines, "pace": round(pace, 2),
            "shots": len(shots), "changes": len(changes) - 1}


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"
