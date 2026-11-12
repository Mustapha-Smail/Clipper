"""Stage 2: word-level transcript with a local Whisper model.

Two engines produce the same output:
  mlx             Apple Silicon GPU, macOS only (fastest on a Mac)
  faster-whisper  CPU, runs anywhere including Docker and Linux VMs

Run as a module (`python -m clipper.transcribe ...`) it works in a separate
process and prints PROGRESS lines. The web server uses that so the model's
memory is released as soon as the transcript is written.
"""

import json
import subprocess
import sys
from collections import deque
from pathlib import Path

from .ffutil import require
from .ingest import source_video

MLX_MODELS = {
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


def transcript_path(src_dir: Path) -> Path:
    return src_dir / "transcript.json"


def load_transcript(src_dir: Path) -> dict:
    path = transcript_path(src_dir)
    if not path.exists():
        raise FileNotFoundError("This source has no transcript yet.")
    return json.loads(path.read_text())


def _word(text: str, start: float, end: float) -> dict | None:
    text = text.strip()
    if not text:
        return None
    if end <= start:
        end = start + 0.05
    return {"w": text, "s": round(float(start), 3), "e": round(float(end), 3)}


def _with_mlx(video: Path, language: str, model: str) -> list[dict]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(video),
        path_or_hf_repo=MLX_MODELS[model],
        language=language,
        word_timestamps=True,
        # Stops one hallucinated line from poisoning everything after it.
        condition_on_previous_text=False,
        verbose=False,
    )
    words = []
    for segment in result["segments"]:
        for item in segment.get("words", []):
            word = _word(item["word"], item["start"], item["end"])
            if word:
                words.append(word)
    return words


def _load_audio(video: Path):
    """Decode the audio track to 16 kHz mono floats with ffmpeg.

    faster-whisper would otherwise decode through PyAV, whose API has broken
    between releases; ffmpeg is already a hard dependency.
    """
    import numpy as np

    proc = subprocess.run(
        [require("ffmpeg"), "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("Could not read an audio track from this video.\n"
                           + proc.stderr.decode(errors="replace")[-500:])
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _with_faster_whisper(video: Path, language: str, model: str, progress) -> list[dict]:
    from faster_whisper import WhisperModel

    audio = _load_audio(video)
    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = whisper.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=True,
    )
    words = []
    for segment in segments:  # a generator: the work happens as we iterate
        for item in segment.words or []:
            word = _word(item.word, item.start, item.end)
            if word:
                words.append(word)
        if progress and info.duration:
            progress(min(1.0, segment.end / info.duration))
    return words


def transcribe(src_dir: Path, language: str, engine: str, model: str,
               force: bool = False, progress=None, media: Path | None = None) -> dict:
    """Word timestamps for the video in `src_dir`, or for `media` (any audio or video file)."""
    out_path = transcript_path(src_dir)
    if out_path.exists() and not force:
        return json.loads(out_path.read_text())
    video = media or source_video(src_dir)
    if engine == "mlx":
        words = _with_mlx(video, language, model)
    else:
        words = _with_faster_whisper(video, language, model, progress)
    transcript = {"language": language, "engine": engine, "model": model, "words": words}
    out_path.write_text(json.dumps(transcript))
    return transcript


def transcribe_isolated(src_dir: Path, language: str, engine: str, model: str,
                        force: bool = False, progress=None, media: Path | None = None) -> dict:
    """Same as transcribe(), in a child process."""
    out_path = transcript_path(src_dir)
    if out_path.exists() and not force:
        return json.loads(out_path.read_text())
    proc = subprocess.Popen(
        [sys.executable, "-m", "clipper.transcribe", str(src_dir), language, engine, model,
         *([str(media)] if media else [])],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    tail: deque[str] = deque(maxlen=15)
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS "):
                if progress:
                    progress(float(line.split()[1]))   # may raise to cancel the job
            elif line:
                tail.append(line)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode in (-9, 137):
        raise RuntimeError(f"Transcription ran out of memory ({model} model) and was stopped by the system. "
                           "Pick a smaller Whisper model in Settings, or give Docker more memory "
                           "(Docker Desktop, Settings, Resources).")
    if proc.returncode != 0:
        raise RuntimeError("Transcription failed.\n" + "\n".join(tail))
    return json.loads(out_path.read_text())


if __name__ == "__main__":
    _src_dir, _language, _engine, _model = sys.argv[1:5]
    _media = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    transcribe(Path(_src_dir), _language, _engine, _model, force=True,
               progress=lambda f: print(f"PROGRESS {f:.3f}", flush=True), media=_media)
