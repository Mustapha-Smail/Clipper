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

from .. import config, llm, prompts
from ..db.models import Idea, utcnow
from . import plain, youtube

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
    hook: str = Field(description="The question or claim said in the first five seconds; it names the topic in "
                                  "its first words")
    expected: str = Field(description="What most viewers would guess before watching, in one sentence; the video "
                                      "proves it wrong or more surprising")
    answer: str = Field(description="The surprising answer, in one plain sentence, that overturns the expected guess")
    beats: list[Beat] = Field(description="3 to 7 steps from hook to payoff")
    angle: str = Field(description="What makes this take different from the usual explainer on the topic")
    format: Literal["single", "list"] = Field(
        description="single: one object, about 5 to 10 minutes. list: several objects, one chapter each")
    search_query: str = Field(description="What a viewer would type into YouTube to find videos on this topic, "
                                          "3 to 6 plain words")


class Drafts(BaseModel):
    ideas: list[Draft]


class Judgement(BaseModel):
    hook_score: int = Field(description="0 to 10: would a casual viewer need to know the answer after the hook? "
                                        "Ask three things: is the topic clear from its first words, is there a "
                                        "reason to lean in (stakes or curiosity), and does the answer overturn the "
                                        "expected guess? Low if any of the three is missing")
    hook_reason: str
    easy_score: int = Field(description="0 to 10: could the audience follow the whole answer on one listen, with "
                                        "no background? Low if it needs terms, names, dates or prior knowledge "
                                        "they lack, or if it starts from something outside their daily life")
    easy_reason: str
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


GENERATE = prompts.Prompt("idea_generate")

JUDGE = prompts.Prompt("idea_check")


def _format_existing(existing: list[str]) -> str:
    return "\n".join(f"- {title}" for title in existing[-200:]) or "(none yet)"


def generate(theme: str, count: int, existing: list[str], seed: str = "") -> list[Draft]:
    """Propose `count` ideas for the theme, or develop the user's own idea when `seed` is given."""
    ask = (f"Develop this idea of mine into exactly one idea, keeping its subject:\n<my_idea>{seed}</my_idea>"
           if seed else f"Propose {count} different ideas.")
    prompt = (f"<theme>{theme}</theme>\n\n<already_have>\n{_format_existing(existing)}\n</already_have>\n\n{ask}")
    drafts = llm.ask("ideas", GENERATE.format(audience_rules=plain.idea_rules()), prompt, Drafts,
                     effort="medium").ideas
    return drafts[:1] if seed else drafts[:count]


class ProvenDraft(Draft):
    inspired_by: list[int] = Field(description="The numbers of the proven examples this idea builds on, 1 to 3")
    why_now: str = Field(description="One sentence: the proven pattern this idea follows")


class ProvenDrafts(BaseModel):
    ideas: list[ProvenDraft]


GENERATE_PROVEN = prompts.Prompt("idea_from_theme")


def generate_proven(theme: str, about: str, count: int, existing: list[str], examples: str, lessons: str,
                    rejected: list[str], hint: str = "") -> list[ProvenDraft]:
    """`count` ideas built on what already worked in the theme (see learning.proven for the examples)."""
    rejected_text = "\n".join(f"- {t}" for t in rejected) or "(none)"
    ask = f"Propose {count} different ideas." + (f" The creator would like them to lean towards: {hint}"
                                                  if hint.strip() else "")
    prompt = (f"<theme>{theme}</theme>\n" + (f"<about>{about}</about>\n" if about else "")
              + f"\n<proven>\n{examples}\n</proven>\n\n<lessons>\n{lessons}\n</lessons>\n\n"
              f"<rejected>\n{rejected_text}\n</rejected>\n\n"
              f"<already_have>\n{_format_existing(existing)}\n</already_have>\n\n{ask}")
    drafts = llm.ask("ideas_proven", GENERATE_PROVEN.format(audience_rules=plain.idea_rules()), prompt,
                     ProvenDrafts, effort="medium").ideas
    return drafts[:count]


# --- a batch of ideas, as a worker job ---------------------------------------------------

def with_about(theme) -> str:
    """The theme as the idea prompts get it: its name, and what it is about when you said."""
    return theme.name + (f"\n{theme.description}" if theme.description else "")


def existing_titles(db) -> list[str]:
    from sqlalchemy import select

    return list(db.scalars(select(Idea.title).order_by(Idea.id)))


def proven_examples(db, theme, keys: list[str]) -> list[dict]:
    from . import learning

    return learning.chosen(learning.proven(db, theme), keys)


def run_generation(params: dict, rep) -> str:
    """The `ideas` job: ask for a batch of ideas for a theme and leave them in its inbox. Returns the job's message.

    params: theme_id, count, mode (fresh | proven), hint, examples (keys of the proven examples you kept)."""
    from ..db import session_scope
    from ..db.models import Theme, utcnow
    from . import learning

    count, mode, hint = int(params.get("count") or 8), params.get("mode", "fresh"), params.get("hint", "")
    with session_scope() as db:
        theme = db.get(Theme, params["theme_id"])
        if theme is None:
            raise RuntimeError("The theme was deleted")
        existing = existing_titles(db)
        if mode == "proven":
            examples = proven_examples(db, theme, params.get("examples") or [])
            if not examples:
                raise RuntimeError("Nothing is proven in this theme any more: keep or approve a few ideas first")
            prompt_parts = (theme.name, theme.description, learning.examples_text(examples),
                            learning.lessons_text(theme), learning.rejected_titles(db, theme.id))
        else:
            examples, prompt_parts = [], None
        name, about = theme.name, with_about(theme)
    rep.info(f"Asking for {count} {'ideas built on what works' if mode == 'proven' else 'new ideas'} for “{name}”"
             + (f", leaning towards: {hint}" if hint.strip() else "")
             + (f", from {len(examples)} proven example{'s' if len(examples) != 1 else ''}" if examples else ""))
    rep.progress(0.1)
    with llm.attribute(f"theme:{params['theme_id']}"):
        if prompt_parts:
            theme_name, description, examples_text, lessons, rejected = prompt_parts
            drafts = generate_proven(theme_name, description, count, existing, examples_text, lessons, rejected, hint)
        else:
            drafts = generate(about + (f"\nLean towards: {hint.strip()}" if hint.strip() else ""), count, existing)
    rep.progress(0.9)
    by_number = {e["n"]: e for e in examples}
    with session_scope() as db:
        theme = db.get(Theme, params["theme_id"])
        if theme is None:
            raise RuntimeError("The theme was deleted while the ideas were being written")
        for draft in drafts:
            idea = Idea(theme=theme.name, theme_id=theme.id, origin="claude", status="inbox")   # waits until kept
            apply_draft(idea, draft)
            if examples:
                idea.inspired_by = [{"kind": by_number[n]["kind"], "id": by_number[n]["id"],
                                     "title": by_number[n]["title"], "evidence": by_number[n]["evidence"]}
                                    for n in dict.fromkeys(getattr(draft, "inspired_by", [])) if n in by_number][:3]
                if getattr(draft, "why_now", ""):
                    idea.notes = f"Builds on: {draft.why_now}"
            db.add(idea)
        if drafts:
            theme.updated_at = utcnow()
    for draft in drafts:
        rep.info(f"“{draft.title}”")
    log.info("ideas generated", extra={"theme_id": params["theme_id"], "count": len(drafts), "mode": mode})
    return f"{len(drafts)} new idea{'s' if len(drafts) != 1 else ''} waiting to be sorted"


def judge(idea: Idea, research: dict | None) -> Judgement:
    lines = []
    for v in (research or {}).get("videos", []):
        subs = "hidden" if v["subscribers"] is None else f"{v['subscribers']:,}"
        lines.append(f"- \"{v['title']}\" by {v['channel']} ({subs} subscribers): {v['views']:,} views, "
                     f"published {v['published'][:10]}, {v['duration_s'] // 60} min {v['duration_s'] % 60} s")
    results = "\n".join(lines) or "(not available: YouTube was not checked)"
    beats = "\n".join(f"- {b['text']}" + (" [Short]" if b.get("short") else "") for b in idea.beats)
    prompt = (f"<theme>{idea.theme}</theme>\n\n<idea>\nTitle: {idea.title}\nHook: {idea.hook}\n"
              f"Expected guess: {idea.expected or '(not given)'}\nAnswer: {idea.answer}\nAngle: {idea.angle}\n"
              f"Format: {idea.format}\nBeats:\n{beats}\n</idea>\n\n"
              f"<youtube_results query=\"{idea.search_query}\" today=\"{datetime.now(UTC).date()}\">\n"
              f"{results}\n</youtube_results>")
    return llm.ask("idea_check", JUDGE.format(audience=plain.audience()), prompt, Judgement, effort="medium")


def apply_draft(idea: Idea, draft: Draft) -> None:
    for field, value in draft.model_dump(exclude={"inspired_by", "why_now"}).items():
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


THEMES = prompts.Prompt("theme_suggest")


def suggest_themes(existing: list[str], hint: str = "") -> list[ThemeIdea]:
    """Five themes to start from, different from the ones already used. Quick tier, a few hundred tokens."""
    have = "\n".join(f"- {t}" for t in existing[:30]) or "(none yet)"
    ask = (f"The creator is leaning towards: {hint}. Suggest five themes in that direction, each with its own angle."
           if hint.strip() else "Suggest five different themes.")
    prompt = f"<themes_already_used>\n{have}\n</themes_already_used>\n\n{ask}"
    return llm.ask("themes", THEMES.format(audience_rules=plain.idea_rules()), prompt, ThemeIdeas,
                   effort="low").themes[:5]


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


SHORT_SECONDS = 50          # the length suggested for a Short


def suggest_length(idea: Idea) -> dict:
    """Long video or Short, and how long, from what the check found; the reason says how it was worked out."""
    beats = len(idea.beats or [])
    research = idea.research
    if research and research.get("videos"):
        videos = research["videos"]
        long = sorted((v for v in videos if v["duration_s"] > LONG_FORM_S), key=lambda v: v["views"], reverse=True)
        shorts = [v for v in videos if v["duration_s"] <= LONG_FORM_S]
        best_long = long[0]["views"] if long else 0
        best_short = max((v["views"] for v in shorts), default=0)
        if shorts and best_short > 2 * best_long:
            return {"video": "short", "minutes": None, "seconds": SHORT_SECONDS,
                    "reason": f"Shorts lead this search: the best Short has {_views(best_short)} views, the best "
                              f"long video {_views(best_long)}. A Short of about {SHORT_SECONDS} s."}
        if long:
            top = long[:5]
            minutes = sorted(v["duration_s"] / 60 for v in top)
            middle = minutes[len(minutes) // 2]
            target = max(4, min(15, round(middle)))
            if idea.format == "list":
                target = max(target, 8)
            return {"video": "long", "minutes": target, "seconds": None,
                    "reason": f"The {len(top)} most-viewed long videos run {minutes[0]:.0f} to {minutes[-1]:.0f} min "
                              f"(middle {middle:.0f})" + (", and a list needs room for each item" if
                                                          idea.format == "list" and target > round(middle) else "")
                              + f": about {target} min, between 4 and 15."}
    if idea.format == "single" and beats <= 2:
        return {"video": "short", "minutes": None, "seconds": SHORT_SECONDS,
                "reason": f"One quick fact in {beats} beat{'s' if beats != 1 else ''}: it fits a Short "
                          "(not checked on YouTube)."}
    target = 6 if idea.format == "single" else 9 + max(0, beats - 5)
    return {"video": "long", "minutes": target, "seconds": None,
            "reason": f"Not checked on YouTube: {target} min is the usual length for "
                      + ("one topic." if idea.format == "single" else f"a list of {beats} items.")}


def assess(idea: Idea) -> dict:
    """A 0-100 score, the lines that explain it, warnings, and the demand figures shown on the card."""
    judgement = idea.judgement
    if judgement is None:
        return {"score": None, "complete": False, "breakdown": [], "flags": [], "demand": None, "length": None}

    parts: list[tuple[str, float]] = []
    flags: list[str] = []
    demand = None

    research = idea.research
    if research is None:
        parts.append(("YouTube not checked: demand, room and age unknown", 0.0))
        flags.append("Scored without YouTube data, so out of 50 at most. Add YOUTUBE_API_KEY to .env to check demand")
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
                          25 * _log_scale(reference["views"], 10_000, 1_000_000)))

            small = [v for v in long if v["subscribers"] is not None and v["subscribers"] < SMALL_CHANNEL]
            best_small = max(small, key=lambda v: v["views"]) if small else None
            if best_small:
                parts.append((f"Room: a channel under 100K subscribers got {_views(best_small['views'])} views",
                              15 * _log_scale(best_small["views"], 5_000, 500_000)))
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
    easy = judgement.get("easy_score")
    if easy is None:
        parts.append(("Easy to follow: not checked yet (check the idea again)", 0.0))
    else:
        parts.append((f"Easy to follow {easy}/10", float(easy)))
        if easy <= 4:
            flags.append(f"Needs background the audience may not have: {judgement.get('easy_reason', '')}")
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
        "length": suggest_length(idea),
    }
