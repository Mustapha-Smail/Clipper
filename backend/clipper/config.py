"""Runtime configuration from the environment, plus fixed constants.

Anything a user may change while the app runs lives in settings.py (stored in
the database). This module only knows what is decided when a process starts.

  CLIPPER_DATA_DIR       downloads, transcripts, uploads, default clips folder
  CLIPPER_DATABASE_URL   SQLAlchemy URL; defaults to a SQLite file in the data folder
  CLIPPER_FRONTEND_DIR   built Angular app to serve (native runs only)
  CLIPPER_LOG_FORMAT     "json" or "text"
  CLIPPER_LOG_LEVEL      DEBUG, INFO, WARNING...
  CLIPPER_IN_DOCKER      set by the container image
  YOUTUBE_API_KEY        YouTube Data API key, used to check demand for Studio ideas
  ELEVENLABS_API_KEY     ElevenLabs key, for the paid AI voice (Kokoro, the free one, needs none)
  OPENAI_API_KEY         OpenAI key, when a tier in Settings uses OpenAI
  OPENAI_BASE_URL        any OpenAI-compatible server instead (OpenRouter, Ollama...); empty = OpenAI
  WIKIMEDIA_CONTACT      a URL or email sent to Wikimedia in the User-Agent of archive photo searches;
                         with it Wikimedia allows 200 searches a minute, without it 10
"""

import os
import re
from pathlib import Path

VERSION = "1.4.0"

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent.parent
ASSETS = PACKAGE / "assets"
MODELS = ASSETS / "models"
FONTS = ASSETS / "fonts"

# Clip formats, as (width, height). Vertical is what TikTok, Reels and Shorts show full
# screen; horizontal keeps a landscape source as it is, for X, Facebook and YouTube.
FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080)}
FPS = 30

FACE_MODEL = MODELS / "face_detection_yunet_2023mar.onnx"
FACE_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)

IN_DOCKER = bool(os.environ.get("CLIPPER_IN_DOCKER"))


def data_dir() -> Path:
    return Path(os.environ.get("CLIPPER_DATA_DIR") or REPO / "data").expanduser()


def sources_dir() -> Path:
    return data_dir() / "sources"


def uploads_dir() -> Path:
    return data_dir() / "uploads"


def default_clips_dir() -> Path:
    return data_dir() / "clips"


def database_url() -> str:
    return os.environ.get("CLIPPER_DATABASE_URL") or f"sqlite:///{data_dir() / 'clipper.db'}"


def frontend_dir() -> Path:
    return Path(os.environ.get("CLIPPER_FRONTEND_DIR")
                or REPO / "frontend" / "dist" / "frontend" / "browser")


def log_format() -> str:
    return os.environ.get("CLIPPER_LOG_FORMAT") or ("json" if IN_DOCKER else "text")


def youtube_api_key() -> str:
    return (os.environ.get("YOUTUBE_API_KEY") or "").strip()


def elevenlabs_api_key() -> str:
    return (os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def openai_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def openai_base_url() -> str:
    return (os.environ.get("OPENAI_BASE_URL") or "").strip()


def wikimedia_contact() -> str:
    """Sent to Wikimedia and nowhere else; empty unless the owner chose one. Kept to plain header characters."""
    return re.sub(r"[^\x21-\x7e]", "", os.environ.get("WIKIMEDIA_CONTACT") or "")[:200]


def studio_dir() -> Path:
    """Studio media: voice recordings and generated audio, one folder per script."""
    return data_dir() / "studio"


def log_level() -> str:
    return (os.environ.get("CLIPPER_LOG_LEVEL") or "INFO").upper()
