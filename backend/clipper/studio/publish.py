"""The YouTube text for a rendered, approved video: title, description and tags.

The model writes only the parts that need writing (title options, an opening of
two or three short paragraphs, tags and hashtags), from the script's own text.
Everything factual that can be listed is assembled by code: chapters from the
take's timing, the sources the fact check relied on (numbered as in the
research), and the credit lines for pictures and music. You edit the result
and copy it into YouTube Studio; nothing is uploaded for you.
"""

import re

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import llm
from ..db.models import Storyboard, StudioPhoto, utcnow
from . import film, sound

# YouTube's limits (Studio refuses more).
TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 500          # all tags together, commas included
MAX_SOURCES = 12


class Draft(BaseModel):
    titles: list[str] = Field(description="Three title options, each under 70 characters, most promising first")
    opening: str = Field(description="Two or three short paragraphs for the top of the description")
    hashtags: list[str] = Field(description="Three hashtags without the #, single words or CamelCase")
    tags: list[str] = Field(description="Up to 12 search tags, lowercase, most specific first")


SYSTEM = """\
You write the YouTube title and description for an educational animated video, \
from its narration. Rules:
- Use only facts that are in the narration. No new numbers, dates, names or claims.
- Titles: honest and specific, a question or a surprising fact the video really \
answers; under 70 characters; no clickbait words (shocking, insane, you won't believe), \
no ALL CAPS, at most one emoji and preferably none.
- Opening: the first line works on its own, because YouTube shows only that line \
before "more": say what the viewer will learn. Then one or two short paragraphs. \
Plain words, no hype, no "In this video". Do not ask for likes or subscriptions.
- Hashtags: three, relevant to the topic.
- Tags: what people would type to find this video.
Never use the characters < or >."""


def credits(db: Session, board: Storyboard) -> str:
    """Every credit line the description needs: archive pictures, then music."""
    music = board.music
    music_line = (music.attribution or f"Music: {music.name}") if music and music.license else ""
    queries = film.photo_searches(board.style, board.scenes)
    found = {p.query: p for p in db.scalars(select(StudioPhoto).where(StudioPhoto.query.in_(queries)))} \
        if queries else {}
    return "\n".join([*(f"Picture: {_credit(found[q])}" for q in queries if q in found),
                      *([music_line] if music_line else [])])


def _credit(photo: StudioPhoto) -> str:
    """Made again from the photo's fields, so rows stored before a fix to the wording get it too."""
    from .photos import credit

    return credit({"title": photo.title, "creator": photo.creator, "license": photo.license,
                   "license_url": photo.license_url, "landing_url": photo.landing_url})


def sources(board: Storyboard) -> list[dict]:
    """The research sources this video's facts rest on: those cited by its checked claims, else all of them."""
    script = board.script
    every = (script.research or {}).get("sources", [])
    keys = {board.section_key} if board.section_key else None
    cited: list[int] = []
    for claim in script.claims or []:
        if keys and claim.get("section") not in keys:
            continue
        if claim.get("verdict") == "supported":
            cited += [n for n in claim.get("sources", []) if n not in cited]
    by_n = {s["n"]: s for s in every}
    chosen = [by_n[n] for n in sorted(cited) if n in by_n] or every
    return chosen[:MAX_SOURCES]


def _narration(board: Storyboard) -> str:
    sections = board.script.sections
    if board.section_key:
        sections = [s for s in sections if s["key"] == board.section_key]
    return "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in sections)


def _clean(text: str) -> str:
    return re.sub(r"[<>]", "", text).strip()


def assemble(board: Storyboard, opening: str, hashtags: list[str], credit_text: str) -> str:
    """The full description: opening, chapters, sources, credits, hashtags."""
    parts = [_clean(opening)]
    if board.format == "long":
        chapters = sound.chapters(board.words, {s["key"]: s["heading"] for s in board.script.sections})
        if chapters["valid"]:
            parts.append("Chapters\n" + chapters["text"])
    listed = sources(board)
    if listed:
        parts.append("Sources\n" + "\n".join(f"[{s['n']}] {_clean(s['title'])}: {s['url']}" for s in listed))
    if credit_text:
        parts.append("Credits\n" + _clean(credit_text))
    tags = [re.sub(r"[^\w]", "", h.lstrip("#")) for h in hashtags]
    tags = [t for t in tags if t][:3]
    if tags:
        parts.append(" ".join(f"#{t}" for t in tags))
    return "\n\n".join(p for p in parts if p)


def fit_tags(tags: list[str]) -> list[str]:
    """Clean tags and keep as many as fit in YouTube's 500 characters."""
    out: list[str] = []
    used = 0
    for tag in tags:
        tag = _clean(re.sub(r"\s+", " ", tag)).strip(", ").lower()
        if not tag or tag in out or len(tag) > 100:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + (1 if out else 0)   # quotes around tags with spaces, commas
        if used + cost > TAGS_MAX:
            break
        out.append(tag)
        used += cost
    return out


def problems(text: dict) -> list[str]:
    """What YouTube Studio would refuse."""
    out = []
    if not text.get("title", "").strip():
        out.append("The title is empty")
    if len(text.get("title", "")) > TITLE_MAX:
        out.append(f"The title is longer than {TITLE_MAX} characters")
    if len(text.get("description", "")) > DESCRIPTION_MAX:
        out.append(f"The description is longer than {DESCRIPTION_MAX} characters")
    if any(c in text.get("title", "") + text.get("description", "") for c in "<>"):
        out.append("YouTube does not accept < or > in titles and descriptions")
    return out


def draft(db: Session, board: Storyboard) -> dict:
    """Ask the writing tier for the title and opening, then assemble the rest. About a cent."""
    kind = "a YouTube Short (vertical, under a minute)" if board.format == "short" else "a long YouTube video"
    content = (f"<video>{kind}; working title: {board.title}</video>\n"
               f"<narration>\n{_narration(board)}\n</narration>")
    answer: Draft = llm.ask("description", SYSTEM, content, Draft, effort="low")
    titles = [_clean(t)[:TITLE_MAX] for t in answer.titles if _clean(t)][:3] or [board.title[:TITLE_MAX]]
    text = {"titles": titles, "title": titles[0],
            "description": assemble(board, answer.opening, answer.hashtags, credits(db, board)),
            "tags": fit_tags(answer.tags), "made_at": utcnow().isoformat(), "edited": False}
    text["problems"] = problems(text)
    return text


def edit(current: dict | None, title: str, description: str, tags: list[str]) -> dict:
    text = {**(current or {"titles": []}), "title": _clean(title), "description": description.strip(),
            "tags": fit_tags(tags), "edited": True}
    text["problems"] = problems(text)
    return text
