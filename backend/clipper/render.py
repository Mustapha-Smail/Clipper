"""Stage 4: render one moment into a captioned vertical clip.

ffmpeg decodes the source segment to raw frames, Python reframes each frame and
draws the text, and a second ffmpeg encodes the result with the original audio.
"""

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .captions import CaptionRenderer, group_words, hook_overlay
from .config import FPS, OUT_H, OUT_W
from .ffutil import ToolError, probe, require
from .reframe import SubtitleFix, analyze, compose, usable_height
from .settings import Settings, hex_to_rgb, resolve_encoder, resolve_font


@dataclass
class RenderOptions:
    encoder: str            # "videotoolbox" or "x264"
    bitrate_mbps: int
    font_path: str
    highlight: tuple[int, int, int]
    captions_enabled: bool
    hook_seconds: float
    subtitle_mode: str = "ignore"            # cover | crop | keep | ignore
    band: tuple[float, float] | None = None  # where the source subtitles sit, if known

    def subtitle_fix(self) -> SubtitleFix | None:
        if self.band is None or self.subtitle_mode not in ("cover", "crop"):
            return None
        # Cropping only makes sense for subtitles near the bottom; otherwise it
        # would throw away most of the picture, so blur them instead.
        mode = self.subtitle_mode if self.band[0] >= 0.6 else "cover"
        return SubtitleFix(mode, self.band)

    @classmethod
    def from_settings(cls, settings: Settings, subtitle_mode: str = "ignore",
                      band: tuple[float, float] | None = None) -> "RenderOptions":
        return cls(
            subtitle_mode=subtitle_mode,
            band=band,
            encoder=resolve_encoder(settings),
            bitrate_mbps=settings.bitrate_mbps,
            font_path=resolve_font(settings),
            highlight=hex_to_rgb(settings.highlight_color),
            captions_enabled=settings.captions_enabled,
            hook_seconds=settings.hook_seconds,
        )

    def encoder_args(self) -> list[str]:
        rate = self.bitrate_mbps
        if self.encoder == "videotoolbox":
            return ["-c:v", "h264_videotoolbox", "-b:v", f"{rate}M"]
        return ["-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{rate}M",
                "-maxrate", f"{rate * 1.5:.0f}M", "-bufsize", f"{rate * 2}M"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:50] or "clip"


def clip_name(moment_id: int, title: str) -> str:
    """File name that stays unique and stable for a moment, whatever its title becomes."""
    return f"{_slug(title)}-{moment_id}"


def render_clip(source: Path, moment: dict, words: list[dict], out_path: Path,
                opts: RenderOptions, progress=None) -> dict:
    """Render `moment` from `source` to `out_path`. Returns layout statistics."""
    ffmpeg = require("ffmpeg")
    info = probe(source)
    start = float(moment["start"])
    duration = float(moment["end"]) - start
    if duration <= 0:
        raise ToolError(f"'{moment.get('title', 'Moment')}' ends before it starts")
    if start >= info.duration:
        raise ToolError(f"'{moment.get('title', 'Moment')}' starts after the end of the video")

    fix = opts.subtitle_fix()
    keep_original = opts.subtitle_mode == "keep"
    plan = analyze(source, start, duration, info.width, info.height,
                   crop_height=usable_height(info.height, fix))
    if keep_original:
        plan.track[:] = False   # always show the whole frame so the subtitles stay readable
    captions = None
    if opts.captions_enabled and not keep_original:
        captions = CaptionRenderer(group_words(words, start, start + duration),
                                   opts.font_path, opts.highlight)
    hook = hook_overlay(moment.get("hook", ""), opts.font_path) if opts.hook_seconds > 0 else None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Encode to a temporary name so a half-written file never looks like a finished clip.
    partial = out_path.with_name(out_path.stem + ".partial.mp4")
    seek = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
    decoder = subprocess.Popen(
        [ffmpeg, "-loglevel", "error", *seek, "-vf", f"fps={FPS}", "-an",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    with tempfile.TemporaryFile() as encoder_log:
        encoder = subprocess.Popen(
            [ffmpeg, "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS),
             "-i", "-", *seek,
             "-map", "0:v", "-map", "1:a?",
             *opts.encoder_args(), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k",
             "-movflags", "+faststart", "-shortest", str(partial)],
            stdin=subprocess.PIPE, stderr=encoder_log,
        )

        frame_bytes = info.width * info.height * 3
        total = max(1, int(round(duration * FPS)))
        written = 0
        try:
            while True:
                buf = decoder.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    break
                frame = np.frombuffer(buf, np.uint8).reshape(info.height, info.width, 3)
                is_track, cx = plan.at(written)
                out = compose(frame, is_track, cx, fix)
                t = written / FPS
                if captions is not None:
                    captions.draw(out, t)
                if hook is not None and t < opts.hook_seconds:
                    hook.draw(out)
                encoder.stdin.write(out.tobytes())
                written += 1
                if progress and written % FPS == 0:
                    progress(min(1.0, written / total))
        except BrokenPipeError:
            pass  # the encoder died; its log is reported below
        finally:
            decoder.stdout.close()
            decoder.wait()
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
            code = encoder.wait()
        if code != 0 or written == 0:
            encoder_log.seek(0)
            detail = encoder_log.read().decode(errors="replace")[-2000:]
            partial.unlink(missing_ok=True)
            raise ToolError(f"Rendering {out_path.name} failed.\n{detail}")

    partial.replace(out_path)
    tracked = float(plan.track.mean()) if len(plan.track) else 0.0
    return {"frames": written, "seconds": round(written / FPS, 2), "tracked_share": round(tracked, 2),
            "captions_burned": captions is not None,
            "subtitle_fix": "keep" if keep_original else (fix.mode if fix else None)}


def write_sidecar(out_path: Path, moment: dict, video: dict, stats: dict) -> None:
    """A small JSON file next to the clip, for anyone browsing the output folder.

    The database is the source of truth; this is a convenience copy.
    """
    sidecar = {
        "title": moment.get("title"),
        "hook": moment.get("hook"),
        "caption": moment.get("caption"),
        "start": moment["start"],
        "end": moment["end"],
        "source_id": video.get("id"),
        "source_url": video.get("url"),
        "source_title": video.get("title"),
        **stats,
    }
    out_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2, ensure_ascii=False))
