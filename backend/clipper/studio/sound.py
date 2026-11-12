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
import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 48_000
EFFECT_GAP = 0.12          # seconds: entrances closer than this share one sound
EFFECT_GAIN = 0.16         # well under the voice


# A small bank of synthesised sounds. Each entrance picks a family that suits what
# appears, then a variant and a pitch, so two sounds in a row are never the same.
# Pitches come from a pentatonic scale, so a quick run of pops sounds musical.
PENTATONIC = [0, 2, 4, 7, 9, 12, 14, 16]


def _semitones(rng: np.random.Generator, spread: int = 8) -> float:
    return 2 ** (PENTATONIC[int(rng.integers(0, min(spread, len(PENTATONIC))))] / 12)


def _env(n: int, decay: float) -> np.ndarray:
    t = np.arange(n) / RATE
    attack = np.minimum(1.0, t / 0.004)
    return (attack * np.exp(-t * decay)).astype(np.float32)


def _sweep(start: float, end: float, n: int, speed: float) -> np.ndarray:
    t = np.arange(n) / RATE
    freq = end + (start - end) * np.exp(-t * speed)
    return np.sin(2 * np.pi * np.cumsum(freq) / RATE).astype(np.float32)


def bloop(rng: np.random.Generator) -> np.ndarray:
    """A soft falling 'bloop', for characters and things popping in."""
    k = _semitones(rng)
    n = int(RATE * rng.uniform(0.07, 0.11))
    return _sweep(rng.uniform(480, 620) * k, rng.uniform(170, 230) * k, n, rng.uniform(32, 48)) * _env(n, 36)


def plop(rng: np.random.Generator) -> np.ndarray:
    """Lower and rounder, with a tiny tap at the start: something landing."""
    k = _semitones(rng, 5)
    n = int(RATE * 0.12)
    body = _sweep(260 * k, 95 * k, n, 30) * _env(n, 28)
    tap = rng.standard_normal(n).astype(np.float32) * _env(n, 400) * 0.3
    return body + tap


def pluck(rng: np.random.Generator) -> np.ndarray:
    """A plucked string (Karplus-Strong), for words appearing."""
    freq = 330 * _semitones(rng)
    period = max(2, int(RATE / freq))
    n = int(RATE * 0.35)
    buf = rng.uniform(-1, 1, period).astype(np.float32)
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        out[i] = buf[i % period]
        buf[i % period] = 0.5 * (buf[i % period] + buf[(i + 1) % period]) * 0.996
    return out * _env(n, 9) * 0.8


def click(rng: np.random.Generator) -> np.ndarray:
    """A short, dry tick."""
    n = int(RATE * 0.025)
    noise = rng.standard_normal(n).astype(np.float32)
    return np.diff(noise, prepend=0) * _env(n, rng.uniform(180, 260)) * 0.5


def ding(rng: np.random.Generator) -> np.ndarray:
    """A small bell, for numbers: inharmonic partials that ring briefly."""
    base = 880 * _semitones(rng, 5)
    n = int(RATE * 0.6)
    t = np.arange(n) / RATE
    tone = sum(a * np.sin(2 * np.pi * base * r * t) * np.exp(-t * d)
               for r, a, d in ((1.0, 1.0, 6), (2.76, 0.45, 10), (5.4, 0.25, 16)))
    return (tone * np.minimum(1, t / 0.002)).astype(np.float32) * 0.6


def boing(rng: np.random.Generator) -> np.ndarray:
    """A springy wobble, for things that bounce or grow."""
    n = int(RATE * rng.uniform(0.25, 0.35))
    t = np.arange(n) / RATE
    base = rng.uniform(150, 210)
    freq = base * (1 + 0.6 * np.exp(-t * 8) * np.sin(2 * np.pi * rng.uniform(14, 20) * t)) * (1 + t * 1.5)
    return (np.sin(2 * np.pi * np.cumsum(freq) / RATE) * _env(n, 9)).astype(np.float32) * 0.8


def zip_up(rng: np.random.Generator) -> np.ndarray:
    """A quick rising sweep, for arrows drawing themselves."""
    n = int(RATE * rng.uniform(0.1, 0.16))
    t = np.arange(n) / RATE
    start, end = rng.uniform(250, 350), rng.uniform(900, 1300)
    freq = start + (end - start) * (t / t[-1]) ** 2
    return (np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.sin(np.pi * t / t[-1])).astype(np.float32) * 0.5


def whoosh(rng: np.random.Generator) -> np.ndarray:
    """Filtered noise that swells and fades; length and colour vary."""
    n = int(RATE * rng.uniform(0.2, 0.38))
    noise = rng.standard_normal(n).astype(np.float32)
    kernel = np.hanning(int(rng.integers(24, 160))).astype(np.float32)
    smooth = np.convolve(noise, kernel / kernel.sum(), mode="same")
    peak = rng.uniform(0.35, 0.65)
    x = np.linspace(0, 1, n)
    envelope = np.where(x < peak, x / peak, (1 - x) / (1 - peak)) ** 2
    return (smooth * envelope * 3.0).astype(np.float32) * 0.7


FAMILIES = {"bloop": (bloop, 1.0), "plop": (plop, 0.9), "pluck": (pluck, 0.8), "click": (click, 0.9),
            "ding": (ding, 0.55), "boing": (boing, 0.8), "zip": (zip_up, 0.7), "whoosh": (whoosh, 1.0)}
CHOICES = {   # what appears -> the families that suit it
    "character": ["bloop", "plop"], "prop": ["bloop", "plop", "pluck"], "text": ["pluck", "click", "bloop"],
    "number": ["ding"], "arrow": ["zip"], "slide": ["whoosh"], "bounce": ["boing"], "grow": ["boing", "bloop"],
}


def effect_events(scenes: list[dict], word_time, duration: float) -> list[tuple[float, str]]:
    """(time, kind of entrance) for every element entrance worth a sound."""
    events = []
    starts = [0.0] + [word_time(s["start_word"]) for s in scenes[1:]]
    for i, scene in enumerate(scenes):
        for e in scene["elements"]:
            if e["enter"] in ("none", "fade") and e["kind"] != "arrow":
                continue
            at = word_time(e["enter_word"]) if e.get("enter_word") is not None else starts[i]
            at = max(at, starts[i])
            if at >= duration:
                continue
            if e["kind"] == "arrow":
                kind = "arrow"
            elif e["enter"] == "slide":
                kind = "slide"
            elif e.get("motion") in ("bounce", "grow"):
                kind = e["motion"]
            else:
                kind = e["kind"]
            events.append((at, kind))
    events.sort()
    kept: list[tuple[float, str]] = []
    for at, kind in events:
        if not kept or at - kept[-1][0] >= EFFECT_GAP:
            kept.append((at, kind))
    return kept


def effects_track(events: list[tuple[float, str]], duration: float, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    track = np.zeros(int(RATE * duration) + RATE, dtype=np.float32)
    last = ""
    for at, kind in events:
        options = CHOICES.get(kind, ["bloop"])
        family = options[int(rng.integers(0, len(options)))]
        if family == last and len(options) > 1:     # never the same family twice in a row
            family = options[(options.index(family) + 1) % len(options)]
        last = family
        make, gain = FAMILIES[family]
        sound = make(rng) * gain
        i = int(at * RATE)
        track[i:i + len(sound)] += sound[:max(0, len(track) - i)] * EFFECT_GAIN
    return track[:int(RATE * duration)]


def write_effects(events: list[tuple[float, str]], duration: float, out: Path, seed: int = 7) -> None:
    pcm = (np.clip(effects_track(events, duration, seed), -1, 1) * 32767).astype("<i2")
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


def describe_track(path: Path | str, seconds: float = 60) -> dict:
    """Tempo, density, brightness and dynamics of a track, from up to a minute of its audio. No AI involved.

    Enough to tell a calm sparse bed from a busy bright one, which is what
    matters under a voice. `path` may be a URL: ffmpeg then reads only the
    stretch it needs.
    """
    from ..ffutil import require

    sr, hop = 22_050, 512

    def decode(*seek: str) -> np.ndarray:
        try:
            proc = subprocess.run([require("ffmpeg"), "-v", "error", *seek, "-t", str(seconds), "-i", str(path),
                                   "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, timeout=45)
        except subprocess.TimeoutExpired:
            return np.zeros(0, dtype=np.float32)
        return np.frombuffer(proc.stdout, dtype=np.float32)

    audio = decode("-ss", "20")
    if len(audio) < sr * 5:   # short track: analyse from the start
        audio = decode()
    if len(audio) < sr * 2:
        return {}
    frames = np.lib.stride_tricks.sliding_window_view(audio, 1024)[::hop] * np.hanning(1024)
    spectrum = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(1024, 1 / sr)
    raw = np.maximum(0, np.diff(spectrum, axis=0)).sum(axis=1)
    # Change relative to the track's energy: a steady tone barely changes, a drum hit changes a lot.
    rel = raw / (spectrum.sum(axis=1).mean() + 1e-9)
    fps = sr / hop
    # Tempo: remove the slow drift of the onset curve (a smooth curve always resembles itself
    # at short lags), then take the strongest real peak of its autocorrelation, 60-180 bpm.
    width = max(3, int(fps * 0.4))
    curve = raw - np.convolve(raw, np.ones(width) / width, mode="same")
    curve = np.convolve(curve, np.array([0.25, 0.5, 0.25]), mode="same")   # a beat between frames stays one peak
    curve = curve / (curve.std() + 1e-9)
    lags = np.arange(int(fps * 60 / 180) - 1, int(fps * 60 / 60) + 2)
    ac = np.array([np.dot(curve[:-lag], curve[lag:]) / (len(curve) - lag) for lag in lags])
    peaks = [i for i in range(1, len(ac) - 1) if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1] and ac[i] > 0.1
             and 58 <= 60 * fps / lags[i] <= 182]
    # A peak's strength is its area (a beat between two frames splits its height). A steady pulse
    # repeats almost as strongly every second beat, so prefer the fastest peak close to the best.
    area = {i: ac[i] + 0.5 * (ac[i - 1] + ac[i + 1]) for i in peaks}
    best = min((i for i in peaks if area[i] >= 0.85 * max(area.values())), key=lambda i: lags[i]) \
        if peaks else None
    bpm = 60 * fps / lags[best] if best is not None else 0.0
    threshold = max(0.15, rel.mean() + 1.5 * rel.std())
    onsets = int(((rel[1:-1] > threshold) & (rel[1:-1] > rel[:-2]) & (rel[1:-1] >= rel[2:])).sum())
    density = onsets / (len(audio) / sr)
    centroid = float((spectrum * freqs).sum(axis=1).sum() / (spectrum.sum() + 1e-9))
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    dynamics = float(np.percentile(rms, 90) - np.percentile(rms, 10))
    beat = density >= 0.3 and best is not None   # no onsets or no repeating pulse: no tempo
    tempo = "slow" if bpm < 85 else "fast" if bpm > 125 else "mid-tempo"
    words = [tempo if beat else "no clear beat",
             "sparse" if density < 1.5 else "busy" if density > 4 else "moderately busy",
             "dark" if centroid < 1200 else "bright" if centroid > 2800 else "warm",
             "steady" if dynamics < 12 else "dynamic"]
    return {"bpm": round(float(bpm)) if beat else None, "onsets_per_s": round(density, 2),
            "brightness_hz": round(centroid),
            "dynamics_db": round(dynamics, 1), "words": words}


def duration_label(seconds: float) -> str:
    return f"{math.floor(seconds / 60)}:{int(seconds % 60):02d}"
