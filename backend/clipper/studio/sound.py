"""Everything you hear besides the voice, and the text files YouTube wants.

  effects    Small pops and whooshes synthesised here, timed to the animation:
             a pop when something pops in, a whoosh when it slides. Quiet, and
             never two within a few frames of each other.
  music      A track you uploaded, looped, faded in and out, and pushed down
             automatically whenever the voice speaks (sidechain ducking).
  subtitles  An .srt made from the script's own words with the take's timing,
             so names and numbers are spelled as written.
  chapters   The YouTube chapter list ("0:00 Cold open") from section starts.
"""

import math
import wave
from pathlib import Path

import numpy as np

RATE = 48_000
EFFECT_GAP = 0.12          # seconds: entrances closer than this share one sound
EFFECT_GAIN = 0.16         # well under the voice


def _pop(rng: np.random.Generator) -> np.ndarray:
    """A short, soft 'bloop': a falling sine with a fast decay."""
    n = int(RATE * 0.09)
    t = np.arange(n) / RATE
    start, end = rng.uniform(520, 700), rng.uniform(180, 240)
    freq = end + (start - end) * np.exp(-t * 40)
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    return (np.sin(phase) * np.exp(-t * 38)).astype(np.float32)


def _whoosh(rng: np.random.Generator) -> np.ndarray:
    """Filtered noise that swells and fades, for things sliding in."""
    n = int(RATE * 0.28)
    noise = rng.standard_normal(n).astype(np.float32)
    kernel = np.hanning(64).astype(np.float32)
    kernel /= kernel.sum()
    smooth = np.convolve(noise, kernel, mode="same")
    envelope = np.sin(np.linspace(0, np.pi, n)) ** 2
    return (smooth * envelope * 3.0).astype(np.float32)


def effect_events(scenes: list[dict], word_time, duration: float) -> list[tuple[float, str]]:
    """(time, 'pop' | 'whoosh') for every element entrance worth a sound."""
    events = []
    starts = [0.0] + [word_time(s["start_word"]) for s in scenes[1:]]
    for i, scene in enumerate(scenes):
        for e in scene["elements"]:
            if e["kind"] == "arrow" or e["enter"] in ("none", "fade"):
                continue
            at = word_time(e["enter_word"]) if e.get("enter_word") is not None else starts[i]
            at = max(at, starts[i])
            if at < duration:
                events.append((at, "whoosh" if e["enter"] == "slide" else "pop"))
    events.sort()
    kept: list[tuple[float, str]] = []
    for at, kind in events:
        if not kept or at - kept[-1][0] >= EFFECT_GAP:
            kept.append((at, kind))
    return kept


def write_effects(events: list[tuple[float, str]], duration: float, out: Path) -> None:
    rng = np.random.default_rng(7)
    track = np.zeros(int(RATE * duration) + RATE, dtype=np.float32)
    for at, kind in events:
        sound = _pop(rng) if kind == "pop" else _whoosh(rng) * 0.7
        i = int(at * RATE)
        track[i:i + len(sound)] += sound[:max(0, len(track) - i)] * EFFECT_GAIN
    pcm = (np.clip(track[:int(RATE * duration)], -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def mix_graph(duration: float, effects: bool, music: bool, music_db: float) -> tuple[str, str]:
    """An ffmpeg filter graph for voice (input 1), effects (2) and music (next input).

    Returns the graph and the label of the mixed output.
    """
    parts = [f"[1:a]aresample={RATE},aformat=channel_layouts=mono,asplit=2[voice][key]"]
    mix = ["[voice]"]
    music_input = 3 if effects else 2
    if effects:
        parts.append(f"[2:a]aresample={RATE}[fx]")
        mix.append("[fx]")
    if music:
        fade_out = max(0.0, duration - 3.0)
        parts.append(f"[{music_input}:a]aresample={RATE},aformat=channel_layouts=mono,volume={music_db}dB,"
                     f"afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3[bed]")
        # The voice pushes the music down while it speaks and lets it back up in pauses.
        parts.append("[bed][key]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=500:makeup=1[ducked]")
        mix.append("[ducked]")
    else:
        parts.append("[key]anullsink")
    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,"
                 f"alimiter=limit=0.95,aresample={RATE}[mixed]")
    return ";".join(parts), "[mixed]"


# --- subtitles and chapters ---------------------------------------------------------------

MAX_LINE = 42
MAX_CUE = 6.0


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt(words: list[dict]) -> str:
    """Subtitle cues of at most two lines of 42 characters, broken at sentences and pauses."""
    cues: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        text = " ".join(x["w"] for x in current + [w])
        too_long = len(text) > 2 * MAX_LINE or (current and w["e"] - current[0]["s"] > MAX_CUE)
        pause = current and w["s"] - current[-1]["e"] > 0.7
        if current and (too_long or pause):
            cues.append(current)
            current = []
        current.append(w)
        if w["w"].rstrip("\"')”").endswith((".", "?", "!")) and len(" ".join(x["w"] for x in current)) > 20:
            cues.append(current)
            current = []
    if current:
        cues.append(current)

    out = []
    for n, cue in enumerate(cues, 1):
        line, lines = "", []
        for w in cue:
            if line and len(line) + 1 + len(w["w"]) > MAX_LINE:
                lines.append(line)
                line = w["w"]
            else:
                line = f"{line} {w['w']}".strip()
        lines.append(line)
        end = cue[-1]["e"] + 0.15
        if n < len(cues):
            end = min(end, cues[n][0]["s"] - 0.01)
        out.append(f"{n}\n{_stamp(cue[0]['s'])} --> {_stamp(max(end, cue[0]['s'] + 0.5))}\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n"


def chapters(words: list[dict], headings: dict[str, str]) -> dict:
    """YouTube chapters from where each section starts. YouTube needs 3 or more, 10 s apart, from 0:00."""
    marks = []
    for w in words:
        key = w.get("section")
        if key and (not marks or marks[-1][1] != key):
            marks.append((w["s"], key))
    lines = []
    for i, (at, key) in enumerate(marks):
        at = 0.0 if i == 0 else at
        lines.append(f"{int(at // 60)}:{int(at % 60):02d} {headings.get(key, 'Chapter')}")
    gaps = [b[0] - a[0] for a, b in zip(marks, marks[1:], strict=False)]
    problems = []
    if len(marks) < 3:
        problems.append("YouTube shows chapters only when there are at least 3")
    if any(g < 10 for g in gaps):
        problems.append("Every chapter must last at least 10 seconds")
    return {"text": "\n".join(lines), "valid": not problems, "problems": problems}


def normalise_music(src: Path, out: Path) -> None:
    """Bring a track to -16 LUFS, the voice's level, so the music level setting means the same for every track."""
    from ..ffutil import require, run

    partial = out.with_suffix(".partial" + out.suffix)
    run([require("ffmpeg"), "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(RATE),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", str(partial)])
    partial.replace(out)


def duration_label(seconds: float) -> str:
    return f"{math.floor(seconds / 60)}:{int(seconds % 60):02d}"
