"""The film style: a documentary told on one evidence wall that the camera travels across.

Inspired by the cinematic-film skill (github.com/Moh4696/cinematic-skill, MIT):
one continuous camera over one designed world, one hero per narration line, and
every hit (a print landing, a stamp, a highlighter swipe, a number rolling up)
on the word the narrator says. The model never writes coordinates or code: a
storyboard in this style is shots of beat lines, like the ink style, and code
lays the wall out and moves the camera (`wall.py`).

Each shot is a cluster of things pinned on the wall. Lines:

    0 big "EASTER" bar as title                 big type (bar: on a yellow block)
    5 count "35" "DAYS IT CAN MOVE"             a number rolling up, with a caption
    12 photo "full moon" label "MARCH 21"       an archive photo (Wikimedia Commons), pinned
    14 flag ro                                  a flag, pinned
    19 card "THE RULE" "line" "line" as rule    an index card, typed (up to 4 lines)
    20 doc "THE TIMES" "9 OCTOBER 1903" "the words, quoted" as nyt   a typeset page (a recreation)
    22 map us fr as m                           a map print of those countries
    23 pin fr "PARIS" on m                      a pin with a label on a map
    24 route us fr on m                         a route drawn between two countries of a map
    26 stamp "FAILED" on rule                   a stamp slammed onto something
    27 highlight rule "full moon"               a highlighter swipe (a phrase of a card or a page, or big type)
    28 circle rule / underline / cross          pen marks
    30 string moon rule                         red string between two pins
    32 look rule                                the camera goes back to something
    34 wide                                     the camera pulls back to the cluster

The number is the narration word on which it happens (never a timestamp). A
plan as text opens each shot with `N shot "what it shows" [cut]`.
"""

import re
from dataclasses import dataclass, field

from . import composition

TOKEN = re.compile(r'"[^"]*"|\S+')
NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
CREATES = ("photo", "flag", "big", "count", "card", "doc", "map")
PINNED = ("photo", "flag", "card", "doc", "map")       # things with a pin, that strings tie to
MARKS = ("stamp", "highlight", "circle", "underline", "cross")
VERBS = (*CREATES, *MARKS, "pin", "route", "string", "look", "wide")
COLOURS = ("red", "yellow", "white")
MAX_TEXT = 40                 # characters of big type, a stamp, a card line, a label
MAX_QUOTE = 220               # a document's quoted sentence
MAX_CARD_LINES = 4
MAX_MAP = 6


@dataclass
class Line:
    word: int
    verb: str
    text: str = ""
    lines: list[str] = field(default_factory=list)   # card lines; a page's date line and sentence; map countries
    caption: str = ""                                 # a counter's caption, a pin's label
    label: str = ""                                   # a print's name bar
    target: str = ""
    target2: str = ""
    ident: str = ""
    bar: bool = False
    colour: str = ""
    lane: str = ""

    @property
    def id(self) -> str:
        if self.ident:
            return self.ident
        if self.verb == "flag":
            return f"flag_{self.text}"
        if self.verb == "map":
            return "map"
        return _slug(self.text) or self.verb

    def render(self) -> str:
        """The canonical text of the line."""
        q = _quote
        parts = [str(self.word), self.verb]
        v = self.verb
        if v in ("photo", "big", "stamp"):
            parts.append(q(self.text))
            if self.bar and v == "big":
                parts.append("bar")
        elif v == "flag":
            parts.append(self.text)
        elif v == "count":
            parts.append(q(self.text))
            if self.caption:
                parts.append(q(self.caption))
        elif v in ("card", "doc"):
            parts += [q(self.text), *(q(x) for x in self.lines)]
        elif v == "map":
            parts += self.lines
        elif v == "pin":
            parts += [self.text, q(self.caption)] if self.caption else [self.text]
        elif v == "route":
            parts += self.lines
        if v in ("stamp", "pin", "route") and self.target:
            parts += ["on", self.target]
        elif v in ("highlight", "circle", "underline", "cross", "look"):
            parts.append(self.target)
            if v == "highlight" and self.text:
                parts.append(q(self.text))
        elif v == "string":
            parts += [self.target, self.target2]
        if self.label:
            parts += ["label", q(self.label)]
        if self.colour:
            parts.append(self.colour)
        if self.ident and v in CREATES:
            parts += ["as", self.ident]
        if self.lane:
            parts += ["lane", self.lane]
        return " ".join(parts)


def _quote(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:3]
    slug = "_".join(words)
    if slug and not slug[0].isalpha():
        slug = "n_" + slug
    return slug[:40]


def _country(token: str) -> str | None:
    from . import geo

    return geo.code(token)


def parse_line(raw: str) -> Line | None:
    """One beat line, or None when it cannot be understood."""
    tokens = TOKEN.findall(raw.strip())
    if len(tokens) < 2 or not tokens[0].isdigit() or tokens[1] not in VERBS:
        return None
    line = Line(word=int(tokens[0]), verb=tokens[1])
    quoted: list[str] = []
    bare: list[str] = []
    rest = tokens[2:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        nxt = rest[i + 1] if i + 1 < len(rest) else None
        if tok == "as" and nxt and NAME.match(nxt):
            line.ident = nxt
            i += 2
            continue
        if tok.lower() == "lane" and nxt and nxt.lower() in {"a", "b"}:
            line.lane = nxt.lower()
            i += 2
            continue
        if tok == "on" and nxt and NAME.match(nxt):
            line.target = nxt
            i += 2
            continue
        if tok == "label" and nxt and nxt.startswith('"'):
            line.label = nxt.strip('"').strip()[:MAX_TEXT]
            i += 2
            continue
        if tok == "bar":
            line.bar = True
        elif tok in COLOURS:
            line.colour = tok
        elif tok.startswith('"'):
            quoted.append(tok.strip('"').strip())
        else:
            bare.append(tok.lower())
        i += 1
    short = [q[:MAX_TEXT] for q in quoted]
    v = line.verb
    if v in ("photo", "big", "stamp", "count", "card", "doc"):
        if not quoted or not quoted[0]:
            return None
        line.text = short[0]
        if v == "photo":
            line.text = quoted[0][:120]
        if v == "count":
            if not re.search(r"\d", line.text):
                return None
            line.caption = short[1] if len(short) > 1 else ""
        if v == "card":
            line.lines = [x for x in short[1:] if x][:MAX_CARD_LINES]
        if v == "doc":
            if len(quoted) < 2:
                return None
            sentence = quoted[-1][:MAX_QUOTE]
            line.lines = ([short[1]] if len(quoted) > 2 and short[1] else []) + [sentence]
        if v == "stamp" and not line.target and bare and NAME.match(bare[0]):
            line.target = bare[0]
    elif v == "flag":
        country = _country(bare[0]) if bare else None
        if country is None:
            return None
        line.text = country
    elif v in ("map", "route"):
        codes = []
        for b in bare:
            c = _country(b)
            if c and c not in codes:
                codes.append(c)
        if not codes or (v == "route" and len(codes) < 2):
            return None
        line.lines = codes[:2] if v == "route" else codes[:MAX_MAP]
    elif v == "pin":
        country = _country(bare[0]) if bare else None
        if country is None:
            return None
        line.text = country
        line.caption = short[0] if short else ""
    elif v in ("highlight", "circle", "underline", "cross", "look"):
        if not bare or not NAME.match(bare[0]):
            return None
        line.target = bare[0]
        if v == "highlight" and quoted:
            line.text = short[0]
    elif v == "string":
        if len(bare) < 2 or not all(NAME.match(b) for b in bare[:2]):
            return None
        line.target, line.target2 = bare[0], bare[1]
    return line


def parsed(shot: dict) -> list[Line]:
    return [line for line in (parse_line(x) for x in shot.get("lines", [])) if line is not None]


def clean(shots: list[dict], word_count: int) -> tuple[list[dict], list[str]]:
    """Make any plan safe to render: lines in order, words in range, references to things that exist.

    Returns (shots, the lines that were dropped)."""
    dropped: list[str] = []
    known: dict[str, tuple[str, str]] = {}  # canonical id -> (verb, lane)
    aliases: dict[tuple[str, str], str] = {}
    out: list[dict] = []
    last_word = -1
    top = max(0, word_count - 1)
    for i, shot in enumerate(shots):
        start = 0 if i == 0 else max(0, min(int(shot.get("start_word", 0) or 0), top))
        if i and start <= last_word:
            start = min(top, last_word + 1)
        spec = composition.normalize(shot.get("composition"))
        lines: list[tuple[int, str]] = []
        for text in (x for x in shot.get("lines", []) if isinstance(x, str)):
            line = parse_line(text)
            if line is None:
                if text.strip():
                    dropped.append(text.strip()[:80])
                continue
            line.word = max(start, min(line.word, top))
            if line.lane and spec["kind"] != "split":
                dropped.append(f"{line.render()}  (lane {line.lane} needs a split scene)")
                continue
            if spec["kind"] == "split" and not line.lane and line.verb != "wide":
                dropped.append(f"{line.render()}  (split-scene beats need lane a or lane b)")
                continue
            authored_id = line.id
            if line.verb in CREATES and line.id in known:
                n = 2
                while f"{line.id}_{n}" in known:
                    n += 1
                line.ident = f"{line.id}_{n}"
            if line.verb in CREATES:
                aliases[(line.lane, authored_id)] = line.id
            if line.target:
                line.target = aliases.get((line.lane, line.target), line.target)
            if line.target2:
                line.target2 = aliases.get((line.lane, line.target2), line.target2)
            refs = [r for r in (line.target, line.target2) if r]
            ok = all(r in known and (spec["kind"] != "split" or known[r][1] == line.lane) for r in refs)
            if line.verb == "stamp" and not refs:
                ok = any(lane == line.lane for _verb, lane in known.values())
            if line.verb in ("pin", "route"):
                maps = [k for k, (verb, lane) in known.items() if verb == "map" and lane == line.lane]
                if line.target:
                    ok = known.get(line.target) == ("map", line.lane)
                else:
                    ok = bool(maps)
            if not ok:
                dropped.append(text.strip()[:80])
                continue
            if line.verb in CREATES:
                known[line.id] = (line.verb, line.lane)
            lines.append((line.word, line.render()))
        lines.sort(key=lambda x: x[0])
        last_word = start
        item = {"start_word": start, "note": str(shot.get("note", ""))[:200],
                "lines": [x[1] for x in lines], "cut": bool(shot.get("cut", False))}
        if shot.get("composition") is not None or spec["kind"] != "single":
            item["composition"] = spec
        out.append(item)
    return out, dropped


SHOT = re.compile(r'^(\d+)\s+shot\s+"([^"]*)"(.*)$')


def parse_plan(text: str, word_count: int, offset: int = 0) -> list[dict]:
    """A plan written as text (shot lines, then beat lines) -> shots. Word numbers are shifted by `offset`."""
    shots: list[dict] = []
    for raw in text.splitlines():
        raw = raw.strip().strip("`")
        if not raw or not raw[0].isdigit():
            continue
        m = SHOT.match(raw)
        if m:
            options = m.group(3).lower().split()
            divider = "vs" if "vs" in options else "line" if "line" in options else "none"
            spec = composition.normalize({"kind": "split", "divider": divider}) if "split" in options else None
            item = {"start_word": int(m.group(1)) + offset, "note": m.group(2),
                    "lines": [], "cut": "cut" in options}
            if spec:
                item["composition"] = spec
            shots.append(item)
            continue
        number, _, rest = raw.partition(" ")
        if not shots:
            shots.append({"start_word": offset, "note": "", "lines": [], "cut": False})
        shots[-1]["lines"].append(f"{int(number) + offset} {rest}")
    return [s for s in shots if s["lines"] or s["note"]] if word_count else []


def to_text(shots: list[dict]) -> str:
    out = []
    for shot in shots:
        options = ["cut"] if shot.get("cut") else []
        spec = composition.normalize(shot.get("composition"))
        if spec["kind"] == "split":
            options.append("split")
            if spec["divider"] != "none":
                options.append(spec["divider"])
        suffix = " " + " ".join(options) if options else ""
        out.append(f'{shot["start_word"]} shot {_quote(shot.get("note", ""))}{suffix}')
        out += shot.get("lines", [])
    return "\n".join(out)


def photo_queries(shots: list[dict]) -> list[str]:
    """The archive searches a film storyboard uses, in order of appearance."""
    out: list[str] = []
    for shot in shots:
        for line in parsed(shot):
            if line.verb == "photo" and line.text not in out:
                out.append(line.text)
    return out


def photo_searches(style: str, shots: list[dict] | None) -> list[str]:
    """The archive searches of a storyboard of any style."""
    if not shots:
        return []
    if style == "film":
        return photo_queries(shots)
    if style == "ink":
        from . import beats

        return beats.photo_queries(shots)
    return []


def recreations(shots: list[dict]) -> list[str]:
    """The headers of the typeset pages: recreations, which the publish advice mentions."""
    return [line.text for shot in shots for line in parsed(shot) if line.verb == "doc"]
