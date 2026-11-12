"""What YouTube already has on a topic, through the official YouTube Data API v3.

One check costs about 102 units of the default 10,000-unit daily quota: a
search (100) plus one call for video statistics and one for channel sizes.
That is roughly 95 checked ideas a day.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from .. import config

API = "https://www.googleapis.com/youtube/v3"
RESULTS = 15
TIMEOUT = 20


class YouTubeError(RuntimeError):
    pass


def _get(endpoint: str, **params) -> dict:
    """One API call. Kept separate so tests can replace it."""
    key = config.youtube_api_key()
    if not key:
        raise YouTubeError("No YouTube API key. Put YOUTUBE_API_KEY in .env and restart.")
    url = f"{API}/{endpoint}?" + urllib.parse.urlencode({**params, "key": key})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        try:
            error = json.load(err).get("error", {})
        except (ValueError, AttributeError):
            error = {}
        reasons = {e.get("reason") for e in error.get("errors", [])}
        if "quotaExceeded" in reasons:
            raise YouTubeError("The YouTube API quota for today is used up. "
                               "It resets at midnight Pacific time.") from err
        if reasons & {"keyInvalid", "badRequest"} and "API key" in error.get("message", ""):
            raise YouTubeError("YouTube rejected the API key in YOUTUBE_API_KEY.") from err
        raise YouTubeError(f"YouTube API error ({err.code}): {error.get('message') or err.reason}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise YouTubeError("Could not reach the YouTube API. Check your connection.") from err


def seconds(iso_duration: str) -> int:
    """'PT1H2M3S' -> 3723."""
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration or "")
    if not match:
        return 0
    days, hours, minutes, secs = (int(part or 0) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + secs


def research(query: str) -> dict:
    """The top results for a search, with views, length, age and channel size."""
    found = _get("search", part="snippet", q=query, type="video", maxResults=RESULTS,
                 relevanceLanguage="en", safeSearch="none")
    ids = [item["id"]["videoId"] for item in found.get("items", []) if item.get("id", {}).get("videoId")]
    videos = _get("videos", part="snippet,statistics,contentDetails", id=",".join(ids)).get("items", []) if ids else []
    channel_ids = sorted({v["snippet"]["channelId"] for v in videos})
    channels = {}
    if channel_ids:
        for item in _get("channels", part="statistics", id=",".join(channel_ids)).get("items", []):
            stats = item.get("statistics", {})
            channels[item["id"]] = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0))

    rows = []
    for v in videos:
        snippet, stats = v["snippet"], v.get("statistics", {})
        rows.append({
            "id": v["id"],
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "subscribers": channels.get(snippet["channelId"]),
            "views": int(stats.get("viewCount", 0)),
            "published": snippet.get("publishedAt", ""),
            "duration_s": seconds(v.get("contentDetails", {}).get("duration", "")),
        })
    return {"query": query, "checked_at": datetime.now(UTC).isoformat(), "videos": rows}
