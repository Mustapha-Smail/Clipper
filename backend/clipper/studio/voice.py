"""Voice for a script: your own recording, or an AI voice, then timing every word.

Every take ends as `voice.wav` (48 kHz mono, loudness-normalised for YouTube)
in its own folder, plus `timeline.json`: each script word with the time it is
spoken, which the storyboard and renderer will follow.

  speak    AI voice. Kokoro runs on this machine (free, about five times faster
           than real time on an M1, flat); Chatterbox runs here too (free,
           expressive, directed by Claude, slower than real time; see
           chatterbox.py); ElevenLabs is a paid cloud service.
  prepare  Your recording: any audio or video file, converted and normalised.
  align    Whisper transcribes the take; the words are matched to the script,
           so the app knows when each section starts and which lines were
           skipped, changed or added.
"""

import difflib
import hashlib
import json
import logging
import re
import shutil
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np

from .. import config
from .. import transcribe as transcribe_mod
from ..db import session_scope
from ..db.models import Script, VoiceTake
from ..ffutil import ToolError, require, run
from ..jobs import Reporter
from ..settings import Settings, resolve_whisper_engine
from . import script as script_mod

log = logging.getLogger("clipper.studio")

SAMPLE_RATE = 48_000
LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11"     # YouTube plays at about -14 LUFS; -16 leaves headroom
SECTION_PAUSE = 0.55     # deliberate silence between sections in an AI take
LONG_SILENCE = 0.65      # provider pauses longer than this lose attention
KEPT_SILENCE = 0.28      # retain a natural beat when shortening a long pause

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {"kokoro-v1.0.fp16.onnx": 177_464_787, "voices-v1.0.bin": 28_214_398}
# The English voices, with what they sound like. Kokoro has more languages; this channel is English.
KOKORO_VOICES = {
    "am_michael": "Michael · American man, calm",
    "am_adam": "Adam · American man, deep",
    "am_eric": "Eric · American man, bright",
    "am_liam": "Liam · American man, young",
    "am_onyx": "Onyx · American man, low",
    "am_puck": "Puck · American man, playful",
    "am_fenrir": "Fenrir · American man, rough",
    "am_echo": "Echo · American man, soft",
    "bm_george": "George · British man, warm",
    "bm_lewis": "Lewis · British man, deep",
    "bm_daniel": "Daniel · British man, measured",
    "bm_fable": "Fable · British man, storyteller",
    "af_heart": "Heart · American woman, warm",
    "af_bella": "Bella · American woman, lively",
    "af_nicole": "Nicole · American woman, close",
    "af_sarah": "Sarah · American woman, clear",
    "af_sky": "Sky · American woman, light",
    "af_nova": "Nova · American woman, even",
    "bf_emma": "Emma · British woman, clear",
    "bf_isabella": "Isabella · British woman, warm",
    "bf_alice": "Alice · British woman, crisp",
    "bf_lily": "Lily · British woman, soft",
}

ELEVENLABS = "https://api.elevenlabs.io"


# --- where things are ----------------------------------------------------------------

def take_dir(script_id: int, take_id: int) -> Path:
    return config.studio_dir() / f"script-{script_id}" / f"take-{take_id}"


def script_media_dir(script_id: int) -> Path:
    return config.studio_dir() / f"script-{script_id}"


def timeline_path(take: VoiceTake) -> Path:
    return take_dir(take.script_id, take.id) / "timeline.json"


def remove_take_files(script_id: int, take_id: int) -> None:
    shutil.rmtree(take_dir(script_id, take_id), ignore_errors=True)


def _take(take_id: int) -> tuple[VoiceTake, Script]:
    with session_scope() as db:
        take = db.get(VoiceTake, take_id)
        if take is None:
            raise RuntimeError(f"Voice take {take_id} no longer exists")
        script = take.script
        db.expunge(take)
        db.expunge(script)
        return take, script


def _update(take_id: int, **fields) -> None:
    with session_scope() as db:
        take = db.get(VoiceTake, take_id)
        for name, value in fields.items():
            setattr(take, name, value)


def duration_of(path: Path) -> float:
    out = run([require("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return round(float(json.loads(out)["format"]["duration"]), 2)


def _normalise(src: Path, out: Path, highpass: bool) -> None:
    """Convert anything with an audio track to a loudness-normalised 48 kHz mono WAV."""
    filters = ("highpass=f=70," if highpass else "") + LOUDNESS
    partial = out.with_suffix(".partial.wav")
    run([require("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-af", filters, "-c:a", "pcm_s16le", str(partial)])
    partial.replace(out)


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())


def _chunks(text: str, limit: int = 400) -> list[str]:
    """Split narration at sentence ends into pieces short enough for one synthesis call."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    chunks, current = [], ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


# --- AI voices -----------------------------------------------------------------------

def kokoro_dir() -> Path:
    return config.data_dir() / "cache" / "kokoro"


def kokoro_ready() -> bool:
    return all((kokoro_dir() / name).is_file() for name in KOKORO_FILES)


def _download_kokoro(rep: Reporter) -> None:
    folder = kokoro_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name, size in KOKORO_FILES.items():
        target = folder / name
        if target.is_file() and target.stat().st_size == size:
            continue
        rep.info(f"Downloading the Kokoro voice model ({name}, {size / 1e6:.0f} MB), once")
        partial = target.with_suffix(target.suffix + ".partial")
        with urllib.request.urlopen(KOKORO_RELEASE + name, timeout=60) as response, partial.open("wb") as out:
            done = 0
            while block := response.read(1 << 20):
                out.write(block)
                done += len(block)
                rep.progress(min(done / size, 1.0) * 0.3, f"Downloading the voice model: {done / 1e6:.0f} MB")
        if partial.stat().st_size != size:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"The Kokoro download of {name} was incomplete. Retry the job.")
        partial.replace(target)


PAUSE_SCALE = {"tight": 0.7, "natural": 1.0, "relaxed": 1.3}
ROOM_TONE_DB = -62


LOWER_START = re.compile(r"^[\"'“‘(]*[a-z]")


def sentences(text: str) -> list[str]:
    """Split narration into sentences; very long ones into pieces.

    An ellipsis before a lowercase word ("team names, like… the Drunkards") is a
    beat inside the sentence: it stays in one piece, so the engine holds its tone
    through the hesitation. Cut there, the first half was read as a finished
    sentence, falling tone and a sentence's pause, then the rest started anew.
    Before a capital, or at the end, an ellipsis ends the sentence.
    """
    pieces: list[str] = []
    for part in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if not part:
            continue
        if pieces and pieces[-1].endswith(("…", "...")) and LOWER_START.match(part):
            pieces[-1] += " " + part
        else:
            pieces.append(part)
    out = []
    for sentence in pieces:
        out.extend(_chunks(sentence) if len(sentence) > 400 else [sentence])
    return out


def delivery(sentence: str, last_in_paragraph: bool, rng: np.random.Generator) -> tuple[float, float]:
    """(speed factor, pause after) for one sentence, the way a narrator would say it.

    Short punchy lines are a touch slower and get room to land; long ones run a
    little quicker; questions and ellipses hold a beat; a paragraph ends on a breath.
    """
    words = len(sentence.split())
    speed = (0.94 if words <= 4 else 1.04 if words >= 18 else 1.0) * rng.uniform(0.97, 1.03)
    if last_in_paragraph:
        pause = 0.45
    elif sentence.endswith("?"):
        pause = 0.35
    elif sentence.endswith("…"):
        pause = 0.45
    elif words <= 4:
        pause = 0.32
    else:
        pause = 0.20
    return speed, pause * rng.uniform(0.85, 1.15)


def speak_kokoro(sections: list[dict], voice: str, speed: float, out: Path, rep: Reporter,
                 pauses: str = "natural") -> None:
    """Read the sections one sentence at a time, with a narrator's pauses and pace, over quiet room tone."""
    if voice not in KOKORO_VOICES:
        raise ValueError(f"Unknown Kokoro voice: {voice}")
    _download_kokoro(rep)
    from kokoro_onnx import Kokoro

    engine = Kokoro(str(kokoro_dir() / "kokoro-v1.0.fp16.onnx"), str(kokoro_dir() / "voices-v1.0.bin"))
    rng = np.random.default_rng(len(sections) * 7919 + len(voice))
    scale = PAUSE_SCALE.get(pauses, 1.0)
    lines = _lines(sections)
    audio, rate = [], 24_000
    for n, (sentence, paragraph_end, section_end) in enumerate(lines):
        factor, pause = delivery(sentence, paragraph_end, rng)
        samples, rate = engine.create(sentence, voice=voice, speed=max(0.5, min(2.0, speed * factor)), lang="en-us")
        audio.append(_trim(samples.astype(np.float32), rate))
        if section_end:
            pause = max(pause, SECTION_PAUSE)
        if n + 1 < len(lines):
            audio.append(np.zeros(int(rate * pause * scale), dtype=np.float32))
        rep.progress(0.3 + 0.7 * (n + 1) / len(lines), f"Speaking: {n + 1} of {len(lines)} sentences")
    del engine
    _write_wav(out, _room_tone(np.concatenate(audio), rng), rate)


def _lines(sections: list[dict]) -> list[tuple[str, bool, bool]]:
    """(sentence, last in its paragraph, last in its section but not the last section) for every sentence."""
    lines = []
    for i, section in enumerate(sections):
        paragraphs = [p for p in re.split(r"\n\s*\n", section["text"]) if p.strip()] or [section["text"]]
        for pi, paragraph in enumerate(paragraphs):
            parts = sentences(paragraph)
            for si, sentence in enumerate(parts):
                lines.append((sentence, si == len(parts) - 1, si == len(parts) - 1 and pi == len(paragraphs) - 1
                              and i < len(sections) - 1))
    return lines


def _room_tone(track: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Very quiet filtered noise under the voice, so a pause sounds like a room, not like a switch."""
    noise = np.convolve(rng.standard_normal(len(track)), np.ones(40) / 40, mode="same").astype(np.float32)
    return track + noise * (10 ** (ROOM_TONE_DB / 20)) / (noise.std() + 1e-9)


CHATTERBOX_REFERENCE = ("Picture a hand made of rubber, lying on the table in front of you. Now watch what happens "
                        "when someone strokes it, slowly, at the very same moment as your own hidden hand.")


def chatterbox_voices() -> list[dict]:
    """Chatterbox's own voice, then the timbre of each Kokoro voice (a Kokoro clip is the reference it copies)."""
    return [{"id": "default", "label": "Chatterbox · its own voice, American woman"},
            *({"id": k, "label": f"{v} (as Chatterbox)"} for k, v in KOKORO_VOICES.items())]


def speak_chatterbox(sections: list[dict], voice: str, speed: float, out: Path, rep: Reporter,
                     pauses: str = "natural") -> None:
    """Claude directs each sentence (a mood, now and then a sound); Chatterbox speaks it; pauses as for Kokoro."""
    from . import chatterbox

    if voice != "default" and voice not in KOKORO_VOICES:
        raise ValueError(f"Unknown voice for Chatterbox: {voice}")
    lines = _lines(sections)
    texts = chatterbox.direct([s for s, _, _ in lines], rep)
    reference = None
    if voice != "default":
        rep.info(f"Taking the timbre of the Kokoro voice {KOKORO_VOICES[voice].split(' · ')[0]}")
        reference = sample_kokoro(voice, 1.0, CHATTERBOX_REFERENCE)
    parts = chatterbox.speak(texts, reference, out.parent / "chatterbox-parts", rep, 0.05, 0.9)
    rng = np.random.default_rng(len(sections) * 7919 + len(voice))
    scale = PAUSE_SCALE.get(pauses, 1.0)
    audio, rate = [], 24_000
    for n, ((sentence, paragraph_end, section_end), part) in enumerate(zip(lines, parts, strict=True)):
        samples, rate = _read_wav(part)
        audio.append(_trim(samples, rate))
        _, pause = delivery(sentence, paragraph_end, rng)
        if section_end:
            pause = max(pause, SECTION_PAUSE)
        if n + 1 < len(lines):
            audio.append(np.zeros(int(rate * pause * scale), dtype=np.float32))
    track = _room_tone(np.concatenate(audio), rng)
    if abs(speed - 1.0) > 0.01:      # Chatterbox has no speed control: stretch the whole take, pitch kept
        plain = out.with_suffix(".plain.wav")
        _write_wav(plain, track, rate)
        run([require("ffmpeg"), "-y", "-v", "error", "-i", str(plain), "-af", f"atempo={max(0.5, min(2.0, speed))}",
             str(out)])
        plain.unlink(missing_ok=True)
    else:
        _write_wav(out, track, rate)
    shutil.rmtree(out.parent / "chatterbox-parts", ignore_errors=True)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as f:
        rate, width, channels = f.getframerate(), f.getsampwidth(), f.getnchannels()
        raw = f.readframes(f.getnframes())
    if width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    else:
        raise RuntimeError(f"Unexpected sample width in {path.name}: {width}")
    return samples.reshape(-1, channels).mean(axis=1), rate


def _trim(samples: np.ndarray, rate: int, floor_db: float = -45) -> np.ndarray:
    """Cut the silence a model leaves before and after a sentence: the pauses are ours."""
    loud = np.flatnonzero(np.abs(samples) > 10 ** (floor_db / 20))
    if not len(loud):
        return samples
    pad = int(rate * 0.03)
    return samples[max(0, loud[0] - pad):loud[-1] + pad + 1]


def _compact_silence(samples: np.ndarray, rate: int, floor_db: float = -45) -> np.ndarray:
    """Shorten confirmed long internal silences without touching normal speech rhythm."""
    samples = _trim(samples, rate, floor_db)
    frame = max(1, int(rate * 0.01))
    frame_count = (len(samples) + frame - 1) // frame
    padded = np.pad(samples, (0, frame_count * frame - len(samples)))
    rms = np.sqrt(np.mean(padded.reshape(frame_count, frame).astype(np.float64) ** 2, axis=1))
    silent = rms < 10 ** (floor_db / 20)
    pieces, copied_to, i = [], 0, 0
    while i < frame_count:
        if not silent[i]:
            i += 1
            continue
        end = i + 1
        while end < frame_count and silent[end]:
            end += 1
        run_start, run_end = i * frame, min(end * frame, len(samples))
        if i and end < frame_count and run_end - run_start >= int(rate * LONG_SILENCE):
            keep = min(int(rate * KEPT_SILENCE), run_end - run_start)
            left = keep // 2
            pieces.append(samples[copied_to:run_start + left])
            copied_to = run_end - (keep - left)
        i = end
    pieces.append(samples[copied_to:])
    return np.concatenate(pieces)


def _elevenlabs(method: str, path: str, body: dict | None = None, accept: str = "application/json") -> bytes:
    key = config.elevenlabs_api_key()
    if not key:
        raise RuntimeError("No ElevenLabs key. Put ELEVENLABS_API_KEY in .env and restart, or use Kokoro.")
    request = urllib.request.Request(ELEVENLABS + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as err:
        try:
            detail = json.load(err).get("detail")
        except (ValueError, AttributeError):
            detail = None
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status")
        elif isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)
        if err.code == 401:
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"ElevenLabs rejected the key in ELEVENLABS_API_KEY{suffix}") from err
        raise RuntimeError(f"ElevenLabs error ({err.code}){': ' + str(detail) if detail else ''}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise RuntimeError("Could not reach ElevenLabs. Check your connection.") from err


def elevenlabs_voices() -> list[dict]:
    data = json.loads(_elevenlabs("GET", "/v2/voices?page_size=100&sort=name&sort_direction=asc"))
    voices = []
    for v in data.get("voices", []):
        labels = ", ".join(str(x) for x in (v.get("labels") or {}).values() if x)
        voices.append({"id": v["voice_id"], "label": v.get("name", v["voice_id"]) + (f" · {labels}" if labels else ""),
                       "preview": v.get("preview_url")})   # ElevenLabs' own sample: free to play
    return voices


# --- short samples, to compare voices before making a whole take ---------------------------

_sample_lock = threading.Lock()


def sample_kokoro(voice: str, speed: float, text: str, pauses: str = "natural") -> Path:
    """A few seconds of `text` in a Kokoro voice, cached so each sample is made once."""
    if voice not in KOKORO_VOICES:
        raise ValueError(f"Unknown Kokoro voice: {voice}")
    text = " ".join(text.split())[:300] or "Your fridge has never made cold. It just moves heat around."
    key = hashlib.sha256(f"{voice}|{speed:.2f}|{pauses}|{text}|v4".encode()).hexdigest()[:20]
    folder = config.data_dir() / "cache" / "voice-samples"
    out = folder / f"{key}.wav"
    if out.is_file():
        return out
    folder.mkdir(parents=True, exist_ok=True)
    with _sample_lock:   # one sample at a time keeps memory flat
        if not out.is_file():
            partial = out.with_suffix(".partial.wav")
            speak_kokoro([{"text": text}], voice, speed, partial, Reporter(), pauses)   # the base reporter is silent
            partial.replace(out)
    return out


def speak_elevenlabs(sections: list[dict], voice_id: str, model: str, speed: float, out: Path,
                     rep: Reporter) -> None:
    """One request per section, joined with a pause. Neighbouring text keeps the delivery continuous."""
    if not voice_id:
        raise RuntimeError("Choose an ElevenLabs voice first")
    folder = out.parent / "parts"
    folder.mkdir(exist_ok=True)
    parts = []
    for i, section in enumerate(sections):
        body = {"text": section["text"], "model_id": model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed},
                "previous_text": sections[i - 1]["text"][-500:] if i else None,
                "next_text": sections[i + 1]["text"][:500] if i + 1 < len(sections) else None}
        audio = _elevenlabs("POST", f"/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128", body,
                            accept="audio/mpeg")
        part = folder / f"{i:03d}.mp3"
        part.write_bytes(audio)
        parts.append(part)
        rep.progress((i + 1) / len(sections), f"Speaking: section {i + 1} of {len(sections)}")
    # TTS providers can bake distracting gaps into a section. Decode each one so
    # confirmed long silences can be shortened, then add one exact section beat.
    cleaned = []
    for i, part in enumerate(parts):
        decoded = folder / f"clean-{i:03d}.wav"
        run([require("ffmpeg"), "-y", "-v", "error", "-i", str(part), "-vn", "-ac", "1", "-ar",
             str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(decoded)])
        samples, rate = _read_wav(decoded)
        samples = _compact_silence(samples, rate)
        if i + 1 < len(parts):
            samples = np.concatenate([samples, np.zeros(int(rate * SECTION_PAUSE), dtype=np.float32)])
        _write_wav(decoded, samples, rate)
        cleaned.append(decoded)
    inputs = [arg for part in cleaned for arg in ("-i", str(part))]
    graph = "".join(f"[{i}:a]" for i in range(len(cleaned))) + f"concat=n={len(cleaned)}:v=0:a=1[out]"
    run([require("ffmpeg"), "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[out]", str(out)])
    shutil.rmtree(folder, ignore_errors=True)


# --- job stages ------------------------------------------------------------------------

def speak(take_id: int, settings: Settings, rep: Reporter) -> None:
    take, script = _take(take_id)
    folder = take_dir(take.script_id, take.id)
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / "raw.wav"
    sections = [s for s in script.sections if s["text"].strip()]
    if not sections:
        raise RuntimeError("The script has no text to speak")
    rep.info(f"Voice: {take.voice} ({take.source}), speed {take.speed or 1.0:g}"
             + (f", {take.pauses} pauses" if take.source in ("kokoro", "chatterbox") else ""))
    if take.source == "kokoro":
        speak_kokoro(sections, take.voice, take.speed or 1.0, raw, rep, take.pauses or "natural")
    elif take.source == "chatterbox":
        speak_chatterbox(sections, take.voice, take.speed or 1.0, raw, rep, take.pauses or "natural")
    elif take.source == "elevenlabs":
        speak_elevenlabs(sections, take.voice, settings.elevenlabs_model, take.speed or 1.0, raw, rep)
    else:
        raise RuntimeError(f"Not an AI take: {take.source}")
    out = folder / "voice.wav"
    _normalise(raw, out, highpass=False)
    raw.unlink(missing_ok=True)
    seconds = duration_of(out)
    _update(take_id, path=str(out), duration=seconds)
    rep.info(f"Voice ready: {seconds / 60:.1f} minutes")


def prepare(take_id: int, settings: Settings, rep: Reporter) -> None:
    take, _ = _take(take_id)
    folder = take_dir(take.script_id, take.id)
    original = next((p for p in folder.glob("original.*")), None)
    if original is None:
        raise RuntimeError("The uploaded recording is missing. Upload it again.")
    out = folder / "voice.wav"
    rep.info("Converting the recording and evening out its loudness")
    try:
        _normalise(original, out, highpass=True)
    except ToolError as err:
        raise RuntimeError("Could not read the recording. Is it an audio or video file?\n" + str(err)) from err
    seconds = duration_of(out)
    _update(take_id, path=str(out), duration=seconds)
    rep.info(f"Recording ready: {seconds / 60:.1f} minutes")


def align(take_id: int, settings: Settings, rep: Reporter) -> None:
    take, script = _take(take_id)
    if not take.path or not Path(take.path).is_file():
        raise RuntimeError("The take has no audio yet")
    folder = take_dir(take.script_id, take.id)
    engine = resolve_whisper_engine(settings)
    rep.info(f"Transcribing the take ({engine}, {settings.voice_align_model})")
    transcript = transcribe_mod.transcribe_isolated(folder, settings.language, engine, settings.voice_align_model,
                                                    force=True, progress=rep.progress, media=Path(take.path))
    result = match(script.sections, transcript["words"])
    timeline_path(take).write_text(json.dumps(result.pop("timeline")))
    result["script_hash"] = script_mod.text_hash(script.sections)
    _update(take_id, alignment=result)
    rep.info(f"{result['coverage']:.0%} of the script was found in the take")
    if result["missing"]:
        rep.warn(f"{len(result['missing'])} passages of the script were not spoken or were said differently")
    if result["extra"]:
        rep.info(f"{len(result['extra'])} stretches of speech are not in the script (retakes, asides)")


# --- matching speech to the script ---------------------------------------------------------

def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("’", "'"))


MIN_RUN = 3    # shorter mismatches are usually transcription noise, not real differences


def match(sections: list[dict], spoken: list[dict]) -> dict:
    """Line the spoken words up with the script's words.

    Returns each section's time range, a timeline of script words with times
    (words that were not recognised get times between their neighbours),
    script passages that were not spoken, speech that is not in the script,
    and the share of script words found.
    """
    script_words = [(i, w) for i, s in enumerate(sections) for w in s["text"].split()]
    a = [_norm(w) for _, w in script_words]
    b = [_norm(w["w"]) for w in spoken]
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(a)
    missing, extra, found = [], [], 0
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            found += a1 - a0
            for k in range(a1 - a0):
                times[a0 + k] = (spoken[b0 + k]["s"], spoken[b0 + k]["e"])
            continue
        if op == "replace" and a1 - a0 == b1 - b0:   # same length: misheard words, still timed in place
            for k in range(a1 - a0):
                times[a0 + k] = (spoken[b0 + k]["s"], spoken[b0 + k]["e"])
        if op in ("delete", "replace") and a1 - a0 >= MIN_RUN:
            section = sections[script_words[a0][0]]
            missing.append({"section": section["key"], "heading": section["heading"],
                            "text": " ".join(w for _, w in script_words[a0:a1]),
                            "heard": " ".join(w["w"] for w in spoken[b0:b1])})
        if op in ("insert", "replace") and b1 - b0 >= MIN_RUN and b1 - b0 > a1 - a0:
            extra.append({"at": spoken[b0]["s"], "text": " ".join(w["w"] for w in spoken[b0:b1])})

    # Words that were not recognised share the time between their nearest timed neighbours.
    known = [k for k, t in enumerate(times) if t is not None]
    filled = list(times)
    for k in range(len(a)):
        if times[k] is not None or not known:
            continue
        before = max((j for j in known if j < k), default=None)
        after = min((j for j in known if j > k), default=None)
        start = times[before][1] if before is not None else times[after][0]
        end = times[after][0] if after is not None else times[before][1]
        first = before + 1 if before is not None else 0
        last = after - 1 if after is not None else len(a) - 1
        step = (end - start) / (last - first + 1)
        filled[k] = (round(start + step * (k - first), 3), round(start + step * (k - first + 1), 3))
    timeline = [{"section": sections[i]["key"], "w": word, "s": filled[k][0] if filled[k] else 0.0,
                 "e": filled[k][1] if filled[k] else 0.0}
                for k, (i, word) in enumerate(script_words)]

    ranges = []
    for section in sections:
        entries = [t for t in timeline if t["section"] == section["key"]]
        if entries:
            ranges.append({"key": section["key"], "heading": section["heading"],
                           "start": entries[0]["s"], "end": entries[-1]["e"]})
    return {"coverage": round(found / len(a), 3) if a else 0.0, "sections": ranges,
            "missing": missing, "extra": extra, "spoken_words": len(spoken), "timeline": timeline}
