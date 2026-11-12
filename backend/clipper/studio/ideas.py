"""Video ideas: proposed by Claude for a theme, checked against YouTube, scored.

The score is plain arithmetic on two inputs kept on the idea: what YouTube
returned for its search (`research`) and Claude's reading of the idea against
those results (`judgement`). Every point added or removed appears as a line in
the breakdown, so a score can always be explained.
"""

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from .. import config, llm
from ..db.models import Idea, utcnow
from . import youtube

log = logging.getLogger("clipper.studio")

LONG_FORM_S = 60            # shorter videos are Shorts; demand is measured on long videos
SMALL_CHANNEL = 100_000     # subscribers
BIG_CHANNEL = 1_000_000
OLD_YEARS = 3


# --- what Claude returns --------------------------------------------------------

class Beat(BaseModel):
    text: str = Field(description="One step of the video, in a sentence")
    short: bool = Field(description="True if this beat could stand alone as a 30 to 60 second Short")


class Draft(BaseModel):
    title: str = Field(description="A working YouTube title: a surprising claim or question, not 'How X works'")
    hook: str = Field(description="The question or claim said in the first five seconds")
    answer: str = Field(description="The surprising answer, in one plain sentence")
    beats: list[Beat] = Field(description="3 to 7 steps from hook to payoff")
    angle: str = Field(description="What makes this take different from the usual explainer on the topic")
    format: Literal["single", "list"] = Field(
        description="single: one object, about 5 to 10 minutes. list: several objects, one chapter each")
    search_query: str = Field(description="What a viewer would type into YouTube to find videos on this topic, "
                                          "3 to 6 plain words")


class Drafts(BaseModel):
    ideas: list[Draft]


class Judgement(BaseModel):
    hook_score: int = Field(description="0 to 10: would a casual viewer need to know the answer after the hook?")
    hook_reason: str
    drawable_score: int = Field(description="0 to 10: can it be told with simple drawn characters, objects and "
                                            "diagrams, without footage or detailed illustration?")
    drawable_reason: str
    facts_score: int = Field(description="0 to 10: is the explanation settled and easy to source? "
                                         "Low if it rests on myths, disputed stories or claims you cannot verify")
    facts_reason: str
    ad_friendly: bool = Field(description="False if the topic is likely to get limited ads on YouTube "
                                          "(violence, tragedy, sexual content, drugs, controversial politics)")
    ad_reason: str
    angle_taken: bool = Field(description="True if one of the results already tells it with essentially this angle")
    angle_reason: str
    big_recent_same: str = Field(description="The title of a result from a channel over 1M subscribers, published in "
                                             "the last 2 years, that covers the same angle; empty if none")


GENERATE = """\
You come up with video ideas for a faceless YouTube channel of short animated videos: \
simple drawn characters, deadpan comic narration, one surprising idea per video or per \
chapter. The channel sticks to one theme, given below.

A good idea is about something specific that viewers use or see every day, and turns on \
a surprising mechanism or fact the viewer can repeat to a friend. The working title is a \
claim or question that creates curiosity ("Your fridge doesn't make cold"), never a flat \
"How X works". Big science channels already cover the obvious explainers, so look for the \
angle they miss: a funny consequence, a hidden history, a wrong common belief.

The facts must be true and well established. Do not build an idea on a popular myth or \
an anecdote you cannot verify; if a famous story is disputed, the dispute can be the idea. \
Avoid topics that YouTube limits ads on (violence, tragedy, drugs, divisive politics).

Do not repeat or lightly reword any idea in the <already_have> list."""

JUDGE = """\
You assess one video idea for a faceless animated YouTube channel (simple drawn \
characters, comic narration) before any work is done on it. You get the idea and the top \
YouTube search results for its topic, with views, channel size, age and length.

Be strict and calibrated: 5 is an ordinary idea, 8 or more is rare. Give each reason in \
one sentence that names what you saw. Judge the angle against the result titles you were \
given, not against videos you imagine exist."""


def _format_existing(existing: list[str]) -> str:
    return "\n".join(f"- {title}" for title in existing[-200:]) or "(none yet)"


def generate(theme: str, count: int, existing: list[str], seed: str = "") -> list[Draft]:
    """Propose `count` ideas for the theme, or develop the user's own idea when `seed` is given."""
    ask = (f"Develop this idea of mine into exactly one idea, keeping its subject:\n<my_idea>{seed}</my_idea>"
           if seed else f"Propose {count} different ideas.")
    prompt = (f"<theme>{theme}</theme>\n\n<already_have>\n{_format_existing(existing)}\n</already_have>\n\n{ask}")
    drafts = llm.ask("ideas", GENERATE, prompt, Drafts, effort="medium").ideas
    return drafts[:1] if seed else drafts[:count]


def judge(idea: Idea, research: dict | None) -> Judgement:
    lines = []
    for v in (research or {}).get("videos", []):
        subs = "hidden" if v["subscribers"] is None else f"{v['subscribers']:,}"
        lines.append(f"- \"{v['title']}\" by {v['channel']} ({subs} subscribers): {v['views']:,} views, "
                     f"published {v['published'][:10]}, {v['duration_s'] // 60} min {v['duration_s'] % 60} s")
    results = "\n".join(lines) or "(not available: YouTube was not checked)"
    beats = "\n".join(f"- {b['text']}" + (" [Short]" if b.get("short") else "") for b in idea.beats)
    prompt = (f"<theme>{idea.theme}</theme>\n\n<idea>\nTitle: {idea.title}\nHook: {idea.hook}\n"
              f"Answer: {idea.answer}\nAngle: {idea.angle}\nFormat: {idea.format}\nBeats:\n{beats}\n</idea>\n\n"
              f"<youtube_results query=\"{idea.search_query}\" today=\"{datetime.now(UTC).date()}\">\n"
              f"{results}\n</youtube_results>")
    return llm.ask("idea_check", JUDGE, prompt, Judgement, effort="medium")


def apply_draft(idea: Idea, draft: Draft) -> None:
    for field, value in draft.model_dump().items():
        setattr(idea, field, value)


def validate(idea: Idea) -> None:
    """Check YouTube (when a key is set) and ask Claude for its judgement."""
    research = None
    if config.youtube_api_key():
        research = youtube.research(idea.search_query or idea.title)
    idea.research = research
    idea.judgement = judge(idea, research).model_dump()
    idea.validated_at = utcnow()
    log.info("idea checked", extra={"idea_id": idea.id, "youtube": research is not None,
                                    "results": len((research or {}).get("videos", []))})


# --- themes, when inspiration runs out ---------------------------------------------------

class ThemeIdea(BaseModel):
    theme: str = Field(description="The channel theme in a few words, as it would go in the theme field")
    why: str = Field(description="One sentence: why it works for this kind of channel")
    example: str = Field(description="One example video title for it")


class ThemeIdeas(BaseModel):
    themes: list[ThemeIdea]


THEMES = """\
You suggest themes for a faceless YouTube channel of short animated videos: simple drawn \
characters, deadpan comic narration, one surprising idea per video or per chapter. A \
theme is what every video of the channel is about, narrow enough to build an audience, \
wide enough for hundreds of videos.

Good themes:
- turn on surprising, true, explainable facts that a viewer repeats to friends;
- can be drawn with stick figures, objects and diagrams, without footage;
- lean towards topics advertisers pay more for (technology, science, money and \
business, health, how things work, history) over ones they avoid (violence, tragedy, \
divisive politics);
- are not already crowded by huge channels doing the same thing in the same tone.

Vary them: do not give five versions of one idea. Do not repeat or lightly reword the \
themes the creator already has."""


def suggest_themes(existing: list[str], hint: str = "") -> list[ThemeIdea]:
    """Five themes to start from, different from the ones already used. Quick tier, a few hundred tokens."""
    have = "\n".join(f"- {t}" for t in existing[:30]) or "(none yet)"
    ask = (f"The creator is leaning towards: {hint}. Suggest five themes in that direction, each with its own angle."
           if hint.strip() else "Suggest five different themes.")
    return llm.ask("themes", THEMES, f"<themes_already_used>\n{have}\n</themes_already_used>\n\n{ask}",
                   ThemeIdeas, effort="low").themes[:5]


# --- the score ------------------------------------------------------------------------

def _views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}K"
    return str(n)


def _log_scale(value: float, zero: float, full: float) -> float:
    """0 at `zero`, 1 at `full`, on a log scale, clamped."""
    if value <= zero:
        return 0.0
    return min(1.0, math.log10(value / zero) / math.log10(full / zero))


def _published(video: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(video["published"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def assess(idea: Idea) -> dict:
    """A 0-100 score, the lines that explain it, warnings, and the demand figures shown on the card."""
    judgement = idea.judgement
    if judgement is None:
        return {"score": None, "complete": False, "breakdown": [], "flags": [], "demand": None}

    parts: list[tuple[str, float]] = []
    flags: list[str] = []
    demand = None

    research = idea.research
    if research is None:
        parts.append(("YouTube not checked: demand, room and age unknown", 0.0))
        flags.append("Scored without YouTube data, so out of 40 at most. Add YOUTUBE_API_KEY to .env to check demand")
    else:
        long = sorted((v for v in research["videos"] if v["duration_s"] > LONG_FORM_S),
                      key=lambda v: v["views"], reverse=True)
        if not long:
            parts.append(("No long videos found for this search", 0.0))
            flags.append(f"YouTube returned no long videos for \"{research['query']}\": try another search")
        else:
            reference = long[min(2, len(long) - 1)]
            rank = "3rd most-viewed" if len(long) >= 3 else "Most-viewed"
            parts.append((f"Demand: {rank} video has {_views(reference['views'])} views",
                          30 * _log_scale(reference["views"], 10_000, 1_000_000)))

            small = [v for v in long if v["subscribers"] is not None and v["subscribers"] < SMALL_CHANNEL]
            best_small = max(small, key=lambda v: v["views"]) if small else None
            if best_small:
                parts.append((f"Room: a channel under 100K subscribers got {_views(best_small['views'])} views",
                              20 * _log_scale(best_small["views"], 5_000, 500_000)))
            else:
                parts.append(("Room: no channel under 100K subscribers in the results", 0.0))

            top = long[:5]
            cutoff = datetime.now(UTC) - timedelta(days=365 * OLD_YEARS)
            old = sum(1 for v in top if (d := _published(v)) and d < cutoff)
            parts.append((f"Age: {old} of the top {len(top)} are over {OLD_YEARS} years old", 5 * old / len(top)))

            demand = {"reference_views": reference["views"], "reference_rank": rank,
                      "best_small_views": best_small["views"] if best_small else None,
                      "best_small_channel": best_small["channel"] if best_small else None,
                      "old_share": f"{old}/{len(top)}", "videos": len(long)}

    if research is None:
        parts.append(("Angle not compared with existing videos", 0.0))
    elif judgement["angle_taken"]:
        parts.append(("Angle already used by a top result", 0.0))
    else:
        parts.append(("Angle not used by the top results", 5.0))

    parts.append((f"Hook {judgement['hook_score']}/10", judgement["hook_score"] * 1.5))
    parts.append((f"Drawable {judgement['drawable_score']}/10", float(judgement["drawable_score"])))
    parts.append((f"Facts settled {judgement['facts_score']}/10", float(judgement["facts_score"])))
    shorts = sum(1 for b in idea.beats if b.get("short"))
    parts.append((f"{shorts} beat{'s' if shorts != 1 else ''} usable as Shorts", float(min(shorts, 5))))

    if not judgement["ad_friendly"]:
        parts.append(("Likely to get limited ads", -15.0))
        flags.append(f"Limited ads likely: {judgement['ad_reason']}")
    if judgement["big_recent_same"]:
        parts.append(("A channel over 1M subscribers did this angle recently", -10.0))
        flags.append(f"Recently covered the same way by a big channel: \"{judgement['big_recent_same']}\"")
    if judgement["facts_score"] <= 4:
        flags.append(f"Facts are shaky: {judgement['facts_reason']}")

    score = sum(points for _, points in parts)
    return {
        "score": max(0, round(score)),
        "complete": research is not None,
        "breakdown": [{"label": label, "points": round(points, 1)} for label, points in parts],
        "flags": flags,
        "demand": demand,
    }
