"""Small helpers around the ffmpeg, ffprobe and yt-dlp binaries."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ToolError(RuntimeError):
    pass


def require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ToolError(f"{tool} is not installed. Run: brew install {tool}")
    return path


def run(cmd: list[str], capture: bool = True) -> str:
    """Run a command; raise ToolError with its stderr if it fails."""
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[-2000:] if capture else ""
        raise ToolError(f"{Path(cmd[0]).name} failed ({proc.returncode})\n{detail}")
    return proc.stdout if capture else ""


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


def probe(path: Path) -> VideoInfo:
    out = run([
        require("ffprobe"), "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ])
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ToolError(f"No video stream in {path}")
    width, height = int(video["width"]), int(video["height"])
    # Phone footage is often stored sideways with a rotation flag.
    rotation = 0
    for side in video.get("side_data_list", []):
        rotation = int(side.get("rotation", rotation))
    if abs(rotation) in (90, 270):
        width, height = height, width
    return VideoInfo(
        width=width,
        height=height,
        duration=float(data["format"]["duration"]),
        has_audio=any(s["codec_type"] == "audio" for s in data["streams"]),
    )
