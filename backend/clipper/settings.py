"""User settings: validated by this model, stored in the database, edited in the UI."""

import platform
import re
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import config
from .db import session_scope
from .db.models import Setting

MAC_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
]
KEY = "app"


class Settings(BaseModel):
    # Storage
    output_dir: str = ""                      # empty = <data folder>/clips
    delete_source_after_render: bool = False
    # Studio: a rendered video that is not approved within this many days is deleted to
    # save space (its plan is kept, so it can be rendered again). 0 = keep renders forever.
    unapproved_keep_days: int = Field(30, ge=0, le=365)

    # Your channel: used to rank campaigns
    theme: str = Field("Tech, AI, SaaS, and founder or business podcasts", max_length=300)
    # Share of your viewers in the US, UK, Canada and Australia; None = not known yet.
    audience_tier1_share: int | None = Field(None, ge=0, le=100)
    # Where you post. A campaign is ranked on the best rate it pays on one of these.
    platforms: list[Literal["tiktok", "instagram", "youtube", "x", "facebook"]] = ["tiktok", "instagram", "youtube"]
    # Campaign scout (scout.py): how often the worker reads public listings by itself
    # (0 = only when you press the button), and how many listing pages one run reads.
    scout_every_hours: Literal[0, 6, 12, 24, 72, 168] = 0
    scout_max_pages: int = Field(15, ge=1, le=30)

    # Studio: the animated channel. Ideas are generated and judged against this theme.
    studio_theme: str = Field("How everyday things secretly work", max_length=300)
    # How carefully Claude draws a new prop: careful (more detail, about $0.20 each) or quick (about $0.06).
    prop_drawing: Literal["careful", "quick"] = "careful"
    # Who the videos are for: ideas, research and scripts are written for them (see studio/plain.py).
    audience: Literal["everyone", "curious", "enthusiasts"] = "everyone"
    # AI voice. Kokoro and Chatterbox run on this machine for free (Chatterbox is expressive and slower);
    # ElevenLabs is paid and needs ELEVENLABS_API_KEY.
    voice_engine: Literal["kokoro", "chatterbox", "elevenlabs"] = "kokoro"
    kokoro_voice: str = Field("am_michael", pattern=r"^[a-z]{2}_[a-z]+$")
    chatterbox_voice: str = Field("default", pattern=r"^(default|[a-z]{2}_[a-z]+)$")   # its own, or a Kokoro timbre
    elevenlabs_voice: str = Field("", max_length=64)       # a voice_id from your ElevenLabs account
    elevenlabs_model: Literal["eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5"] = "eleven_multilingual_v2"
    voice_speed: float = Field(1.0, ge=0.7, le=1.3)
    # Kokoro and Chatterbox read one sentence at a time; this sets how long they pause between them.
    voice_pauses: Literal["tight", "natural", "relaxed"] = "natural"

    # Moment selection and brief checks
    clips_per_video: int = Field(5, ge=1, le=20)
    min_len: int = Field(20, ge=5, le=600)
    max_len: int = Field(60, ge=10, le=900)
    # AI models, by tier (see llm.py). Writing: scripts, storyboards, moments, ideas.
    # Quick: checks, props, campaign terms. Research: web search with citations.
    ai_writing_provider: Literal["anthropic", "openai"] = "anthropic"
    ai_writing_model: str = Field("claude-opus-5-5", pattern=r"^[\w.:/-]{2,80}$")
    ai_quick_provider: Literal["anthropic", "openai"] = "anthropic"
    ai_quick_model: str = Field("claude-haiku-5-5", pattern=r"^[\w.:/-]{2,80}$")
    ai_research_provider: Literal["anthropic", "openai"] = "anthropic"
    ai_research_model: str = Field("claude-sonnet-5-5", pattern=r"^[\w.:/-]{2,80}$")
    auto_compliance: bool = True              # check clips against the brief after rendering
    # When the AI is unavailable (no credit, no key, down): choose moments and check briefs with plain
    # rules (marked as such, still reviewed by you), or stop the job with the error.
    ai_fallback: Literal["rules", "fail"] = "rules"

    # Transcription
    language: str = Field("en", pattern=r"^[a-z]{2}$")
    whisper_engine: Literal["auto", "mlx", "faster-whisper"] = "auto"
    whisper_model: Literal["small", "medium", "large-v3-turbo"] = "large-v3-turbo"
    # Timing a voice take only has to find words we already know: a small model is enough,
    # and it needs a fraction of the memory (the large one can run Docker out of memory).
    voice_align_model: Literal["small", "medium", "large-v3-turbo"] = "small"
    # The ink style's hand-drawn wobble of the lines
    ink_wobble: Literal["off", "subtle", "strong"] = "subtle"

    # Which parts of the app are shown. The Studio is the main feature; turn Clipping on to use it.
    feature_clipping: bool = False
    feature_studio: bool = True

    # Download
    max_height: Literal[720, 1080] = 1080
    cookies_file: str = ""                    # optional yt-dlp cookies.txt

    # Render
    encoder: Literal["auto", "videotoolbox", "x264"] = "auto"
    bitrate_mbps: int = Field(8, ge=2, le=20)
    captions_enabled: bool = True
    caption_font: str = "ArchivoBlack-Regular.ttf"
    highlight_color: str = Field("#FFDD00", pattern=r"^#[0-9a-fA-F]{6}$")
    hook_seconds: float = Field(4.0, ge=0, le=15)
    # What to do when a source already has burned-in subtitles or caption cards:
    # cover = blur them, crop = cut the frame above them,
    # keep = show the whole frame and add no captions, ignore = do nothing.
    subtitle_mode: Literal["cover", "crop", "keep", "ignore"] = "cover"

    @field_validator("output_dir", "cookies_file", "theme", "studio_theme")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _lengths(self):
        if self.max_len <= self.min_len:
            raise ValueError("The longest clip length must be greater than the shortest")
        if not (self.feature_clipping or self.feature_studio):
            raise ValueError("Keep at least one feature on: Studio or Clipping")
        return self


def load() -> Settings:
    with session_scope() as db:
        row = db.get(Setting, KEY)
        if row is not None:
            try:
                return Settings(**row.value)
            except (ValidationError, TypeError):
                pass  # written by an older version: fall back to defaults
    return Settings()


def save(settings: Settings) -> None:
    """Check the folders and engines the settings point at, then store them."""
    resolve_whisper_engine(settings)
    resolve_encoder(settings)
    out = clips_dir(settings)
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".write-test"
        probe.write_text("")
        probe.unlink()
    except OSError as err:
        raise ValueError(f"Cannot write to the output folder {out}: {err.strerror}") from err
    if settings.cookies_file and not Path(settings.cookies_file).expanduser().is_file():
        raise ValueError(f"Cookies file not found: {settings.cookies_file}")
    with session_scope() as db:
        row = db.get(Setting, KEY)
        if row is None:
            db.add(Setting(key=KEY, value=settings.model_dump()))
        else:
            row.value = settings.model_dump()


def clips_dir(settings: Settings) -> Path:
    if settings.output_dir:
        return Path(settings.output_dir).expanduser()
    return config.default_clips_dir()


# --- values resolved for the machine the code is running on ------------------

def resolve_whisper_engine(settings: Settings) -> str:
    mlx_ok = (sys.platform == "darwin" and platform.machine() == "arm64"
              and find_spec("mlx_whisper") is not None)
    if settings.whisper_engine == "mlx" and not mlx_ok:
        raise ValueError("The MLX engine only runs on Apple Silicon Macs, outside Docker")
    if settings.whisper_engine == "faster-whisper" and find_spec("faster_whisper") is None:
        raise ValueError("faster-whisper is only installed on Linux and in Docker")
    if settings.whisper_engine == "auto":
        return "mlx" if mlx_ok else "faster-whisper"
    return settings.whisper_engine


def resolve_encoder(settings: Settings) -> str:
    if settings.encoder == "videotoolbox" and sys.platform != "darwin":
        raise ValueError("The VideoToolbox encoder only exists on macOS, outside Docker")
    if settings.encoder == "auto":
        return "videotoolbox" if sys.platform == "darwin" else "x264"
    return settings.encoder


def font_choices() -> list[dict]:
    choices = [{"value": p.name, "label": re.sub(r"[-_]", " ", p.stem)}
               for p in sorted(config.FONTS.glob("*.ttf"))]
    choices += [{"value": p, "label": f"{Path(p).stem} (this Mac only)"}
                for p in MAC_FONTS if Path(p).exists()]
    return choices


def resolve_font(settings: Settings) -> str:
    candidate = Path(settings.caption_font)
    if candidate.is_absolute() and candidate.exists():
        return str(candidate)
    bundled = config.FONTS / candidate.name
    if bundled.exists():
        return str(bundled)
    fallback = sorted(config.FONTS.glob("*.ttf"))
    if not fallback:
        raise ValueError("No caption font is bundled with the app")
    return str(fallback[0])


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
