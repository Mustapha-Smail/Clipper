"""Import videos processed by versions that kept everything in JSON files.

Those versions wrote meta.json and moments.json next to each source video and a
sidecar .json next to each clip. The files stay where they are; this only
creates the database rows that describe them.
"""

import json
from pathlib import Path

from . import config
from .db import session_scope
from .db.models import Clip, Moment, Video


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def import_legacy() -> list[str]:
    """Returns one line per video found, saying what was done with it."""
    report = []
    sources = config.sources_dir()
    for src_dir in sorted(p for p in sources.iterdir() if p.is_dir()) if sources.is_dir() else []:
        meta = _read(src_dir / "meta.json")
        if not meta:
            continue
        with session_scope() as db:
            if db.get(Video, src_dir.name) is not None:
                report.append(f"{src_dir.name}: already in the database, skipped")
                continue
            brief = src_dir / "brief.txt"
            video = Video(id=src_dir.name, url=meta.get("url"), title=meta.get("title") or src_dir.name,
                          uploader=meta.get("uploader"), duration=meta["duration"], width=meta["width"],
                          height=meta["height"], brief=brief.read_text().strip() if brief.exists() else "")
            moments = (_read(src_dir / "moments.json") or {}).get("moments", [])
            for position, item in enumerate(moments, 1):
                video.moments.append(Moment(
                    position=position, start_s=item["start"], end_s=item["end"], title=item.get("title", ""),
                    hook=item.get("hook", ""), caption=item.get("caption", ""), score=item.get("score"),
                    reason=item.get("reason", "")))
            db.add(video)
            db.flush()

            clips = 0
            for sidecar in sorted((config.default_clips_dir() / src_dir.name).glob("*.json")):
                file = sidecar.with_suffix(".mp4")
                data = _read(sidecar) or {}
                index = data.get("index") or (int(file.stem[:2]) if file.stem[:2].isdigit() else 0)
                if not file.exists() or not 1 <= index <= len(video.moments):
                    continue
                moment = video.moments[index - 1]
                if moment.clip is not None:
                    continue
                db.add(Clip(video_id=video.id, moment_id=moment.id, path=str(file),
                            status=data.get("status") if data.get("status") in ("approved", "rejected") else "pending",
                            seconds=data.get("seconds") or moment.end_s - moment.start_s,
                            size_bytes=file.stat().st_size, tracked_share=data.get("tracked_share") or 0.0,
                            captions_burned=True))
                clips += 1
            report.append(f"{src_dir.name}: imported with {len(moments)} moments and {clips} clips")
    return report
