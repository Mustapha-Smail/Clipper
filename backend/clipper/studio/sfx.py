"""Search, safely cache and use reusable production sound effects.

Search never makes an asset usable by itself. A later creator-facing import
step must show the landing page, download and normalise the file, and set an
approval timestamp. Only CC0 and CC BY metadata cross this boundary.
"""

import hashlib
import html
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sqlalchemy import select

from .. import config
from ..db import session_scope
from ..db.models import StoryboardSound, StudioSound, utcnow
from . import openverse, photos

OPENVERSE_AUDIO = openverse.API + "audio/"
SAFE_LICENSES = {"cc0", "by"}
MAX_COMMONS_CONCURRENCY = 3
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_DURATION_SECONDS = 12.0
DOWNLOAD_TIMEOUT = 30


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


# --- local import and creator approval -------------------------------------------------------

def _provider_url(candidate: dict) -> str:
    provider = str(candidate.get("provider") or "").strip().lower()
    provider_id = str(candidate.get("provider_id") or "").strip()
    if provider == "openverse":
        return OPENVERSE_AUDIO + urllib.parse.quote(provider_id, safe="") + "/"
    raise ValueError("Unknown sound provider")


def candidate(provider: str, provider_id: str) -> dict:
    """Fetch one candidate's metadata through an approved provider adapter."""
    if provider == "openverse":
        item = openverse.get_json(_provider_url({"provider": provider, "provider_id": provider_id}))
        normal = _openverse_item(item)
    elif provider == "wikimedia":
        if not provider_id.isdigit():
            raise ValueError("A Wikimedia page ID is required")
        data = photos._get({
            "action": "query", "pageids": provider_id, "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|Attribution|UsageTerms",
        })
        pages = data.get("query", {}).get("pages", [])
        pages = list(pages.values()) if isinstance(pages, dict) else pages
        normal = _commons_item(pages[0]) if pages else None
    else:
        raise ValueError("Unknown sound provider")
    if normal is None:
        raise ValueError("The candidate has no safe CC0 or CC BY licence")
    return normal


def _media_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not parsed.netloc:
        raise ValueError("The sound URL is not a safe HTTPS/HTTP URL")
    return value


def download(url: str, target: Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> int:
    """Download a bounded response without exposing response contents in errors/logs."""
    request = urllib.request.Request(_media_url(url), headers={"User-Agent": openverse.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, target.open("wb") as out:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise ValueError("The sound file is larger than Clipper's download limit")
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("The sound file is larger than Clipper's download limit")
                out.write(chunk)
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"The sound download failed ({err.code})") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError("The sound download could not be reached") from err
    return total


def _duration(path: Path) -> float:
    from . import voice

    return float(voice.duration_of(path))


def import_sound(db, item: dict, kind: str) -> StudioSound:
    """Cache and locally normalise one candidate; leave it unapproved until review."""
    licence = allowed_license(str(item.get("license") or ""))
    if licence not in SAFE_LICENSES:
        raise ValueError("Only CC0 and CC BY sound effects can be imported")
    provider, provider_id = str(item.get("provider") or ""), str(item.get("provider_id") or "")
    if not provider_id or provider not in {"openverse", "wikimedia"}:
        raise ValueError("A provider and provider ID are required")
    media_url = _media_url(str(item.get("media_url") or item.get("audio_url") or ""))
    landing_url = str(item.get("landing_url") or "")
    if not landing_url.startswith(("http://", "https://")):
        raise ValueError("A source landing page is required")
    kind = re.sub(r"[^a-z0-9_-]", "", kind.lower())[:24]
    if not kind:
        raise ValueError("A sound kind is required")
    prior = db.scalar(select(StudioSound).where(StudioSound.provider == provider,
                                                StudioSound.provider_id == provider_id))
    if prior is not None:
        return prior
    listed_duration = float(item.get("duration") or 0)
    if listed_duration > MAX_DURATION_SECONDS:
        raise ValueError("The sound is longer than Clipper's duration limit")
    row = StudioSound(kind=kind, name=str(item.get("title") or "Untitled")[:200], provider=provider,
                      provider_id=provider_id, upstream_id=str(item.get("upstream_id") or "")[:160],
                      creator=str(item.get("creator") or "")[:200], creator_url=str(item.get("creator_url") or ""),
                      landing_url=landing_url, license=licence,
                      license_version=str(item.get("license_version") or "")[:16],
                      license_url=str(item.get("license_url") or ""),
                      attribution=str(item.get("attribution") or "")[:1000], media_url=media_url)
    db.add(row)
    db.flush()
    root = config.studio_dir() / "sounds"
    root.mkdir(parents=True, exist_ok=True)
    descriptor, raw_name = tempfile.mkstemp(prefix=f"{row.id}-", suffix=".download", dir=root)
    os.close(descriptor)
    raw = Path(raw_name)
    normal = raw.with_suffix(".normalized.wav")
    try:
        download(media_url, raw)
        from . import sound

        sound.normalise_effect(raw, normal)
        duration = _duration(normal)
        if not duration or duration > MAX_DURATION_SECONDS:
            raise ValueError("The sound is longer than Clipper's duration limit")
        digest = hashlib.sha256(normal.read_bytes()).hexdigest()
        final = root / f"{row.id}-{digest[:16]}.wav"
        normal.replace(final)                 # atomic within the same sounds directory
        row.path, row.duration, row.checksum, row.acquired_at = str(final), duration, digest, utcnow()
        row.enabled = True
        db.flush()
        return row
    except Exception:
        normal.unlink(missing_ok=True)
        final = locals().get("final")
        if isinstance(final, Path):
            final.unlink(missing_ok=True)
        db.delete(row)
        db.flush()
        raise
    finally:
        raw.unlink(missing_ok=True)


def approve(row: StudioSound) -> StudioSound:
    """Make a locally cached candidate renderable only after creator approval."""
    if not row.path or not Path(row.path).is_file() or not row.checksum:
        raise ValueError("Import and cache the sound before approving it")
    row.approved_at, row.rejected_at, row.rejection_note, row.enabled = utcnow(), None, "", True
    return row


def reject(row: StudioSound, note: str = "") -> StudioSound:
    row.approved_at, row.rejected_at, row.rejection_note, row.enabled = None, utcnow(), note.strip()[:500], False
    return row


def usable(row: StudioSound | None) -> bool:
    return bool(row and row.enabled and row.approved_at and row.path and Path(row.path).is_file())


def credit(row: StudioSound) -> str:
    """A complete, human-readable credit; URLs are provenance, never render inputs."""
    title = row.name or "Sound effect"
    author = f" by {row.creator}" if row.creator else ""
    licence = f"{row.license.upper()}{(' ' + row.license_version) if row.license_version else ''}"
    source = f"{row.landing_url}"
    terms = f", {row.license_url}" if row.license_url else ""
    return f"Sound: {title}{author} ({licence}; {source}{terms}; trimmed/mixed by Clipper)"


def assignments(storyboard_id: int) -> list[dict]:
    """Read only approved local assignments for a render; remote URLs are ignored."""
    with session_scope() as db:
        rows = db.scalars(select(StoryboardSound).where(StoryboardSound.storyboard_id == storyboard_id)
                          .order_by(StoryboardSound.at, StoryboardSound.id)).all()
        return [{"id": row.id, "sound_id": row.sound_id, "rule": row.rule, "at": row.at, "gain": row.gain,
                 "path": row.sound.path if usable(row.sound) else "", "kind": row.sound.kind}
                for row in rows]
