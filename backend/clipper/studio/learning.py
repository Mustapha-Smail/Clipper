"""What a theme has learnt from its own videos: statistics, the ranking, and the lessons drawn.

Statistics come from Buffer for the Shorts it posted (`posting.sync_metrics`) and
from YouTube's Data API for long videos you published by hand and linked
(`storyboards.published_url`). Never from scraping. Once YouTube is connected
(Settings), watch time and retention come from YouTube Analytics (`analytics.py`).

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
from ..db.models import (
    Idea,
    Post,
    Script,
    Storyboard,
    Theme,
    VideoAnalytics,
    VideoMetricSnapshot,
    as_utc,
    record_metric_snapshot,
    utcnow,
)
from ..jobs import Reporter
from . import analytics, youtube
from . import ideas as ideas_mod

log = logging.getLogger("clipper.studio")

COUNTED = ("views", "likes", "comments", "shares", "saves")
STATS_EVERY = timedelta(hours=20)       # a long video's YouTube statistics are read at most this often
MIN_FOR_LESSONS = 3                     # videos with views before the lessons can say anything
NETWORKS = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
DAY_SEVEN = timedelta(days=7)
DAY_SEVEN_TOLERANCE = timedelta(days=2)


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
                now = utcnow()
                db.get(Storyboard, board_id).stats = {**found[vid], "at": now.isoformat(), "source": "youtube"}
                values = found[vid]
                record_metric_snapshot(db, storyboard_id=board_id, source_key=f"youtube:{vid}", service="youtube",
                                       observed_at=now, views=values.get("views"), likes=values.get("likes"),
                                       comments=values.get("comments"))
    if rep:
        rep.info(f"Read YouTube statistics of {len(found)} video{'s' if len(found) != 1 else ''} published by hand")
    return len(found)


def refresh(rep: Reporter | None = None, force: bool = False) -> str:
    """Every statistic we can read: Buffer posts, then videos published by hand. Returns the job's message."""
    from .. import posting

    posts = posting.sync_metrics(rep, force=force)
    linked = refresh_youtube(rep, force=force)
    watched = analytics.refresh(rep, force=force)
    message = f"Statistics read for {posts} post{'s' if posts != 1 else ''} and {linked} linked video" \
              + ("s" if linked != 1 else "")
    if watched["read"]:
        message += f"; watch time and retention for {watched['read']} on YouTube"
    if watched["problem"]:
        message += f"; {watched['problem']}"
    return message


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


def _observation_value(row, name: str):
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


def seven_day_velocity(snapshots: list, starts: dict[str, datetime]) -> dict:
    """Seven-day view gain and daily velocity from real observations.

    Each source keeps its own publication clock and baseline. The endpoint is
    the closest observation *not after* day seven, within a two-day tolerance.
    Nothing is extrapolated from today's lifetime total.
    """
    grouped: dict[str, list] = {}
    for row in snapshots:
        grouped.setdefault(str(_observation_value(row, "source_key")), []).append(row)
    source_keys = sorted(set(grouped) | set(starts))
    if not source_keys:
        return {"status": "unavailable", "reason": "no daily observations", "coverage": "0/0",
                "sources": [], "cross_platform": None}

    sources = []
    for source_key in source_keys:
        rows = sorted(grouped.get(source_key, []), key=lambda row: as_utc(_observation_value(row, "observed_at")))
        service = str(_observation_value(rows[0], "service")) if rows else source_key.split(":", 1)[0]
        start = as_utc(starts.get(source_key))
        base = {"source_key": source_key, "service": service, "name": NETWORKS.get(service, service.title())}
        if start is None:
            sources.append({**base, "status": "unavailable", "reason": "publication time is unknown"})
            continue
        target = start + DAY_SEVEN
        valid = [row for row in rows if _observation_value(row, "views") is not None
                 and as_utc(_observation_value(row, "observed_at")) >= start]
        if not valid:
            sources.append({**base, "status": "unavailable", "reason": "no view observation after publication"})
            continue
        latest = max(as_utc(_observation_value(row, "observed_at")) for row in valid)
        if latest < target:
            elapsed = (latest - start).total_seconds() / 86400
            sources.append({**base, "status": "unavailable",
                            "reason": f"not matured: latest observation is {elapsed:.1f} days after publication"})
            continue
        baseline = min(valid, key=lambda row: as_utc(_observation_value(row, "observed_at")))
        endpoint_candidates = [row for row in valid
                               if target - DAY_SEVEN_TOLERANCE <= as_utc(_observation_value(row, "observed_at"))
                               <= target]
        if not endpoint_candidates:
            sources.append({**base, "status": "unavailable",
                            "reason": "no observation from day 5 through day 7"})
            continue
        endpoint = max(endpoint_candidates, key=lambda row: as_utc(_observation_value(row, "observed_at")))
        baseline_at = as_utc(_observation_value(baseline, "observed_at"))
        endpoint_at = as_utc(_observation_value(endpoint, "observed_at"))
        elapsed_hours = (endpoint_at - baseline_at).total_seconds() / 3600
        if elapsed_hours <= 0:
            sources.append({**base, "status": "unavailable",
                            "reason": "baseline and day-seven point are the same observation"})
            continue
        baseline_views = int(_observation_value(baseline, "views"))
        endpoint_views = int(_observation_value(endpoint, "views"))
        gained = endpoint_views - baseline_views
        sources.append({
            **base,
            "status": "available",
            "baseline_at": baseline_at.isoformat(),
            "baseline_views": baseline_views,
            "endpoint_at": endpoint_at.isoformat(),
            "endpoint_views": endpoint_views,
            "elapsed_hours": round(elapsed_hours, 1),
            "elapsed_days": round(elapsed_hours / 24, 2),
            "views_gained": gained,
            "views_per_day": round(gained / (elapsed_hours / 24), 1),
            "corrected": gained < 0,
        })
    available = [source for source in sources if source["status"] == "available"]
    cross = None if not available else {
        "label": "Cross-platform sum of source-level seven-day windows",
        "source_count": len(available),
        "views_gained": sum(source["views_gained"] for source in available),
        "views_per_day": round(sum(source["views_per_day"] for source in available), 1),
    }
    return {
        "status": "available" if available else "unavailable",
        "reason": None if available else "no source has both a baseline and a day-five-to-seven observation",
        "coverage": f"{len(available)}/{len(sources)}",
        "sources": sources,
        "cross_platform": cross,
    }


def _thumbnail_evidence(board: Storyboard) -> tuple[str | None, str | None]:
    """The creator-selected/manual winner angle; never infer one from views."""
    stored = board.thumbnails or {}
    key = stored.get("winner_key") or stored.get("selected_key")
    if not key:
        return None, None
    option = next((item for item in stored.get("options", []) if item.get("key") == key), None)
    if not option:
        return None, None
    angle = "contrast" if option.get("angle") == "stakes" else option.get("angle")
    return angle, "manual_winner" if stored.get("winner_key") == key else "creator_selected"


CRAFT = ("hold_score", "first_twist", "longest_gap", "opening", "wpm", "story_template", "hook_shape",
         "starter", "riser", "first_visual_event")


def craft(board: Storyboard, script: Script) -> dict:
    """How the video was built, next to its numbers: the hold check (score, seconds to the first twist, longest
    gap, the roles of its first three sentences) and the take's measured pace. Blank (None) when unknown: the
    check is missing, the text changed since it was made or since the take was spoken, or the video tells only
    part of its script (a Short cut from a long one)."""
    from . import hold

    out = dict.fromkeys(CRAFT)
    out["look"] = board.look
    meta = board.render_meta or {}
    if meta.get("version") == 1:
        out.update(hold_score=meta.get("hold_score"), first_twist=meta.get("first_twist_s"),
                   longest_gap=meta.get("longest_gap_s"), opening=meta.get("hook_shape"),
                   wpm=meta.get("wpm"), story_template=meta.get("story_template"),
                   hook_shape=meta.get("hook_shape"), starter=meta.get("starter"), riser=meta.get("riser"),
                   look=meta.get("look") or board.look, first_visual_event=meta.get("first_visual_event_s"))
        return out
    take = board.take
    if take is not None and take.alignment:
        out["wpm"] = take.alignment.get("wpm")
    if board.format != script.video or not script.hold:
        return out
    if take is not None and take.script_hash and take.script_hash != script.hold.get("hash"):
        return out
    result = hold.score(script.sections or [], script.hold, script.video)
    if not result or not result.get("current"):
        return out
    stats = result["stats"]
    roles = [s["role"] or "fact" for s in result["sentences"][:3]]
    out.update(hold_score=result["score"], first_twist=stats["first_twist"], longest_gap=stats["longest_gap"],
               opening=" > ".join(roles))
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
    ideas = {i.id: i for i in db.scalars(select(Idea).where(Idea.theme_id == theme_id))}
    watched: dict[int, list[VideoAnalytics]] = {}
    for row in db.scalars(select(VideoAnalytics).where(VideoAnalytics.storyboard_id.in_(ids))):
        watched.setdefault(row.storyboard_id, []).append(row)
    snapshots: dict[int, list[VideoMetricSnapshot]] = {}
    for row in db.scalars(select(VideoMetricSnapshot).where(VideoMetricSnapshot.storyboard_id.in_(ids))):
        snapshots.setdefault(row.storyboard_id, []).append(row)
    rows = []
    for board, script in boards:
        mine = posts.get(board.id, [])
        if not mine and not board.published_at:
            continue
        idea = ideas.get(script.idea_id) if script.idea_id else None
        root_idea_id = idea.id if idea else None
        seen_lineage: set[int] = set()
        while root_idea_id and root_idea_id not in seen_lineage:
            seen_lineage.add(root_idea_id)
            parent = ideas.get(root_idea_id)
            if parent is None or parent.parent_idea_id is None:
                break
            root_idea_id = parent.parent_idea_id
        sent = [as_utc(p.sent_at) for p in mine if p.sent_at]
        when = min(sent) if sent else as_utc(board.published_at)
        starts = {f"buffer:{post.buffer_id}": as_utc(post.sent_at) for post in mine if post.buffer_id and post.sent_at}
        for snapshot in snapshots.get(board.id, []):
            if snapshot.service == "youtube" and board.published_at:
                starts.setdefault(snapshot.source_key, as_utc(board.published_at))
        meta = board.render_meta or {}
        rendered = meta.get("version") == 1
        thumbnail_angle, thumbnail_evidence = _thumbnail_evidence(board)
        rows.append({
            "storyboard_id": board.id, "script_id": script.id, "idea_id": idea.id if idea else None,
            "parent_idea_id": idea.parent_idea_id if idea else None,
            "root_idea_id": root_idea_id,
            "lineage_kind": idea.lineage_kind if idea else None,
            "title": board.title or script.title, "script_title": script.title,
            "hook": idea.hook if idea else "", "angle": idea.angle if idea else "",
            "format": board.format, "style": board.style, "look": board.look,
            # Experimental dimensions are intentionally render-time facts.
            # Missing old snapshots stay unknown instead of reading mutable live fields.
            "form": meta.get("form") if rendered else None,
            "story_shape": meta.get("story_shape") if rendered else None,
            "thumbnail_angle": thumbnail_angle,
            "thumbnail_evidence": thumbnail_evidence,
            "seconds": round(board.end - board.start) if board.end else None,
            "published_at": when.isoformat() if when else None,
            "published_url": board.published_url,
            "seven_day_velocity": seven_day_velocity(snapshots.get(board.id, []), starts),
            **_numbers(board, mine),
            **craft(board, script),
            **analytics.for_video(board, script, watched.get(board.id, [])),
        })
    return rows


def ranked(rows: list[dict]) -> dict:
    """Best first by views, compared with its own rendered-form median."""
    with_views = [r for r in rows if r["views"] is not None]
    median = statistics.median([r["views"] for r in with_views]) if with_views else None
    cohort_values: dict[str, list[int]] = {}
    for row in with_views:
        cohort_values.setdefault(row.get("form") or "unknown", []).append(row["views"])
    cohort_medians = {form: statistics.median(values) for form, values in cohort_values.items()}
    for row in rows:
        cohort = row.get("form") or "unknown"
        own_median = cohort_medians.get(cohort)
        row["cohort_median_views"] = int(own_median) if own_median else None
        if row["views"] is None or not own_median:
            row["vs_median"] = None
            row["lines"] = ["No statistics yet"]
            continue
        row["vs_median"] = round(row["views"] / own_median, 2)
        median_label = "theme's" if cohort == "unknown" else cohort.replace("_", " ")
        lines = [f"{row['views']:,} views across {len([n for n in row['networks'] if n['views'] is not None])} "
                 "network(s)", f"{row['vs_median']}x the {median_label} median of {int(own_median):,}"]
        if row["engagement"] is not None:
            lines.append(f"{row['engagement']}% engagement (likes, comments, shares and saves per view)")
        row["lines"] = lines
    order = sorted(rows, key=lambda r: (r["views"] is None, -(r["views"] or 0)))
    return {"videos": order, "median_views": int(median) if median else None,
            "cohort_medians": [{"form": form, "median_views": int(value),
                                "with_views": len(cohort_values[form])}
                               for form, value in sorted(cohort_medians.items())],
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


def _craft_text(v: dict) -> str:
    """The craft columns of one video for the table; 'not measured' rather than a guess when blank."""
    if v.get("hold_score") is None:
        hold_part = "hold not measured"
    else:
        twist = "none" if v["first_twist"] is None else f"{v['first_twist']} s"
        gap = "n/a" if v["longest_gap"] is None else f"{v['longest_gap']} s"
        hold_part = (f"hold {v['hold_score']}/100, first twist {twist}, longest gap {gap}, "
                     f"opens {v['opening']}")
    pace = f"pace {v['wpm']} wpm (measured)" if v.get("wpm") else "pace not measured"
    rendered = [f"look {v['look']}"]
    if v.get("story_template"):
        rendered.append(f"template {v['story_template']}")
    if v.get("hook_shape"):
        rendered.append(f"hook {v['hook_shape']}")
    for key in ("starter", "riser"):
        rendered.append(f"{key} {'on' if v[key] else 'off'}" if v.get(key) is not None else f"{key} not measured")
    rendered.append("first visual not measured" if v.get("first_visual_event") is None
                    else f"first visual {v['first_visual_event']} s")
    return f"\n    Craft: {hold_part}; {pace}; " + ", ".join(rendered)


def _watch_text(v: dict) -> str:
    """The YouTube Analytics columns of one video; 'not measured' rather than a guess when blank."""
    if (v.get("avg_viewed") is None and v.get("kept_at_5s") is None and v.get("kept_at_twist") is None
            and v.get("kept_at_payoff") is None):
        return "\n    Watch: not measured"
    parts = ["average viewed not measured" if v.get("avg_viewed") is None else f"average viewed {v['avg_viewed']}%"]
    for key, name in (("kept_at_5s", "at five seconds"), ("kept_at_twist", "at the first twist"),
                      ("kept_at_payoff", "at the payoff")):
        parts.append(f"{name} not measured" if v.get(key) is None else f"{v[key]}% still watching {name}")
    return "\n    Watch: " + ", ".join(parts)


COMPARISON_FIELDS = ("story_template", "hook_shape", "starter", "riser", "look", "first_visual_event", "wpm")
COMPARISON_OUTCOMES = (("kept_at_5s", "five-second retention"), ("avg_viewed", "average viewed"))


def _duration_band(seconds: int | None, fmt: str) -> str:
    if seconds is None:
        return "duration unknown"
    if fmt == "short":
        return "up to 30 s" if seconds <= 30 else "31–60 s" if seconds <= 60 else "over 60 s"
    return "up to 5 min" if seconds <= 300 else "5–10 min" if seconds <= 600 else "over 10 min"


def _comparison_group(row: dict, field: str) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if field in ("starter", "riser"):
        return "on" if value else "off"
    if field == "first_visual_event":
        return "by 1.0 s" if value <= 1 else "after 1.0 s"
    if field == "wpm":
        return "under 145 wpm" if value < 145 else "145–165 wpm" if value <= 165 else "over 165 wpm"
    return str(value)


def craft_comparisons(videos: list[dict]) -> list[str]:
    """Code-counted craft comparisons safe to show the model.

    Each comparison stays within one rendered form, story shape and, only when
    durations differ materially, one duration band. It needs at least two
    groups of three and reports median plus range.
    """
    material: dict[str, bool] = {}
    for form in {v.get("form") or v.get("format") for v in videos}:
        lengths = [v["seconds"] for v in videos if (v.get("form") or v.get("format")) == form and v.get("seconds")]
        short_form = form in {"short", "micro_short"}
        material[form] = bool(len(lengths) > 1 and max(lengths) - min(lengths) >= (15 if short_form else 120)
                              and max(lengths) >= min(lengths) * 1.35)
    strata: dict[tuple[str, str, str], list[dict]] = {}
    for video in videos:
        form = video.get("form") or video.get("format")
        shape = video.get("story_shape") or "shape unknown"
        fmt = "short" if form in {"short", "micro_short"} else "long"
        band = _duration_band(video.get("seconds"), fmt) if material.get(form, False) else "similar duration"
        strata.setdefault((form, shape, band), []).append(video)

    lines: list[str] = []
    for (form, shape, band), rows in sorted(strata.items()):
        for field in COMPARISON_FIELDS:
            for outcome, outcome_label in COMPARISON_OUTCOMES:
                groups: dict[str, list[float]] = {}
                for row in rows:
                    # 3D has one beta, code-owned look and is not comparable to the established drawing looks.
                    # Keep all other craft evidence, but exclude it from visual-style conclusions for now.
                    if field == "look" and row.get("style") == "3d":
                        continue
                    group, value = _comparison_group(row, field), row.get(outcome)
                    if group is not None and value is not None:
                        groups.setdefault(group, []).append(float(value))
                eligible = {name: values for name, values in groups.items() if len(values) >= 3}
                if len(eligible) < 2 or sum(map(len, eligible.values())) < 6:
                    continue
                parts = []
                for name, values in sorted(eligible.items()):
                    median = statistics.median(values)
                    parts.append(f"{name}: n={len(values)}, median {median:g}%, range {min(values):g}–{max(values):g}%")
                scope = f"{form}, {band}" if band != "similar duration" else f"{form}, similar durations"
                if shape != "shape unknown":
                    scope += f", {shape} shape"
                lines.append(f"{scope} — {field} and {outcome_label}: " + "; ".join(parts))
                break
    return lines or ["not enough comparable videos (need at least 6 in the same format and duration group, "
                     "story shape and duration group, with at least 3 videos in each craft group)"]


def _velocity_value(video: dict) -> float | None:
    velocity = video.get("seven_day_velocity") or {}
    cross = velocity.get("cross_platform") or {}
    return cross.get("views_per_day") if velocity.get("status") == "available" else None


def cohort_breakdown(videos: list[dict]) -> dict[str, list[dict]]:
    """Descriptive form/shape/thumbnail groups, never causal claims."""
    dimensions = {
        "form": lambda video: video.get("form") or "unknown",
        "story_shape": lambda video: video.get("story_shape"),
        "thumbnail_angle": lambda video: video.get("thumbnail_angle"),
    }
    output: dict[str, list[dict]] = {}
    for dimension, getter in dimensions.items():
        groups: dict[str, list[dict]] = {}
        for video in videos:
            value = getter(video)
            if value:
                groups.setdefault(str(value), []).append(video)
        summaries = []
        for value, rows in sorted(groups.items()):
            views = [row["views"] for row in rows if row.get("views") is not None]
            velocities = [number for row in rows if (number := _velocity_value(row)) is not None]
            summaries.append({
                "value": value,
                "videos": len(rows),
                "with_views": len(views),
                "median_views": int(statistics.median(views)) if views else None,
                "views_range": [min(views), max(views)] if views else None,
                "with_mature_velocity": len(velocities),
                "median_views_per_day": round(statistics.median(velocities), 1) if velocities else None,
                "velocity_range": [round(min(velocities), 1), round(max(velocities), 1)] if velocities else None,
            })
        output[dimension] = summaries
    return output


def experiment_comparisons(videos: list[dict]) -> list[str]:
    """Underpowered shape/thumbnail comparisons stay unavailable.

    Form is a stratum, never a treatment: longs, Shorts and micro-Shorts cannot
    enter the same comparison. Thumbnail outcomes are creator-entered evidence;
    no CTR or swipe-away value is invented.
    """
    lines: list[str] = []
    for form in sorted({video.get("form") for video in videos if video.get("form")}):
        same_form = [video for video in videos if video.get("form") == form]
        for dimension, label in (("story_shape", "story shape"), ("thumbnail_angle", "manual thumbnail angle")):
            groups: dict[str, list[float]] = {}
            for video in same_form:
                value, outcome = video.get(dimension), _velocity_value(video)
                if value and outcome is not None:
                    groups.setdefault(str(value), []).append(float(outcome))
            eligible = {name: values for name, values in groups.items() if len(values) >= 3}
            if len(eligible) < 2 or sum(len(values) for values in eligible.values()) < 6:
                continue
            parts = [f"{name}: n={len(values)}, median {statistics.median(values):g} views/day, "
                     f"range {min(values):g}–{max(values):g}"
                     for name, values in sorted(eligible.items())]
            lines.append(f"{form}, comparable mature seven-day windows — {label} was associated with: "
                         + "; ".join(parts))
    return lines or ["not enough comparable videos (need at least 6 mature videos in one form, with at least "
                     "3 in each story-shape or creator-recorded thumbnail group)"]


def lineage_breakdown(videos: list[dict]) -> list[dict]:
    groups: dict[int, list[dict]] = {}
    for video in videos:
        if video.get("root_idea_id") is not None:
            groups.setdefault(video["root_idea_id"], []).append(video)
    return [{
        "root_idea_id": root,
        "videos": [{"storyboard_id": video["storyboard_id"], "idea_id": video.get("idea_id"),
                    "parent_idea_id": video.get("parent_idea_id"), "lineage_kind": video.get("lineage_kind"),
                    "form": video.get("form"), "title": video["title"], "views": video.get("views"),
                    "seven_day_velocity": video.get("seven_day_velocity")}
                   for video in sorted(rows, key=lambda item: (item.get("published_at") or "", item["storyboard_id"]))],
    } for root, rows in sorted(groups.items()) if len(rows) > 1]


def experiments(videos: list[dict]) -> dict:
    return {"cohorts": cohort_breakdown(videos), "comparisons": experiment_comparisons(videos),
            "lineages": lineage_breakdown(videos),
            "note": "Descriptive associations only; unavailable CTR and swipe-away metrics are never inferred."}


def _video_table(videos: list[dict]) -> str:
    lines = []
    for n, v in enumerate(videos, 1):
        views = "no statistics" if v["views"] is None else f"{v['views']:,} views, {v['vs_median']}x median"
        extra = ", ".join(f"{k} {v[k]:,}" for k in ("likes", "comments", "shares", "saves") if v[k] is not None)
        form = v.get("form") or "render form unknown"
        shape = v.get("story_shape") or "story shape unknown"
        lines.append(f"[{n}] \"{v['title']}\" | {form}, {shape}, {v['style']}"
                     + (f", {v['seconds']} s" if v["seconds"] else "")
                     + f" | {views}" + (f" | {extra}" if extra else "")
                     + (f" | engagement {v['engagement']}%" if v["engagement"] is not None else "")
                     + (f" | posted {v['published_at'][:16].replace('T', ' ')} UTC" if v["published_at"] else "")
                     + _craft_text(v)
                     + _watch_text(v)
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
              + _video_table(table["videos"]) + "\n</videos>\n\n<craft_comparisons>\n"
              + "\n".join(craft_comparisons(table["videos"])) + "\n</craft_comparisons>")
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
