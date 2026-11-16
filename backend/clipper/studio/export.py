"""Export of an approved video: one folder with everything YouTube Studio asks for.

The app keeps its renders inside the data folder under names only it
understands (`studio/script-4/storyboard-12/long.mp4`). Exporting copies an
approved video into the output folder (Settings, Storage), in a folder named
after it:

  <output folder>/studio/<title>-<id>/
      <title>-<id>.mp4     the video
      <title>-<id>.srt     subtitles, long videos only (Shorts have theirs burned in)
      youtube.txt          title, description and tags, once they are written
      thumbnail-A.jpg ...  long videos: the three thumbnails for YouTube's A/B test, once made
      authorship.txt       who did what, to keep with the video

Exporting again overwrites that folder. It is a copy for you to upload or sync;
nothing is posted, and the app never reads the folder back.
"""

import re
import shutil
import unicodedata
from pathlib import Path

from sqlalchemy.orm import Session

from .. import settings as settings_mod
from ..db.models import Storyboard, as_utc, utcnow
from ..settings import Settings
from . import compliance, sound


class ExportError(RuntimeError):
    """The export cannot be made; the message says what to do."""


def slug(text: str) -> str:
    """A file name any disk and any sync tool accepts: plain letters, digits and dashes."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")[:60].rstrip("-")


def folder_for(board: Storyboard, settings: Settings) -> Path:
    return settings_mod.clips_dir(settings) / "studio" / f"{slug(board.title) or board.format}-{board.id}"


def youtube_file(text: dict, thumbnails: list[dict] | None = None) -> str:
    out = (f"TITLE\n{text.get('title', '')}\n\nDESCRIPTION\n{text.get('description', '')}\n\n"
           f"TAGS\n{', '.join(text.get('tags', []))}\n")
    if thumbnails:
        titles = text.get("titles") or [text.get("title", "")]
        rows = [f"{o['key']}: thumbnail-{o['key']}.jpg ({o['angle']}), with the title: "
                + (o.get("title_text") or (titles[o["title"] - 1] if 0 < o["title"] <= len(titles)
                                           else text.get("title", "")))
                for o in thumbnails]
        out += ("\nA/B TEST (YouTube Studio on a computer: Details, Thumbnail, A/B testing)\n"
                + "\n".join(rows) + "\n")
    return out


def _copy(source: Path, target: Path) -> None:
    """Under a temporary name first, so a sync tool never picks up half a video."""
    partial = target.with_name(f".{target.name}.part")
    try:
        shutil.copyfile(source, partial)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def is_stale(board: Storyboard) -> bool:
    """The exported copy is of an earlier render."""
    made = (board.export or {}).get("rendered_at")
    return bool(board.export) and made != (as_utc(board.rendered_at).isoformat() if board.rendered_at else None)


def run(db: Session, board: Storyboard, settings: Settings) -> dict:
    """Copy the approved video and its publishing files to the output folder."""
    if not board.video_path:
        raise ExportError("Render the video and approve it first")
    if board.review != "approved":
        raise ExportError("Only an approved video can be exported: watch it and approve it first")
    video = Path(board.video_path)
    if not video.is_file():
        raise ExportError("The rendered file is no longer on disk. Render the video again.")
    folder = folder_for(board, settings)
    name = folder.name
    files = [f"{name}.mp4"]
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _copy(video, folder / files[0])
        if board.format == "long":
            files.append(f"{name}.srt")
            (folder / files[-1]).write_text(sound.srt(board.words), encoding="utf-8")
        else:
            (folder / f"{name}.srt").unlink(missing_ok=True)
        thumbs = [o for o in (board.thumbnails or {}).get("options", [])
                  if board.format == "long" and o.get("path") and Path(o["path"]).is_file()]
        for old in folder.glob("thumbnail-*.jpg"):
            old.unlink(missing_ok=True)
        for option in thumbs:
            files.append(f"thumbnail-{option['key']}.jpg")
            _copy(Path(option["path"]), folder / files[-1])
        if board.youtube:
            files.append("youtube.txt")
            (folder / "youtube.txt").write_text(youtube_file(board.youtube, thumbs), encoding="utf-8")
        files.append("authorship.txt")
        (folder / "authorship.txt").write_text(compliance.authorship_text(db, board), encoding="utf-8")
    except OSError as err:
        raise ExportError(f"Could not write to {folder}: {err.strerror or err}") from err
    board.export = {"folder": str(folder), "files": files, "at": utcnow().isoformat(),
                    "rendered_at": as_utc(board.rendered_at).isoformat() if board.rendered_at else None,
                    "size_bytes": video.stat().st_size}
    return board.export
