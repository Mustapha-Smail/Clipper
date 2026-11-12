"""Stage 1: bring a source video into <data>/sources/<id>/ and describe it."""

import re
import shutil
import subprocess
from collections import deque
from pathlib import Path

from .config import sources_dir
from .ffutil import ToolError, probe, require, run
from .settings import Settings

VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip()))


def source_dir(video_id: str) -> Path:
    if not SAFE_ID.match(video_id) or ".." in video_id:
        raise ToolError(f"Invalid video id '{video_id}'")
    return sources_dir() / video_id


def find_video(src_dir: Path) -> Path | None:
    for path in sorted(src_dir.glob("source.*")):
        if path.suffix.lower() in VIDEO_SUFFIXES:
            return path
    return None


def source_video(src_dir: Path) -> Path:
    path = find_video(src_dir)
    if path is None:
        raise ToolError("The source video file was deleted. Add the video again to render more clips.")
    return path


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:60] or "video"


def _describe(src_dir: Path, **meta) -> dict:
    info = probe(source_video(src_dir))
    return {**meta, "width": info.width, "height": info.height, "duration": info.duration}


def _ingest_file(path: Path, move: bool) -> dict:
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ToolError(f"Unsupported file type '{path.suffix}'. Use mp4, mov, mkv, webm or m4v.")
    video_id = _slug(path.stem)
    src_dir = sources_dir() / video_id
    src_dir.mkdir(parents=True, exist_ok=True)
    target = src_dir / f"source{path.suffix.lower()}"
    if move:
        shutil.move(str(path), target)
    elif not target.exists():
        shutil.copy2(path, target)
    return _describe(src_dir, id=video_id, url=None, title=path.stem, uploader=None)


def _download(cmd: list[str], progress, info) -> None:
    """Run yt-dlp, forwarding its percentage and keeping its last lines for errors."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: deque[str] = deque(maxlen=25)
    try:
        for line in proc.stdout:
            line = line.strip()
            match = re.match(r"PROGRESS\s+([\d.]+)%", line)
            if match:
                if progress:
                    progress(float(match.group(1)) / 100)   # may raise to cancel the job
            elif line:
                tail.append(line)
                if info and line.startswith(("[info]", "[Merger]", "WARNING", "ERROR")):
                    info(line)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode != 0:
        raise ToolError("Download failed.\n" + "\n".join(tail))


def _ingest_url(url: str, settings: Settings, progress, info) -> dict:
    ytdlp = require("yt-dlp")
    common = ["--no-playlist"]
    if settings.cookies_file:
        common += ["--cookies", str(Path(settings.cookies_file).expanduser())]
    described = run([ytdlp, *common, "--print", "%(id)s\t%(title)s\t%(uploader)s\t%(webpage_url)s", url])
    video_id, title, uploader, page = (described.strip().splitlines()[-1].split("\t") + [""] * 4)[:4]
    if not SAFE_ID.match(video_id):
        video_id = _slug(video_id)
    src_dir = sources_dir() / video_id
    src_dir.mkdir(parents=True, exist_ok=True)

    if find_video(src_dir) is None:
        height = settings.max_height
        _download([
            ytdlp, *common,
            # 1080p is the most the vertical crop can use; prefer h264 for fast decoding.
            "-f", f"bv*[height<={height}]+ba/b[height<={height}]/b",
            "-S", "vcodec:h264,res,acodec:m4a",
            "--merge-output-format", "mp4",
            "--newline", "--progress-template", "download:PROGRESS %(progress._percent_str)s",
            "-o", str(src_dir / "source.%(ext)s"),
            url,
        ], progress, info)
    elif info:
        info("The video file is already on disk; skipping the download")

    return _describe(src_dir, id=video_id, url=page or url, title=title or video_id,
                     uploader=None if uploader in ("", "NA") else uploader)


def ingest(src: str, settings: Settings, progress=None, info=None, move: bool = False) -> dict:
    """Download a URL (or copy a local file). Returns id, url, title, uploader, size, duration."""
    src = src.strip()
    if is_url(src):
        return _ingest_url(src, settings, progress, info)
    path = Path(src).expanduser()
    if path.is_file():
        return _ingest_file(path, move)
    raise ToolError(f"'{src}' is neither a link nor a file this app can read")

