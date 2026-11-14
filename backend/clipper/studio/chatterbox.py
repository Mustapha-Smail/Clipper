"""Chatterbox Turbo: a free, expressive voice that runs on this machine.

Resemble AI's model (MIT licence) reads tags such as [dramatic], [surprised] or
[whispering] and sounds such as [sigh] or [gasp]. Claude directs the delivery:
each sentence gets a mood and, now and then, a sound (`llm` task `delivery`).

chatterbox-tts pins torch, numpy and transformers versions the app does not use,
so it lives in its own virtual environment under data/cache/chatterbox/<platform>, made with
uv on first use, and runs in a child process (`chatterbox_runner.py`) whose
memory is freed when the take is spoken. It runs on the CPU, about two to three
times slower than real time on an M1, using about 2.5 GB of memory. Its audio
carries Resemble's inaudible Perth watermark, which marks it as AI-made.

The voice is its own, or the timbre of a Kokoro voice: a Kokoro clip is given
as the reference it copies.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .. import config, llm
from ..jobs import Reporter

PACKAGES = ["chatterbox-tts==0.1.7", "setuptools<81"]     # setuptools: its watermarker still imports pkg_resources
RUNNER = Path(__file__).with_name("chatterbox_runner.py")
MOODS = ("narration", "dramatic", "surprised", "sarcastic", "whispering", "happy", "fear", "angry")
SOUNDS = ("none", "sigh", "gasp", "clear throat", "chuckle", "laugh")
BEFORE = ("sigh", "gasp", "clear throat")      # sounds that come before the words; the others after
SOUND_EVERY = 5                                 # at most one sound per this many sentences


def folder() -> Path:
    """Per platform: the data folder is shared by the Mac app and Docker, and their packages differ."""
    return config.data_dir() / "cache" / "chatterbox" / f"{sys.platform}-{platform.machine()}"


def python() -> Path:
    return folder() / "venv" / "bin" / "python"


def ready() -> bool:
    marker = folder() / "installed.txt"
    return python().is_file() and marker.is_file() and marker.read_text() == "|".join(PACKAGES)


def _env() -> dict:
    return {**os.environ, "HF_HOME": str(config.data_dir() / "cache" / "huggingface"),
            "PYTHONNOUSERSITE": "1", "TOKENIZERS_PARALLELISM": "false"}


def install(rep: Reporter) -> None:
    """Make Chatterbox's own environment, once (about 1 GB; the model, about 1.5 GB more, comes on first use)."""
    if ready():
        return
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("Chatterbox is installed with uv, which was not found. Install uv, or use Kokoro.")
    rep.info("Installing Chatterbox, once: about 1 GB of packages, then its 1.5 GB model on the first take")
    folder().mkdir(parents=True, exist_ok=True)
    steps = [[uv, "venv", "--quiet", "--allow-existing", "--python", sys.executable, str(folder() / "venv")],
             [uv, "pip", "install", "--quiet", "--python", str(python()), *PACKAGES,
              *(["--torch-backend", "cpu"] if sys.platform == "linux" else [])]]   # no CUDA libraries in Docker
    for step in steps:
        done = subprocess.run(step, capture_output=True, text=True, timeout=3600, env=_env())
        if done.returncode:
            raise RuntimeError("Could not install Chatterbox: " + (done.stderr or done.stdout).strip()[-600:])
    (folder() / "installed.txt").write_text("|".join(PACKAGES))


def speak(texts: list[str], reference: Path | None, out: Path, rep: Reporter, start: float, span: float) -> list[Path]:
    """Speak each text (tags included) into out/NNN.wav, in a child process. Progress from start to start+span."""
    install(rep)
    job = out / "job.json"
    out.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"sentences": texts, "reference": str(reference) if reference else None,
                               "out": str(out), "threads": os.cpu_count() or 4}))
    # -I: isolated, so the runner's folder (where this module, also named chatterbox, lives) is not on its path
    proc = subprocess.Popen([str(python()), "-I", str(RUNNER), str(job)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=_env())
    tail: deque[str] = deque(maxlen=12)
    try:
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS "):
                done, total = (int(x) for x in line.split()[1:3])
                rep.progress(start + span * done / max(1, total),
                             f"Speaking: {done} of {total} sentences" if done else "Loading the Chatterbox model")
            elif line:
                tail.append(line)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode in (-9, 137):
        raise RuntimeError("Chatterbox ran out of memory and was stopped by the system. Close other apps and "
                           "speak the take again; it resumes where it stopped.")
    if proc.returncode:
        raise RuntimeError("Chatterbox failed: " + " | ".join(tail)[-600:])
    return [out / f"{n:03d}.wav" for n in range(len(texts))]


# --- directing the delivery ---------------------------------------------------------------

class Mark(BaseModel):
    n: int
    mood: Literal[MOODS]
    sound: Literal[SOUNDS] = "none"


class Marks(BaseModel):
    marks: list[Mark]


DIRECT = f"""\
You direct the narrator of a short explainer video. For each numbered sentence, choose how \
it is said: a mood, and now and then a sound.

Moods: {", ".join(MOODS)}. Most sentences are narration: an engaged storyteller already \
sounds alive. Use the others where the words call for them: dramatic for a reveal, a \
stake or a turn; surprised for something unexpected; sarcastic for irony or a myth being \
mocked; whispering for a secret or an aside; happy for a win or a fun fact; fear for \
danger; angry for an injustice. Vary them: never the same strong mood on more than two \
sentences in a row.
Sounds: {", ".join(s for s in SOUNDS if s != "none")}, or none. A sound must fit the line \
(a gasp before a shock, a chuckle after a joke, a sigh before a letdown). At most one \
sound every {SOUND_EVERY} sentences; none in the first sentence.
Answer with one mark per sentence, by its number."""


def direct(sentences: list[str], rep: Reporter) -> list[str]:
    """The sentences with their tags, as Claude directs them; plain sentences if the AI cannot be reached."""
    numbered = "\n".join(f"{n}. {s}" for n, s in enumerate(sentences))
    try:
        marks = llm.ask("delivery", DIRECT, f"<sentences>\n{numbered}\n</sentences>", Marks, effort="low").marks
    except Exception as err:  # noqa: BLE001 - a voice without direction is still a voice
        rep.warn(f"Could not direct the delivery ({str(err)[:160]}): every sentence is read as plain narration")
        return list(sentences)
    by_n = {m.n: m for m in marks if 0 <= m.n < len(sentences)}
    out, last_sound, moods, sounds = [], -SOUND_EVERY, {}, 0
    for n, sentence in enumerate(sentences):
        mark = by_n.get(n) or Mark(n=n, mood="narration")
        sound = mark.sound if mark.sound != "none" and n > 0 and n - last_sound >= SOUND_EVERY else "none"
        if sound != "none":
            last_sound, sounds = n, sounds + 1
        moods[mark.mood] = moods.get(mark.mood, 0) + 1
        text = sentence if mark.mood == "narration" else f"[{mark.mood}] {sentence}"
        if sound != "none":
            text = f"[{sound}] {text}" if sound in BEFORE else f"{text} [{sound}]"
        out.append(text)
    rep.info("Delivery: " + ", ".join(f"{k} {v}" for k, v in sorted(moods.items(), key=lambda kv: -kv[1]))
             + f"; {sounds} sounds")
    return out
