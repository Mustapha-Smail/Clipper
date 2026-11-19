"""Conservative, deterministic continuity advice for a saved storyboard plan.

The renderer already keeps a known ID visually consistent. This check only
reports contradictions code can establish from IDs, definitions, settings and
the story map; it never asks a model and never stores a score.
"""

import re

from . import beats
from . import storyboard as storyboard_mod

STOP = {"a", "an", "and", "as", "at", "back", "by", "for", "from", "in", "into", "of", "on", "or",
        "the", "that", "this", "to", "when", "with"}
RAPID_SETTING_SECONDS = 8.0       # three changes inside this window is almost certainly visual churn
RETURN_SECONDS = 5.0              # an A -> B -> A trip shorter than this needs a visible story beat in B


def _normal(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _time(words: list[dict], index: int) -> float:
    return float(words[max(0, min(index, len(words) - 1))].get("s", 0)) if words else 0.0


def _person_lines(shots: list[dict]) -> tuple[list[tuple[int, beats.Line]], dict[str, list[tuple[int, beats.Line]]]]:
    ordered, definitions = [], {}
    for shot in shots:
        for line in beats.parsed(shot):
            position = len(ordered)
            ordered.append((position, line))
            if line.verb == "who":
                definitions.setdefault(line.who, []).append((position, line))
    return ordered, definitions


def _people(shots: list[dict]) -> list[str]:
    lines, definitions = _person_lines(shots)
    advice: list[str] = []
    by_name: dict[str, set[str]] = {}
    for person_id, rows in definitions.items():
        for _, line in rows:
            name = _normal(line.text)
            if len(name.split()) >= 2:
                by_name.setdefault(name, set()).add(person_id)
        first = rows[0][1]
        for _, line in rows[1:]:
            names_conflict = _normal(first.text) != _normal(line.text)
            traits_conflict = bool(first.extra and line.extra and set(first.extra) != set(line.extra))
            if names_conflict or traits_conflict:
                advice.append(f"Person ID '{person_id}' is redefined with conflicting name or traits; define it once.")
                break
    for name, ids in by_name.items():
        if len(ids) > 1:
            advice.append(f"'{name.title()}' is defined under multiple person IDs "
                          f"({', '.join(sorted(ids))}); reuse one ID.")

    first_definition = {person_id: rows[0][0] for person_id, rows in definitions.items()}
    reported: set[str] = set()
    for position, line in lines:
        refs = [line.who, line.target]
        for ref in refs:
            if ref in first_definition and position < first_definition[ref] and ref not in reported:
                advice.append(f"Person '{ref}' is referenced before its who definition.")
                reported.add(ref)
    return advice


def _objects(shots: list[dict]) -> list[str]:
    uses: dict[str, list[tuple[int, str]]] = {}
    for shot_index, shot in enumerate(shots):
        for line in beats.parsed(shot):
            if line.verb == "draw" and line.ident:
                uses.setdefault(line.who, []).append((shot_index, line.id))
    advice = []
    for name, rows in uses.items():
        ids = {ident for _, ident in rows}
        shots_used = {shot for shot, _ in rows}
        if len(ids) > 1 and len(shots_used) > 1 and not any(
                len({ident for index, ident in rows if index == shot}) > 1 for shot in shots_used):
            advice.append(f"Recurring object '{name}' changes ID ({', '.join(sorted(ids))}); reuse one ID "
                          "unless they are different objects.")
    return advice


def _settings(shots: list[dict], words: list[dict]) -> list[str]:
    if not shots or not any(shot.get("place") for shot in shots):
        return []
    places = []
    current = ""
    for shot in shots:
        current = shot.get("place") or current
        places.append(current)
    changes = [(i, _time(words, shots[i]["start_word"])) for i in range(1, len(shots))
               if places[i] and places[i] != places[i - 1]]
    advice = []
    for start in range(len(changes)):
        window = [item for item in changes[start:] if item[1] - changes[start][1] <= RAPID_SETTING_SECONDS]
        if len(window) >= 3:
            advice.append(f"Settings change {len(window)} times in {RAPID_SETTING_SECONDS:g} seconds; "
                          "check that each move serves the story.")
            break
    for i in range(2, len(shots)):
        if not places[i - 2] or places[i - 2] != places[i] or places[i - 1] == places[i]:
            continue
        away = shots[i - 1]
        span = _time(words, shots[i]["start_word"]) - _time(words, away["start_word"])
        substantive = bool(str(away.get("note", "")).strip()) or any(
            line.verb in ("draw", "write", "flag", "map", "timeline", "photo") for line in beats.parsed(away))
        if span <= RETURN_SECONDS and not substantive:
            advice.append(f"The setting switches {places[i - 2]} → {places[i - 1]} → {places[i - 2]} in "
                          f"{span:.1f} s without a visible story beat in between.")
            break
    return advice


def _callback(shots: list[dict], words: list[dict], sections: list[dict], hold: dict | None,
              story_map: dict | None) -> list[str]:
    callback = ((story_map or {}).get("ending") or {}).get("callback", "").strip()
    if not callback or not shots:
        return []
    marks, _ = storyboard_mod.turn_marks(words, sections, hold)
    payoff = min((word for word, role in marks.items() if role == "payoff"), default=None)
    if payoff is None:
        return []        # no reliable payoff shot: be conservative
    index = max(i for i, shot in enumerate(shots) if shot["start_word"] <= payoff)
    shot = shots[index]
    visible = _normal(" ".join([str(shot.get("note", "")), *shot.get("lines", [])]))
    wanted = {word for word in _normal(callback).split() if word not in STOP and len(word) > 2}
    if wanted and not wanted.intersection(visible.split()):
        return [f"The story-map callback ('{callback}') is not visible in the payoff shot."]
    return []


def check(style: str, shots: list[dict], words: list[dict] | None = None, sections: list[dict] | None = None,
          hold: dict | None = None, story_map: dict | None = None) -> list[str]:
    """Advisory lines from the current saved plan. Unsupported styles yield no guesses."""
    if style != "ink" or not shots:
        return []
    words, sections = words or [], sections or []
    return [*_people(shots), *_objects(shots), *_settings(shots, words),
            *_callback(shots, words, sections, hold, story_map)]
