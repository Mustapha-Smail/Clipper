"""The system prompts of every AI step, as text files you can read and change.

Each prompt is a Markdown file in `clipper/prompts/` (the default, tracked in
git) with a short header saying which step uses it and which values the code
fills in. Your edits from the Prompts page are saved under
`<data folder>/prompts/` and win over the default until you reset them; a
default that changes in a new version does not overwrite your edit.

Inside a prompt:
  {name}           a value the code fills in (listed in the header as `variables`)
  {prompt:other}   the text of another prompt, so shared rules live in one file
Any other brace is plain text.

A module keeps its prompt as `Prompt("name")`: the text is read when the call
is made, so an edit applies to the next call without a restart.
"""

import difflib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config

DEFAULTS = Path(__file__).parent / "prompts"
NAME = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
INCLUDE = re.compile(r"\{prompt:([a-z][a-z0-9_]*)\}")
MAX_LENGTH = 60_000

# The order and names of the steps on the Prompts page.
STEPS = ("Themes and ideas", "Script", "Voice", "Storyboard", "Drawings", "Music", "Publishing", "Clipping")


class PromptError(ValueError):
    """An unknown prompt, or an edit that cannot be saved; the message is fit to show."""


@dataclass
class Meta:
    name: str
    title: str
    step: str
    task: str = ""                      # the llm task that sends it (its tier and model come from Settings)
    used_in: str = ""                   # the module that uses it
    about: str = ""                     # one line on what it does
    variables: list[str] = field(default_factory=list)
    variable_help: dict[str, str] = field(default_factory=dict)   # what each value is, for the Prompts page


def overrides_dir() -> Path:
    return config.data_dir() / "prompts"


def _split(text: str) -> tuple[dict, str]:
    """A file's header (`key: value` lines between --- marks) and its body."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    header = {}
    for line in text[4:end].splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            header[key.strip()] = value.strip()
    return header, text[end + 5:]


@lru_cache(maxsize=256)
def _default_file(name: str, mtime: float) -> tuple[Meta, str]:
    header, body = _split((DEFAULTS / f"{name}.md").read_text(encoding="utf-8"))
    variables = [v.strip() for v in header.get("variables", "").split(",") if v.strip()]
    meta = Meta(name=name, title=header.get("title", name), step=header.get("step", "Other"),
                task=header.get("task", ""), used_in=header.get("used_in", ""), about=header.get("about", ""),
                variables=variables, variable_help={v: header.get(f"var.{v}", "") for v in variables})
    return meta, body.strip("\n")


def _default(name: str) -> tuple[Meta, str]:
    if not NAME.match(name):
        raise PromptError(f"Unknown prompt: {name}")
    path = DEFAULTS / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"Unknown prompt: {name}")
    return _default_file(name, path.stat().st_mtime)


def names() -> list[str]:
    return sorted(p.stem for p in DEFAULTS.glob("*.md") if NAME.match(p.stem))


def meta(name: str) -> Meta:
    return _default(name)[0]


def default(name: str) -> str:
    return _default(name)[1]


def _override_path(name: str) -> Path:
    return overrides_dir() / f"{name}.md"


def overridden(name: str) -> bool:
    _default(name)
    return _override_path(name).is_file()


def text(name: str) -> str:
    """The prompt as it will be sent (before values are filled in): your edit, or the default."""
    _, body = _default(name)
    path = _override_path(name)
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip("\n")
        except OSError:
            return body
    return body


def _expand(body: str, seen: tuple[str, ...]) -> str:
    def include(match: re.Match) -> str:
        other = match.group(1)
        if other in seen:
            return match.group(0)       # a loop: leave the mark as it is rather than recurse forever
        try:
            return _expand(text(other), (*seen, other))
        except PromptError:
            return match.group(0)
    return INCLUDE.sub(include, body)


def render(name: str, **values) -> str:
    """The prompt with its includes expanded and the given values filled in.

    Only `{name}` marks for the values given are replaced, so braces in examples stay as written."""
    body = _expand(text(name), (name,))
    if not values:
        return body
    pattern = re.compile(r"\{(" + "|".join(re.escape(k) for k in values) + r")\}")
    return pattern.sub(lambda m: str(values[m.group(1)]), body)


class Prompt:
    """A prompt read when it is used. `str(p)` or `p.format(**values)` give the text to send."""

    def __init__(self, name: str):
        self.name = name

    def format(self, **values) -> str:
        return render(self.name, **values)

    def __str__(self) -> str:
        return render(self.name)

    def __add__(self, other) -> str:
        return str(self) + str(other)

    def __radd__(self, other) -> str:
        return str(other) + str(self)

    def __contains__(self, item: str) -> bool:
        return item in str(self)

    def __repr__(self) -> str:
        return f"Prompt({self.name!r})"


PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def problems(name: str, body: str) -> list[str]:
    """Why an edit cannot be kept: a value the code fills in was removed, a value that does not exist was
    added, or an include names no prompt. An edit with problems is refused, so a prompt never breaks."""
    m = meta(name)
    found = []
    for variable in m.variables:
        if "{" + variable + "}" not in body:
            found.append(f"{{{variable}}} is missing: the code fills it in, and without it the model "
                         "will not see that part")
    for unknown in dict.fromkeys(PLACEHOLDER.findall(body)):
        if unknown not in m.variables:
            offer = ", ".join("{" + v + "}" for v in m.variables) or "none"
            found.append(f"{{{unknown}}} is not a value the code fills in for this prompt (available: {offer})")
    known = set(names())
    for other in INCLUDE.findall(body):
        if other not in known:
            found.append(f"{{prompt:{other}}} names no prompt")
        elif other == name:
            found.append("A prompt cannot include itself")
    return found


def save(name: str, body: str) -> list[str]:
    """Keep your version of a prompt. Refused (PromptError) when it would break: see `problems`."""
    _default(name)
    body = body.replace("\r\n", "\n").strip("\n")
    if not body.strip():
        raise PromptError("A prompt cannot be empty: use Reset to go back to the default")
    if len(body) > MAX_LENGTH:
        raise PromptError(f"Too long: at most {MAX_LENGTH:,} characters")
    if body == default(name):
        reset(name)
        return []
    found = problems(name, body)
    if found:
        raise PromptError(" ".join(found))
    path = _override_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(body + "\n", encoding="utf-8")
    temp.replace(path)
    return []


def reset(name: str) -> None:
    _default(name)
    _override_path(name).unlink(missing_ok=True)


def diff(name: str) -> str:
    """Your edit against the default, as a unified diff; empty when there is no edit."""
    if not overridden(name):
        return ""
    return "\n".join(difflib.unified_diff(default(name).splitlines(), text(name).splitlines(),
                                          "default", "yours", lineterm="", n=2))


def catalogue() -> list[dict]:
    """Every prompt, grouped by step, with whether you changed it."""
    out = []
    for name in names():
        m, body = _default(name)
        mine = overridden(name)
        current = text(name)
        out.append({**m.__dict__, "overridden": mine, "text": current, "default": body,
                    "problems": problems(name, current) if mine else [],
                    "includes": INCLUDE.findall(current)})
    order = {step: i for i, step in enumerate(STEPS)}
    return sorted(out, key=lambda p: (order.get(p["step"], len(STEPS)), p["title"].lower()))
