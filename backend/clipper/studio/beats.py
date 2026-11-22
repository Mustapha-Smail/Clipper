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

from . import art, composition, geo, ink

SLOTS = ("left", "center", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right")
SIZES = {"small": "small", "medium": "medium", "large": "large", "s": "small", "m": "medium", "l": "large",
         "full": "full"}     # full: a photo that fills the frame
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
CREATES = ("actor", "draw", "write", "flag", "map", "timeline", "photo", "data")
VERBS = (*CREATES, "do", "face", "move", "arrow", *MARKS, *MOTIONS, "erase", "look", "who")
SHOWN = ("flag", "map", "timeline", "photo", "data")  # source/archive objects, not prop-library drawings
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
    lane: str = ""                         # split-scene scope: a or b

    @property
    def id(self) -> str:
        """The name other lines use for what this line creates."""
        if self.ident:
            return self.ident
        if self.verb == "write":
            return _slug(self.text)
        if self.verb == "flag":
            return f"flag_{self.who}"
        if self.verb in ("map", "timeline", "photo"):
            return self.verb
        return self.who

    def render(self) -> str:
        """The canonical text of the line."""
        parts = [str(self.word), self.verb]
        if self.verb in ("flag", "map", "timeline", "who", "photo", "data"):
            if self.verb == "photo":
                parts.append(_quote(self.text))
            elif self.verb == "flag":
                parts.append(self.who)
            elif self.verb == "map":
                parts += self.extra
            elif self.verb == "data":
                parts.append(self.who)
            elif self.verb == "timeline":
                parts += [_quote(x) for x in self.extra]
            else:
                parts += [self.who, _quote(self.text), *self.extra]
            parts += [v for v in (self.slot, self.size, self.color) if v]
            if self.ident:
                parts += ["as", self.ident]
            if self.lane:
                parts += ["lane", self.lane]
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
        if self.lane:
            parts += ["lane", self.lane]
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
    if verb in ("flag", "map", "timeline", "who", "photo", "data"):
        return _parse_shown(line, rest)
    refs: list[str] = []
    # Lane is a semantic modifier, not a left/right slot. Strip it before the
    # existing token parser so every legacy line parses exactly as before.
    lane_rest = []
    i = 0
    while i < len(rest):
        if rest[i].lower() == "lane" and i + 1 < len(rest) and rest[i + 1].lower() in {"a", "b"}:
            line.lane = rest[i + 1].lower()
            i += 2
            continue
        lane_rest.append(rest[i])
        i += 1
    for token in lane_rest:
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
    i = 0
    while i < len(rest):
        token = rest[i]
        low = token.lower()
        if low == "lane" and i + 1 < len(rest) and rest[i + 1].lower() in {"a", "b"}:
            line.lane = rest[i + 1].lower()
            i += 2
            continue
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
        i += 1
    words = [w for w in words if w]
    if line.verb == "photo":
        if not quoted or not quoted[0].strip():
            return None
        line.text = " ".join(quoted[0].split()[:8])
    elif line.verb == "data":
        if len(words) != 1 or not NAME.match(words[0]):
            return None
        line.who = words[0]
    elif line.verb == "flag":
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
         lines: list[str] | None = None, cut: bool = False, place: str = "",
         composition_spec: dict | None = None, **kwargs) -> dict:
    """A shot. `cut`: the camera jumps to it (a hard cut) instead of gliding across the page.

    `place`: where it happens ("in kitchen"), drawn behind it in the cartoon look; a
    shot without one stays where the shot before it was.
    """
    # ``composition`` is the public keyword used by API/editor callers;
    # ``composition_spec`` keeps the local name explicit for old call sites.
    if composition_spec is None and "composition" in kwargs:
        composition_spec = kwargs["composition"]
    place = _name(place or "")
    result = {"start_word": start_word, "note": note, "camera": camera if camera in CAMERAS else "wide",
              "page": bool(page), "cut": bool(cut), "lines": list(lines or []),
              "place": place if NAME.match(place) else ""}
    if composition_spec is not None:
        result["composition"] = composition.normalize(composition_spec)
    return result


def place_at(shots: list[dict], index: int) -> str:
    """The place of shot `index`: its own, or the last one named before it ("" if none yet)."""
    for sh in reversed(shots[:index + 1]):
        if sh.get("place"):
            return sh["place"]
    return ""


def place_names(shots: list[dict]) -> list[str]:
    """Every place a storyboard names, in order of appearance."""
    out: list[str] = []
    for sh in shots:
        if sh.get("place") and sh["place"] not in out:
            out.append(sh["place"])
    return out


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
            place = words[words.index("in") + 1] if "in" in words[:-1] else ""
            divider = "vs" if "vs" in words else "line" if "line" in words else "none"
            spec = composition.normalize({"kind": "split", "divider": divider}) if "split" in words else None
            shots.append(shot(word, note, camera, "page" in words, cut="cut" in words, place=place,
                              composition_spec=spec))
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


def clean(shots: list[dict], word_count: int, data_cards: set[str] | None = None) -> tuple[list[dict], list[str]]:
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
                            list(raw.get("lines") or []), raw.get("cut", False), str(raw.get("place") or ""),
                            raw.get("composition")))
    if ordered:
        ordered[0]["start_word"] = 0

    previous: dict[str, Line] = {}     # still on stage at the end of the shot before
    previous_aliases: dict[tuple[str, str], str] = {}
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
        spec = composition.normalize(sh.get("composition"))
        if "composition" in sh or spec["kind"] != "single":
            sh["composition"] = spec
        else:
            sh.pop("composition", None)

        live: dict[str, Line] = {}       # canonical id -> the line that created it
        order: list[str] = []
        kept: list[Line] = []
        carried = dict(previous)
        aliases = dict(previous_aliases)  # (lane, authored id) -> canonical, globally unique id
        for line in parsed:
            if line.lane and spec["kind"] != "split":
                dropped.append(f"{line.render()}  (lane {line.lane} needs a split scene)")
                continue
            if spec["kind"] == "split" and not line.lane and line.verb != "who" and not (
                    line.verb in {"look", "erase"} and line.who in {"all", "wide"}):
                dropped.append(f"{line.render()}  (split-scene beats need lane a or lane b)")
                continue
            if line.verb == "who":
                personas.add(line.who)
                kept.append(line)
                continue
            if line.verb in CREATES:
                if line.verb == "data" and data_cards is not None and line.who not in data_cards:
                    dropped.append(f"{line.render()}  (data card '{line.who}' is unknown or is not source-backed)")
                    continue
                authored_id = line.id
                current_id = aliases.get((line.lane, authored_id), authored_id)
                if line.verb == "actor":
                    if line.who not in art.CHARACTERS and line.who not in personas:
                        line.who = "person"
                    if current_id in live and live[current_id].verb == "actor" \
                            and live[current_id].lane == line.lane:
                        # the same actor again: that is a change of pose or place, not a second actor
                        if line.pose or line.expression:
                            kept.append(Line(word=line.word, verb="do", who=current_id, pose=line.pose,
                                             expression=line.expression, lane=line.lane))
                        if line.slot and line.slot != live[current_id].slot:
                            kept.append(Line(word=line.word, verb="move", who=current_id, slot=line.slot,
                                             lane=line.lane))
                        continue
                    carried.pop(current_id, None)
                elif line.verb == "draw":
                    line.who = _name(line.who)
                    if not NAME.match(line.who):
                        dropped.append(f"{line.render()}  (not a prop name)")
                        continue
                if line.id in live:          # globally unique ids keep every renderer cache lane-safe
                    n = 2
                    while f"{line.id}_{n}" in live:
                        n += 1
                    line.ident = f"{line.id}_{n}"
                aliases[(line.lane, authored_id)] = line.id
                crowd = [x for x in order if live[x].verb != "actor"]
                if len(live) >= MAX_LIVE and crowd:
                    oldest = crowd[0]
                    kept.append(Line(word=line.word, verb="erase", who=oldest, lane=live[oldest].lane))
                    order.remove(oldest)
                    live.pop(oldest)
                live[line.id] = line
                order.append(line.id)
                kept.append(line)
                continue
            if line.verb == "erase" and line.who == "all":
                kept.append(line)
                live = {k: v for k, v in live.items()
                        if v.verb == "actor" or (line.lane and v.lane != line.lane)}
                order = [x for x in order if x in live]
                continue
            if line.verb == "look" and line.who in ("all", "wide"):
                line.who = "all"
                kept.append(line)
                continue
            # References stay inside their lane. When two lanes author the same
            # id, the second create was canonicalised above and all later beats
            # are rewritten to that stable id before the runtime sees them.
            if line.who and line.who not in SLOTS:
                line.who = aliases.get((line.lane, line.who), line.who)
            if line.target and line.target not in SLOTS:
                line.target = aliases.get((line.lane, line.target), line.target)
            refs = [line.who] + ([line.target] if line.target and line.verb in ("arrow", "do", "move") else [])
            why = ""
            for ref in refs:
                if ref in SLOTS:
                    continue
                if ref in live and (spec["kind"] != "split" or live[ref].lane == line.lane):
                    continue
                if ref in carried and (spec["kind"] != "split" or carried[ref].lane == line.lane):
                    # from the shot before: it comes along
                    live[ref] = carried.pop(ref)
                    order.append(ref)
                    continue
                if (ref in art.CHARACTERS or ref in personas) and ref not in live:
                    # a cast member used before being brought on: bring them on, rather than lose the line
                    taken = {x.slot for x in live.values()}
                    slot = next((s for s in ("left", "right", "center") if s not in taken), "left")
                    actor = Line(word=line.word, verb="actor", who=ref, slot=slot, lane=line.lane)
                    kept.append(actor)
                    live[ref] = actor
                    aliases[(line.lane, ref)] = ref
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
        previous_aliases = {key: value for key, value in aliases.items() if value in live}
        sh["lines"] = [x.render() for x in kept]
    return ordered, dropped


def parsed(sh: dict) -> list[Line]:
    """A cleaned shot's lines, read back."""
    return [line for line in (parse_line(x) for x in sh["lines"]) if line is not None]


def prop_names(shots: list[dict]) -> set[str]:
    return {line.who for sh in shots for line in parsed(sh) if line.verb == "draw"}


def photo_queries(shots: list[dict]) -> list[str]:
    """The archive searches a storyboard uses, in order of appearance."""
    out: list[str] = []
    for sh in shots:
        for line in parsed(sh):
            if line.verb == "photo" and line.text not in out:
                out.append(line.text)
    return out


def to_text(shots: list[dict]) -> str:
    """The whole storyboard as text, the way the planner writes it."""
    out = []
    for sh in shots:
        options = [sh["camera"]] if sh["camera"] != "wide" else []
        spec = composition.normalize(sh.get("composition"))
        if spec["kind"] == "split":
            options.append("split")
            if spec["divider"] != "none":
                options.append(spec["divider"])
        if sh.get("page"):
            options.append("page")
        if sh.get("cut"):
            options.append("cut")
        if sh.get("place"):
            options += ["in", sh["place"]]
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
