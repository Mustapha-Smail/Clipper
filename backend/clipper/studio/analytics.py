"""Watch time and retention of your own videos, from the YouTube Analytics API (after Connect YouTube).

Videos are matched by the YouTube ids Clipper already knows: `storyboards.published_url` for long videos
published by hand, and the link Buffer reports for a Short it sent to YouTube (`posts.link`). A Short whose
post has no YouTube link yet cannot be matched, and the log says which. Videos that are not on the connected
channel are skipped with a note.

Calls, read with the daily pass and the `stats` job, each video at most every EVERY unless forced:
  - one Data API `videos.list` per 50 videos (length and owner),
  - one `reports.query` per 200 videos for views, watch time, average view duration and percentage,
  - one `reports.query` per video for the retention curve (the API takes a single video there).

What the API does not give, and is not faked: impressions and thumbnail click-through rate (only in the
bulk Reporting API), and the Shorts "viewed vs swiped away" share. Data arrives 2 to 3 days late.

The share still watching at the first twist and the payoff is plain arithmetic on YouTube's curve, every
step in its lines: where the sentence starts (the take's word times, through the hold check's roles) as a
fraction of the video, then a straight line between the two nearest points of the curve.
"""

import logging
import urllib.parse
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..db.models import Post, Script, Storyboard, VideoAnalytics, as_utc, record_metric_snapshot, utcnow
from ..jobs import Reporter
from . import youtube, youtube_auth

log = logging.getLogger("clipper.studio")

REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
EVERY = timedelta(hours=20)
SUMMARY = ("views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage")
FIRST_DAY = "2005-04-23"        # the first YouTube upload: a start date that covers any video's life


def _get(url: str, params: dict) -> dict:
    """One authorised call; kept separate so tests can replace it."""
    return youtube_auth.get_json(url + "?" + urllib.parse.urlencode(params), youtube_auth.access_token())


def table(answer: dict) -> list[dict]:
    """A `resultTable` as dicts, by the names in `columnHeaders`. No `rows` means no data."""
    names = [h["name"] for h in answer.get("columnHeaders", [])]
    return [dict(zip(names, row, strict=False)) for row in answer.get("rows", []) or []]


# --- matching ------------------------------------------------------------------------------------

def targets(db: Session, board_ids: set[int] | None = None) -> tuple[list[dict], list[dict]]:
    """The videos we can ask about, [{storyboard_id, youtube_id, source, title}], and those we cannot,
    [{storyboard_id, title, why}]. A storyboard may have both a linked long video and a Buffer Short."""
    found, unmatched, seen = [], [], set()
    query = select(Storyboard).where(Storyboard.published_url.is_not(None))
    if board_ids is not None:
        query = query.where(Storyboard.id.in_(board_ids))
    for board in db.scalars(query):
        vid = youtube.video_id(board.published_url or "")
        title = board.title or board.script.title
        if vid:
            found.append({"storyboard_id": board.id, "youtube_id": vid, "source": "linked", "title": title})
            seen.add((board.id, vid))
        else:
            unmatched.append({"storyboard_id": board.id, "title": title, "why": "its link is not a YouTube video"})
    query = (select(Post, Storyboard).join(Storyboard, Post.storyboard_id == Storyboard.id)
             .where(Post.kind == "storyboard", Post.service == "youtube", Post.status == "sent"))
    if board_ids is not None:
        query = query.where(Post.storyboard_id.in_(board_ids))
    for post, board in db.execute(query):
        title = board.title or board.script.title
        vid = youtube.video_id(post.link or "")
        if not vid:
            unmatched.append({"storyboard_id": board.id, "title": title,
                              "why": "Buffer has not reported its YouTube link" if not post.link
                              else "Buffer's link is not a YouTube video"})
        elif (board.id, vid) not in seen:
            found.append({"storyboard_id": board.id, "youtube_id": vid, "source": "buffer", "title": title})
            seen.add((board.id, vid))
    return found, unmatched


# --- reading -------------------------------------------------------------------------------------

def connected() -> bool:
    return youtube_auth.status()["connected"]


def refresh(rep: Reporter | None = None, force: bool = False) -> dict:
    """Read summary and retention for every matched video that is due. Never raises for Google's sake:
    a refused connection is marked for Reconnect, logged and reported; returns {read, problem}."""
    say = rep or Reporter()
    if not youtube_auth.configured() or not connected():
        state = youtube_auth.status()
        if state["needs_reconnect"]:
            say.warn(f"YouTube Analytics not read: {state['problem']}")
            return {"read": 0, "problem": "YouTube needs reconnecting (Settings)"}
        say.info("YouTube is not connected: watch time and retention are not read (Settings > Connect YouTube)")
        return {"read": 0, "problem": None}
    due = utcnow() - EVERY
    with session_scope() as db:
        found, unmatched = targets(db)
        known = {(r.storyboard_id, r.youtube_id): r.fetched_at for r in db.scalars(select(VideoAnalytics))}
    if unmatched:
        say.info(f"{len(unmatched)} video{'s' if len(unmatched) != 1 else ''} on YouTube cannot be matched: "
                 + "; ".join(f"\"{u['title']}\" ({u['why']})" for u in unmatched[:8]))
    wanted = [f for f in found if force or not known.get((f["storyboard_id"], f["youtube_id"]))
              or as_utc(known[(f["storyboard_id"], f["youtube_id"])]) < due]
    if not wanted:
        return {"read": 0, "problem": None}
    try:
        read = _read(wanted, say)
    except youtube_auth.AuthError as err:
        if connected():
            youtube_auth.mark_problem(f"{err}. Reconnect YouTube.")
        say.warn(f"YouTube Analytics not read: {err}. Reconnect YouTube on the Settings page.")
        log.warning("YouTube Analytics refused", extra={"reason": str(err)})
        return {"read": 0, "problem": "YouTube needs reconnecting (Settings)"}
    except youtube_auth.GoogleError as err:
        say.warn(f"YouTube Analytics not read: {err}")
        log.warning("YouTube Analytics failed", extra={"reason": str(err)})
        return {"read": 0, "problem": None}
    return {"read": read, "problem": None}


def _read(wanted: list[dict], say: Reporter) -> int:
    channel = youtube_auth.status()["channel_id"]
    ids = sorted({w["youtube_id"] for w in wanted})
    info: dict[str, dict] = {}
    for start in range(0, len(ids), 50):
        group = ids[start:start + 50]
        for item in _get(VIDEOS_URL, {"part": "snippet,contentDetails", "id": ",".join(group)}).get("items", []):
            info[item["id"]] = {"channel": item.get("snippet", {}).get("channelId"),
                                "published": item.get("snippet", {}).get("publishedAt"),
                                "seconds": youtube.seconds(item.get("contentDetails", {}).get("duration", ""))}
    mine = [v for v in ids if info.get(v, {}).get("channel") == channel]
    notes = {v: ("YouTube does not know this video (deleted, or private to another account)" if v not in info
                 else "it is not on the connected channel") for v in ids if v not in mine}
    if notes:
        say.info(f"Skipped {len(notes)} video{'s' if len(notes) != 1 else ''} not on the connected channel: "
                 + ", ".join(sorted(notes)))
    today = datetime.now(UTC).date()
    summary: dict[str, dict] = {}
    for start in range(0, len(mine), 200):
        group = mine[start:start + 200]
        days = [info[v]["published"][:10] for v in group if info[v].get("published")]
        first = (min(days) if days else FIRST_DAY)
        first = (datetime.fromisoformat(first).date() - timedelta(days=1)).isoformat()
        answer = _get(REPORTS_URL, {"ids": "channel==MINE", "startDate": first, "endDate": today.isoformat(),
                                    "metrics": ",".join(SUMMARY), "dimensions": "video",
                                    "filters": "video==" + ",".join(group), "sort": "-views",
                                    "maxResults": len(group)})
        summary.update({row["video"]: row for row in table(answer)})
    curves: dict[str, list] = {}
    failed = 0
    for vid in mine:
        first = (info[vid].get("published") or FIRST_DAY)[:10]
        first = (datetime.fromisoformat(first).date() - timedelta(days=1)).isoformat()
        try:
            answer = _get(REPORTS_URL, {"ids": "channel==MINE", "startDate": first, "endDate": today.isoformat(),
                                        "metrics": "audienceWatchRatio,relativeRetentionPerformance",
                                        "dimensions": "elapsedVideoTimeRatio",
                                        "filters": f"video=={vid};audienceType==ORGANIC"})
        except youtube_auth.GoogleError as err:
            failed += 1
            say.warn(f"Retention of {vid} not read: {err}")
            continue
        curves[vid] = sorted([round(float(r["elapsedVideoTimeRatio"]), 4), round(float(r["audienceWatchRatio"]), 4),
                        None if r.get("relativeRetentionPerformance") is None
                        else round(float(r["relativeRetentionPerformance"]), 4)] for r in table(answer))
    now = utcnow()
    with session_scope() as db:
        rows = {(r.storyboard_id, r.youtube_id): r for r in db.scalars(select(VideoAnalytics))}
        for w in wanted:
            vid = w["youtube_id"]
            row = rows.get((w["storyboard_id"], vid))
            if row is None:
                row = VideoAnalytics(storyboard_id=w["storyboard_id"], youtube_id=vid, source=w["source"])
                db.add(row)
            row.source, row.fetched_at = w["source"], now
            if vid not in mine:
                row.note = notes[vid]
                continue
            data = summary.get(vid, {})
            row.duration_s = float(info[vid]["seconds"]) or None
            row.views = int(data["views"]) if data.get("views") is not None else None
            row.minutes_watched = float(data["estimatedMinutesWatched"]) if data.get("estimatedMinutesWatched") \
                is not None else None
            row.average_view_s = float(data["averageViewDuration"]) if data.get("averageViewDuration") \
                is not None else None
            row.average_view_pct = float(data["averageViewPercentage"]) if data.get("averageViewPercentage") \
                is not None else None
            record_metric_snapshot(
                db, storyboard_id=w["storyboard_id"], source_key=f"youtube:{vid}", service="youtube",
                observed_at=now, views=row.views, minutes_watched=row.minutes_watched,
                average_view_s=row.average_view_s, average_view_pct=row.average_view_pct,
            )
            if vid in curves:
                row.retention = curves[vid] or None
            row.note = None if data and curves.get(vid) else (
                "YouTube has no numbers for it yet: they arrive 2 to 3 days after publishing"
                if not data else "YouTube gives no retention curve for it yet (too few views, or too recent)")
    have = sum(1 for v in mine if summary.get(v))
    say.info(f"Read YouTube Analytics for {len(mine)} video{'s' if len(mine) != 1 else ''}: {have} with numbers, "
             f"{sum(1 for v in mine if curves.get(v))} with a retention curve"
             + (f", {failed} curve{'s' if failed != 1 else ''} failed" if failed else "")
             + ". Impressions, thumbnail click-through and the Shorts swipe-away share are not in this API.")
    return len(mine)


# --- arithmetic for Learning ---------------------------------------------------------------------

def watching_at(curve: list[list], x: float) -> tuple[float, str]:
    """The audience watch ratio at fraction `x` of the video: a straight line between the two nearest points
    of YouTube's curve (the first or last point outside it). Returns (ratio, how it was worked out)."""
    points = sorted((p[0], p[1]) for p in curve)
    if x <= points[0][0]:
        return points[0][1], f"YouTube's first point, {points[0][1]:.2f} at {points[0][0]:.0%}"
    if x >= points[-1][0]:
        return points[-1][1], f"YouTube's last point, {points[-1][1]:.2f} at {points[-1][0]:.0%}"
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if x0 <= x <= x1:
            y = y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
            return y, f"between {y0:.2f} at {x0:.0%} and {y1:.2f} at {x1:.0%} on YouTube's curve"
    return points[-1][1], "YouTube's last point"           # not reached: the points cover x


COLUMNS = ("avg_viewed", "kept_at_5s", "kept_at_twist", "kept_at_payoff")


def for_video(board: Storyboard, script: Script, rows: list[VideoAnalytics]) -> dict:
    """The retention columns of one video for Learning, every number with its lines, blanks with their reason.

    {avg_viewed, kept_at_5s, kept_at_twist, kept_at_payoff, retention: {...} | None}"""
    from . import storyboard as storyboard_mod

    out: dict = dict.fromkeys(COLUMNS)
    out["retention"] = None
    usable = [r for r in rows if r.fetched_at and r.note != "it is not on the connected channel"
              and not (r.note or "").startswith("YouTube does not know")]
    if not usable:
        return out
    row = max(usable, key=lambda r: r.views or 0)
    lines: list[str] = []
    if row.views is not None:
        lines.append(f"{row.views:,} YouTube views, {round(row.minutes_watched or 0):,} minutes watched")
    if row.average_view_pct is not None:
        out["avg_viewed"] = round(row.average_view_pct, 1)
        lines.append(f"Average viewed {row.average_view_pct:.1f}% (YouTube's averageViewPercentage)"
                     + (f", {row.average_view_s:.0f} s of {row.duration_s:.0f} s on average"
                        if row.average_view_s is not None and row.duration_s else ""))
    duration = row.duration_s
    if not duration and row.average_view_s and row.average_view_pct:
        duration = row.average_view_s * 100 / row.average_view_pct
        lines.append(f"Length worked out as {row.average_view_s:.0f} s x 100 / {row.average_view_pct:.1f}% "
                     f"= {duration:.0f} s (YouTube gave none)")
    why = {}
    curve = row.retention or []
    marks, reason = ({}, "") if not curve else storyboard_mod.turn_marks(board.words or [], script.sections or [],
                                                                        script.hold)
    if not curve:
        why["kept_at_5s"] = row.note or "YouTube gives no retention curve for it yet"
    elif not duration:
        why["kept_at_5s"] = "the video's length is unknown"
    elif duration < 5:
        why["kept_at_5s"] = "the video ends before five seconds"
    else:
        x = 5 / duration
        ratio, how = watching_at(curve, x)
        out["kept_at_5s"] = round(100 * ratio)
        lines.append(f"Five-second mark: 5.0 / {duration:.0f} s = {x:.0%} in; {how}, "
                     f"so {ratio:.2f} = {100 * ratio:.0f}% still watching")
    for key, roles, name in (("kept_at_twist", ("twist", "rehook"), "first twist"),
                             ("kept_at_payoff", ("payoff",), "payoff")):
        if not curve:
            why[key] = row.note or "YouTube gives no retention curve for it yet"
            continue
        if not duration:
            why[key] = "the video's length is unknown"
            continue
        word = min((i for i, r in marks.items() if r in roles), default=None)
        if word is None:
            why[key] = reason or f"the hold check marks no {name} in this video"
            continue
        words = board.words or []
        at = float(words[word]["s"]) if word < len(words) else None
        if at is None or at > duration:
            why[key] = f"the {name} falls after the end of the video on YouTube"
            continue
        x = at / duration
        ratio, how = watching_at(curve, x)
        out[key] = round(100 * ratio)
        lines.append(f"{name.capitalize()} at {at:.1f} s (word {word + 1}): {at:.1f} / {duration:.0f} s = {x:.0%} "
                     f"in; {how}, so {ratio:.2f} = {100 * ratio:.0f}% still watching")
    if curve and max(p[1] for p in curve) > 1:
        lines.append("YouTube's watch ratio goes above 100% where people rewind or replay (Shorts loop)")
    out["retention"] = {
        "youtube_id": row.youtube_id, "source": row.source, "views": row.views,
        "minutes_watched": row.minutes_watched, "average_view_s": row.average_view_s,
        "average_view_pct": row.average_view_pct, "duration_s": duration,
        "curve": [[p[0], p[1]] for p in curve],
        "twist_at": None if out["kept_at_twist"] is None else _x(board, marks, ("twist", "rehook"), duration),
        "payoff_at": None if out["kept_at_payoff"] is None else _x(board, marks, ("payoff",), duration),
        "lines": lines, "why": why, "note": row.note,
        "fetched_at": as_utc(row.fetched_at).isoformat() if row.fetched_at else None,
    }
    return out


def _x(board: Storyboard, marks: dict[int, str], roles: tuple[str, ...], duration: float) -> float:
    word = min(i for i, r in marks.items() if r in roles)
    return round(float(board.words[word]["s"]) / duration, 4)


def last_fetch(db: Session) -> tuple[str | None, int]:
    """When analytics were last read, and for how many videos we hold numbers."""
    rows = list(db.scalars(select(VideoAnalytics)))
    times = [as_utc(r.fetched_at) for r in rows if r.fetched_at]
    return (max(times).isoformat() if times else None), sum(1 for r in rows if r.views is not None)
