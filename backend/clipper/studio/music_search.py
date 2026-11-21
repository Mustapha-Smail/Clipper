"""Find background music through Openverse, the open-licence media search.

Openverse indexes Jamendo, Freesound, ccMixter, Wikimedia and others, and its
public API needs no key. Only licences that are safe in a monetised video are
searched: CC0 and public domain (no conditions) and CC BY (credit the creator).
Non-commercial (NC) licences are excluded, and so are share-alike (SA, which
would bind the whole video to the same licence) and no-derivatives (ND, since
looping and fading a track is an adaptation).

The YouTube Audio Library has no API; download from it in YouTube Studio and
upload the file instead.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import openverse

API = openverse.API + "audio/"
SAFE_LICENSES = "cc0,pdm,by"
MAX_BYTES = 60 * 1024 * 1024
HEADERS = {"User-Agent": "clipper-studio/1.0 (self-hosted video tool)"}


def _get_json(url: str) -> dict:
    """Compatibility seam kept for existing tests and prompt/API callers."""
    return openverse.get_json(url)


def _result(item: dict) -> dict:
    return {
        "id": item["id"], "title": item.get("title") or "Untitled", "creator": item.get("creator") or "",
        "license": item.get("license") or "", "license_version": item.get("license_version") or "",
        "license_url": item.get("license_url") or "", "source": item.get("source") or "",
        "landing_url": item.get("foreign_landing_url") or "", "audio_url": item.get("url") or "",
        "duration": round((item.get("duration") or 0) / 1000, 1), "genres": item.get("genres") or [],
        "attribution": item.get("attribution") or "",
    }


def search(query: str, length: str = "", page: int = 1) -> dict:
    """Music matching `query`, under licences that are safe for monetised videos."""
    params = {"q": query[:200], "license": SAFE_LICENSES, "category": "music", "page_size": 20, "page": page,
              "filter_dead": "true"}
    if length in ("short", "medium", "long"):
        params["length"] = length
    data = _get_json(API + "?" + urllib.parse.urlencode(params))
    return {"count": data.get("result_count", 0), "page": data.get("page", page),
            "pages": data.get("page_count", 1), "results": [_result(i) for i in data.get("results", [])]}


def track(openverse_id: str) -> dict:
    if not openverse_id.replace("-", "").isalnum():
        raise ValueError("Not an Openverse id")
    item = _result(_get_json(f"{API}{openverse_id}/"))
    if item["license"] not in SAFE_LICENSES.split(","):
        raise ValueError(f"This track's licence ({item['license']}) is not safe for a monetised video")
    return item


def download(url: str, target) -> None:
    """Fetch the audio file, refusing anything implausibly large."""
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
            done = 0
            while block := response.read(1 << 20):
                done += len(block)
                if done > MAX_BYTES:
                    raise RuntimeError("The audio file is larger than 60 MB; skipped")
                out.write(block)
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError("Could not download the track from its source. Try another one.") from err


def credit(item: dict) -> str:
    """The line to put in the video description. CC0 and public domain need none, but a thank-you is kind."""
    if item["license"] == "by":
        return item["attribution"] or (f"\"{item['title']}\" by {item['creator']}, licensed under CC BY "
                                       f"{item['license_version']} ({item['license_url']})")
    return f"Music: \"{item['title']}\" by {item['creator']} ({'CC0' if item['license'] == 'cc0' else 'public domain'})"


# --- candidates for a video ----------------------------------------------------------------

_cache_lock = threading.Lock()


def _features_cache():
    from .. import config
    return config.data_dir() / "cache" / "openverse-features.json"


def measure(item: dict) -> dict:
    """Measure an online track from 40 seconds read straight from its source; cached by Openverse id."""
    from . import sound

    path = _features_cache()
    with _cache_lock:
        cache = json.loads(path.read_text()) if path.is_file() else {}
    if item["id"] in cache:
        return cache[item["id"]]
    features = sound.describe_track(item["audio_url"], seconds=40) if item["audio_url"] else {}
    with _cache_lock:
        cache = json.loads(path.read_text()) if path.is_file() else {}
        cache[item["id"]] = features
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache))
    return features


def candidates(queries: list[str], skip_urls: set[str], length: str = "", limit: int = 12) -> list[dict]:
    """Online tracks for the searches, without the ones already in the library, measured."""
    from concurrent.futures import ThreadPoolExecutor

    found: dict[str, dict] = {}
    # Openverse matches titles and tags, not moods: plain genre or instrument words work best.
    # When the given searches find little, a few safe genres for a background bed fill in.
    searches = [q for q in queries[:3] if q.strip()] + ["ambient", "piano", "acoustic"]
    for query in searches:
        if len(found) >= limit:
            break
        try:
            results = search(query, length)["results"]
        except RuntimeError:
            continue
        for item in results[:6]:
            if item["id"] not in found and item["landing_url"] not in skip_urls and item["duration"] >= 30:
                found[item["id"]] = {**item, "query": query}
    items = list(found.values())[:limit]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for item, features in zip(items, pool.map(measure, items), strict=True):
            item["features"] = features
    return items
