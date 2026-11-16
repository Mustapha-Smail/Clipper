"""What a theme has learnt from its own videos: statistics, the ranking, and the lessons drawn.

Statistics come from Buffer for the Shorts it posted (`posting.sync_metrics`) and
from YouTube's Data API for long videos you published by hand and linked
(`storyboards.published_url`). Never from scraping.

The ranking is arithmetic, shown line by line: views across networks, the
engagement rate, and how a video compares with the theme's median. The model
only reads that table to put words on the patterns (`insights`, task
`theme_insights`) and to propose new ideas built on what worked
(`ideas.generate_proven`); it never invents a number.
"""

import logging
import statistics
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, llm, prompts
from ..db import session_scope
from ..db.models import Idea, Post, Script, Storyboard, Theme, as_utc, utcnow
from ..jobs import Reporter
from . import ideas as ideas_mod
from . import youtube

log = logging.getLogger("clipper.studio")

COUNTED = ("views", "likes", "comments", "shares", "saves")
STATS_EVERY = timedelta(hours=20)       # a long video's YouTube statistics are read at most this often
MIN_FOR_LESSONS = 3                     # videos with views before the lessons can say anything
NETWORKS = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}


# --- statistics ----------------------------------------------------------------------------

def refresh_youtube(rep: Reporter | None = None, force: bool = False) -> int:
    """Read the statistics of long videos published by hand and linked. Returns how many were updated."""
    if not config.youtube_api_key():
        if rep:
            rep.info("No YOUTUBE_API_KEY in .env: statistics of videos published by hand are not read")
        return 0
    due = utcnow() - STATS_EVERY
    with session_scope() as db:
        boards = {b.id: youtube.video_id(b.published_url or "") for b in db.scalars(
            select(Storyboard).where(Storyboard.published_url.is_not(None)))
            if force or not b.stats or not b.stats.get("at")
            or as_utc(datetime.fromisoformat(b.stats["at"])) < due}
    wanted = {board_id: vid for board_id, vid in boards.items() if vid}
    if not wanted:
        return 0
    try:
        found = youtube.stats(sorted(set(wanted.values())))
    except youtube.YouTubeError as err:
        if rep:
            rep.warn(f"YouTube statistics not read: {err}")
        return 0
    with session_scope() as db:
        for board_id, vid in wanted.items():
            if vid in found:
                db.get(Storyboard, board_id).stats = {**found[vid], "at": utcnow().isoformat(), "source": "youtube"}
    if rep:
        rep.info(f"Read YouTube statistics of {len(found)} video{'s' if len(found) != 1 else ''} published by hand")
    return len(found)


def refresh(rep: Reporter | None = None, force: bool = False) -> str:
    """Every statistic we can read: Buffer posts, then videos published by hand. Returns the job's message."""
    from .. import posting

    posts = posting.sync_metrics(rep, force=force)
    linked = refresh_youtube(rep, force=force)
    return f"Statistics read for {posts} post{'s' if posts != 1 else ''} and {linked} linked video" \
           + ("s" if linked != 1 else "")


def _numbers(board: Storyboard, posts: list[Post]) -> dict:
    """One video's statistics, added up across the networks it went out on."""
    totals = {k: 0.0 for k in COUNTED}
    known = {k: False for k in COUNTED}
    networks = []
    for post in posts:
        values = (post.metrics or {}).get("values") or {}
        networks.append({"service": post.service, "name": NETWORKS.get(post.service, post.service),
                         "link": post.link, "views": values.get("views"), "status": post.status})
        for key in COUNTED:
            if values.get(key) is not None:
                totals[key] += values[key]
                known[key] = True
    if board.stats:
        networks.append({"service": "youtube", "name": "YouTube (by hand)", "link": board.published_url,
                         "views": board.stats.get("views"), "status": "sent"})
        for key in ("views", "likes", "comments"):
            if board.stats.get(key) is not None:
                totals[key] += board.stats[key]
                known[key] = True
    out = {k: (int(totals[k]) if known[k] else None) for k in COUNTED}
    views = out["views"] or 0
    reactions = sum(out[k] or 0 for k in ("likes", "comments", "shares", "saves"))
    out["engagement"] = round(100 * reactions / views, 1) if views else None
    out["networks"] = networks
    return out


def published(db: Session, theme_id: int) -> list[dict]:
    """Every video of the theme that went out (sent on Buffer, or published by hand), with its numbers."""
    boards = db.execute(select(Storyboard, Script).join(Script, Storyboard.script_id == Script.id)
                        .where(Script.theme_id == theme_id)).all()
    if not boards:
        return []
    ids = [b.id for b, _ in boards]
    posts: dict[int, list[Post]] = {}
    for post in db.scalars(select(Post).where(Post.storyboard_id.in_(ids), Post.status == "sent")):
        posts.setdefault(post.storyboard_id, []).append(post)
    ideas = {i.id: i for i in db.scalars(select(Idea).where(Idea.id.in_([s.idea_id for _, s in boards
                                                                          if s.idea_id])))}
    rows = []
    for board, script in boards:
        mine = posts.get(board.id, [])
        if not mine and not board.published_at:
            continue
        idea = ideas.get(script.idea_id) if script.idea_id else None
        sent = [as_utc(p.sent_at) for p in mine if p.sent_at]
        when = min(sent) if sent else as_utc(board.published_at)
        rows.append({
            "storyboard_id": board.id, "script_id": script.id, "idea_id": idea.id if idea else None,
            "title": board.title or script.title, "script_title": script.title,
            "hook": idea.hook if idea else "", "angle": idea.angle if idea else "",
            "format": board.format, "style": board.style, "look": board.look,
            "seconds": round(board.end - board.start) if board.end else None,
            "published_at": when.isoformat() if when else None,
            "published_url": board.published_url,
            **_numbers(board, mine),
        })
    return rows


def ranked(rows: list[dict]) -> dict:
    """Best first by views, each compared with the theme's median; the lines say how it was worked out."""
    with_views = [r for r in rows if r["views"] is not None]
    median = statistics.median([r["views"] for r in with_views]) if with_views else None
    for row in rows:
        if row["views"] is None or not median:
            row["vs_median"] = None
            row["lines"] = ["No statistics yet"]
            continue
        row["vs_median"] = round(row["views"] / median, 2) if median else None
        lines = [f"{row['views']:,} views across {len([n for n in row['networks'] if n['views'] is not None])} "
                 "network(s)", f"{row['vs_median']}x the theme's median of {int(median):,}"]
        if row["engagement"] is not None:
            lines.append(f"{row['engagement']}% engagement (likes, comments, shares and saves per view)")
        row["lines"] = lines
    order = sorted(rows, key=lambda r: (r["views"] is None, -(r["views"] or 0)))
    return {"videos": order, "median_views": int(median) if median else None,
            "with_views": len(with_views), "total_views": sum(r["views"] for r in with_views)}


def summary(db: Session, theme_id: int) -> dict:
    """The figures shown on a theme's card."""
    table = ranked(published(db, theme_id))
    best = next((v for v in table["videos"] if v["views"] is not None), None)
    return {"published": len(table["videos"]), "with_views": table["with_views"],
            "total_views": table["total_views"], "median_views": table["median_views"],
            "best_title": best["title"] if best else None, "best_views": best["views"] if best else None}


# --- what is proven: the examples ideas are built on ---------------------------------------

def proven(db: Session, theme: Theme, most: int = 24) -> list[dict]:
    """Numbered examples for the model, strongest evidence first: videos that did well, approved
    scripts, then ideas that scored well. Each says why it counts."""
    examples: list[dict] = []
    seen_ideas: set[int] = set()
    table = ranked(published(db, theme.id))
    for video in table["videos"][:10]:
        if video["views"] is None:
            evidence = "published, no statistics yet"
        else:
            evidence = f"published: {video['views']:,} views ({video['vs_median']}x the median)" + (
                f", {video['engagement']}% engagement" if video["engagement"] is not None else "")
        examples.append({"kind": "video", "id": video["storyboard_id"], "idea_id": video["idea_id"],
                         "title": video["title"], "hook": video["hook"], "angle": video["angle"],
                         "evidence": evidence, "format": video["format"]})
        if video["idea_id"]:
            seen_ideas.add(video["idea_id"])
    for script in db.scalars(select(Script).where(Script.theme_id == theme.id, Script.status == "approved")
                             .order_by(Script.updated_at.desc())):
        if script.idea_id in seen_ideas:
            continue
        opening = next((s["text"] for s in script.sections or [] if s.get("text")), "")
        examples.append({"kind": "script", "id": script.id, "idea_id": script.idea_id, "title": script.title,
                         "hook": opening[:240], "angle": "", "evidence": "script approved by you",
                         "format": script.video})
        if script.idea_id:
            seen_ideas.add(script.idea_id)
    scored = []
    for idea in db.scalars(select(Idea).where(Idea.theme_id == theme.id, Idea.status.in_(("new", "approved")))):
        if idea.id in seen_ideas:
            continue
        score = ideas_mod.assess(idea)["score"]
        if idea.status == "approved" or (score or 0) >= 45:
            scored.append((idea.status == "approved", score or 0, idea))
    for approved, score, idea in sorted(scored, key=lambda t: (t[0], t[1]), reverse=True):
        evidence = ("idea approved by you" if approved else "idea kept by you") + (
            f", checked at {score}/100" if score else "")
        examples.append({"kind": "idea", "id": idea.id, "idea_id": idea.id, "title": idea.title,
                         "hook": idea.hook, "angle": idea.angle, "evidence": evidence, "format": idea.video})
    examples = examples[:most]
    for n, example in enumerate(examples, 1):
        example["n"] = n
        example["key"] = f"{example['kind']}:{example['id']}"
    return examples


def chosen(examples: list[dict], keys: list[str]) -> list[dict]:
    """Only the examples you kept ticked, numbered again from 1; all of them when none is named."""
    if not keys:
        return examples
    wanted = set(keys)
    kept = [dict(e) for e in examples if e["key"] in wanted]
    for n, example in enumerate(kept, 1):
        example["n"] = n
    return kept


def rejected_titles(db: Session, theme_id: int, most: int = 40) -> list[str]:
    return list(db.scalars(select(Idea.title).where(Idea.theme_id == theme_id, Idea.status == "skipped")
                           .order_by(Idea.id.desc()).limit(most)))


def examples_text(examples: list[dict]) -> str:
    lines = []
    for e in examples:
        line = f"[{e['n']}] \"{e['title']}\" ({e['kind']}, {e['evidence']})"
        if e["hook"]:
            line += f"\n    Hook: {e['hook']}"
        if e["angle"]:
            line += f"\n    Angle: {e['angle']}"
        lines.append(line)
    return "\n".join(lines) or "(nothing proven yet)"


def lessons_text(theme: Theme) -> str:
    insights = theme.insights or {}
    parts = []
    for key, label in (("works", "What works"), ("avoid", "What to avoid"), ("try_next", "Worth trying")):
        if insights.get(key):
            parts.append(label + ":\n" + "\n".join(f"- {x}" for x in insights[key]))
    return "\n\n".join(parts) or "(no lessons drawn yet)"


# --- lessons --------------------------------------------------------------------------------

class Lessons(BaseModel):
    summary: str = Field(description="Two or three sentences: how the theme is doing and the clearest pattern")
    works: list[str] = Field(description="Patterns shared by the videos that did best, each naming the video "
                                         "numbers it rests on, e.g. 'A question title with a number [1][3]'")
    avoid: list[str] = Field(description="Patterns shared by the videos that did worst, with their numbers")
    try_next: list[str] = Field(description="Two to four concrete directions for the next ideas")
    confidence: str = Field(description="low, medium or high: how far the numbers support these lessons")


def _video_table(videos: list[dict]) -> str:
    lines = []
    for n, v in enumerate(videos, 1):
        views = "no statistics" if v["views"] is None else f"{v['views']:,} views, {v['vs_median']}x median"
        extra = ", ".join(f"{k} {v[k]:,}" for k in ("likes", "comments", "shares", "saves") if v[k] is not None)
        lines.append(f"[{n}] \"{v['title']}\" | {v['format']}, {v['style']}"
                     + (f", {v['seconds']} s" if v["seconds"] else "")
                     + f" | {views}" + (f" | {extra}" if extra else "")
                     + (f" | engagement {v['engagement']}%" if v["engagement"] is not None else "")
                     + (f" | posted {v['published_at'][:16].replace('T', ' ')} UTC" if v["published_at"] else "")
                     + (f"\n    Hook: {v['hook']}" if v["hook"] else "")
                     + (f"\n    Angle: {v['angle']}" if v["angle"] else ""))
    return "\n".join(lines)


def insights(theme_id: int) -> dict:
    """Ask the writing tier what the theme's numbers say, and keep the answer on the theme."""
    with session_scope() as db:
        theme = db.get(Theme, theme_id)
        table = ranked(published(db, theme_id))
        name, description = theme.name, theme.description
    if table["with_views"] < MIN_FOR_LESSONS:
        raise LookupError(f"Not enough to learn from yet: {table['with_views']} published video"
                          f"{'s' if table['with_views'] != 1 else ''} with statistics, {MIN_FOR_LESSONS} needed. "
                          "Statistics come from Buffer for posted Shorts and from YouTube for linked long videos.")
    prompt = (f"<theme>{name}</theme>\n" + (f"<about>{description}</about>\n" if description else "")
              + f"\n<videos median_views=\"{table['median_views']}\" today=\"{datetime.now(UTC).date()}\">\n"
              + _video_table(table["videos"]) + "\n</videos>")
    with llm.attribute(f"theme:{theme_id}"):
        answer: Lessons = llm.ask("theme_insights", prompts.Prompt("theme_insights"), prompt, Lessons,
                                  effort="medium")
    confidence = answer.confidence.strip().lower()
    if confidence not in ("low", "medium", "high"):
        confidence = "low"
    if table["with_views"] < 6 and confidence == "high":
        confidence = "medium"      # a handful of videos cannot support more
    result = {**answer.model_dump(), "confidence": confidence, "made_at": utcnow().isoformat(),
              "based_on": table["with_views"], "median_views": table["median_views"]}
    with session_scope() as db:
        db.get(Theme, theme_id).insights = result
    log.info("theme lessons drawn", extra={"theme_id": theme_id, "videos": table["with_views"]})
    return result
