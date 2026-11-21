"""Search metadata for reusable production sound effects.

Search never makes an asset usable by itself. A later creator-facing import
step must show the landing page, download and normalise the file, and set an
approval timestamp. Only CC0 and CC BY metadata cross this boundary.
"""

import html
import re
import urllib.parse

from . import openverse, photos

OPENVERSE_AUDIO = openverse.API + "audio/"
SAFE_LICENSES = {"cc0", "by"}
MAX_COMMONS_CONCURRENCY = 3


def allowed_license(value: str) -> str | None:
    """Return Clipper's canonical safe licence, or None.

    Exact whitelisting matters: commercial/modification category filters can
    still contain BY-SA or legacy Sampling+ records.
    """
    compact = re.sub(r"[ _-]+", " ", (value or "").strip().lower())
    if compact in {"cc0", "cc 0", "creative commons zero"} or compact.startswith("cc0 "):
        return "cc0"
    if re.fullmatch(r"(?:cc |creative commons )?by(?: \d(?:\.\d)?)?", compact):
        return "by"
    return None


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))).strip()


def _openverse_item(item: dict) -> dict | None:
    licence = allowed_license(str(item.get("license") or ""))
    if licence not in SAFE_LICENSES:
        return None
    source = str(item.get("source") or "")
    media = str(item.get("url") or "")
    landing = str(item.get("foreign_landing_url") or "")
    if not media or not landing:
        return None
    return {
        "provider": "openverse",
        "provider_id": str(item.get("id") or ""),
        "upstream_id": str(item.get("foreign_identifier") or ""),
        "upstream_source": source,
        "title": str(item.get("title") or "Untitled")[:200],
        "creator": str(item.get("creator") or "")[:200],
        "creator_url": str(item.get("creator_url") or ""),
        "landing_url": landing,
        "media_url": media,
        "license": licence,
        "license_version": str(item.get("license_version") or "")[:16],
        "license_url": str(item.get("license_url") or ""),
        "attribution": str(item.get("attribution") or ""),
        "duration": round(float(item.get("duration") or 0) / 1000, 3),
        "filetype": str(item.get("filetype") or ""),
    }


def search_openverse(query: str, page: int = 1, limit: int = 20) -> dict:
    """Dedicated SFX candidates indexed from Freesound through Openverse.

    Openverse's Freesound ingestion does not reliably populate `category`, so
    source and licence filters are intentional and no category is sent.
    """
    params = {"q": query.strip()[:200], "source": "freesound", "license": "cc0,by",
              "page": max(1, page), "page_size": max(1, min(limit, 20)), "filter_dead": "true"}
    data = openverse.get_json(OPENVERSE_AUDIO + "?" + urllib.parse.urlencode(params))
    results = [normal for item in data.get("results", []) if (normal := _openverse_item(item)) is not None]
    return {"provider": "openverse", "count": data.get("result_count", len(results)),
            "page": data.get("page", page), "pages": data.get("page_count", 1),
            "rate_limit": openverse.rate_limit(), "results": results}


def _meta(metadata: dict, key: str) -> str:
    value = metadata.get(key) or {}
    return _plain(value.get("value") if isinstance(value, dict) else value)


def _commons_item(page: dict) -> dict | None:
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    raw_license = _meta(meta, "LicenseShortName") or _meta(meta, "UsageTerms")
    licence = allowed_license(raw_license)
    mime = str(info.get("mime") or "")
    media = str(info.get("url") or "")
    landing = str(info.get("descriptionurl") or "")
    if licence not in SAFE_LICENSES or not mime.startswith("audio/") or not media or not landing:
        return None
    title = re.sub(r"^File:", "", str(page.get("title") or "Untitled"), flags=re.I)
    return {
        "provider": "wikimedia",
        "provider_id": str(page.get("pageid") or ""),
        "upstream_id": str(page.get("pageid") or ""),
        "upstream_source": "wikimedia_audio",
        "title": title[:200],
        "creator": _meta(meta, "Artist")[:200],
        "creator_url": "",
        "landing_url": landing,
        "media_url": media,
        "license": licence,
        "license_version": (re.search(r"\b\d\.\d\b", raw_license) or [""])[0],
        "license_url": _meta(meta, "LicenseUrl"),
        "attribution": _meta(meta, "Credit") or _meta(meta, "Attribution"),
        "duration": 0.0,
        "filetype": mime,
    }


def search_commons(query: str, limit: int = 15) -> dict:
    """Original-quality Commons audio fallback, filtered locally."""
    data = photos._get({
        "action": "query", "generator": "search", "gsrsearch": f"filetype:AUDIO {query.strip()[:200]}",
        "gsrnamespace": 6, "gsrlimit": max(1, min(limit, 25)), "prop": "imageinfo",
        "iiprop": "url|mime|size|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|Attribution|UsageTerms",
    })
    pages = sorted(data.get("query", {}).get("pages", []), key=lambda value: value.get("index", 999))
    results = [normal for page in pages if (normal := _commons_item(page)) is not None]
    return {"provider": "wikimedia", "count": len(results), "page": 1, "pages": 1,
            "rate_limit": {"max_concurrency": MAX_COMMONS_CONCURRENCY}, "results": results}
