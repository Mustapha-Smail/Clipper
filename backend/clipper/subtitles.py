"""Detect text that is burned into a source video: subtitles or caption cards.

Frames are sampled at moments when someone is speaking and the lower half is
read with OCR. Captions show up as text that sits in the same horizontal band
in most frames while its wording keeps changing. A logo or a fixed title also
sits in one place, but its wording never changes, so it is left alone.

Each line is also compared with the transcript to tell word-for-word subtitles
from summary captions. That is reported, not used for the decision: both get in
the way of a vertical crop.
"""

import logging
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np

from .ffutil import require

SAMPLES = 20
LOOK_BELOW = 0.5          # only the lower half of the frame is searched
OCR_WIDTH = 640
ROWS = 200                # vertical resolution of the band search
MIN_PRESENCE = 0.5        # share of sampled frames that must show text in the band
MIN_DISTINCT = 3          # different wordings needed to call it captions, not a logo
WINDOW = 5.0              # seconds of speech around a frame that a subtitle line may show
BAND_PAD = 0.012


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _grab(video: Path, t: float) -> np.ndarray | None:
    proc = subprocess.run(
        [require("ffmpeg"), "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={OCR_WIDTH}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)


def _matches_speech(text: str, nearby: set[str]) -> bool:
    tokens = _tokens(text)
    return len(tokens) >= 2 and sum(t in nearby for t in tokens) / len(tokens) >= 0.6


def detect(video: Path, words: list[dict], progress=None) -> dict:
    """Return {detected, kind, hit_rate, samples, band, example}.

    kind: "verbatim" (matches the speech), "summary" (caption cards), or None.
    band: [top, bottom] as fractions of the frame height.
    """
    empty = {"detected": False, "kind": None, "hit_rate": 0.0, "samples": 0,
             "band": None, "example": None}
    if len(words) < 8:
        return empty

    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    engine = RapidOCR()

    step = max(1, len(words) // SAMPLES)
    picked = words[step // 2::step][:SAMPLES]
    frames: list[list[tuple[int, int, str, bool]]] = []   # per frame: (top row, bottom row, text, matches)
    for k, word in enumerate(picked):
        t = (word["s"] + word["e"]) / 2
        frame = _grab(video, t)
        if progress:
            progress((k + 1) / len(picked))
        if frame is None:
            continue
        height = frame.shape[0]
        offset = int(height * LOOK_BELOW)
        found = engine(frame[offset:])
        lines = []
        if found.boxes is not None:
            nearby = {tok for w in words if t - WINDOW <= w["s"] <= t + WINDOW
                      for tok in _tokens(w["w"])}
            for box, text, score in zip(found.boxes, found.txts, found.scores, strict=False):
                if score < 0.6 or len("".join(_tokens(text))) < 3:
                    continue
                ys = [point[1] for point in box]
                top = int((offset + min(ys)) / height * ROWS)
                bottom = min(ROWS, int(np.ceil((offset + max(ys)) / height * ROWS)))
                lines.append((top, bottom, text, _matches_speech(text, nearby)))
        frames.append(lines)

    samples = len(frames)
    if samples < 6:
        return empty

    # How many frames have text at each row; the busiest row anchors the band.
    coverage = np.zeros(ROWS, dtype=int)
    for lines in frames:
        covered = np.zeros(ROWS, dtype=bool)
        for top, bottom, _, _ in lines:
            covered[top:bottom] = True
        coverage += covered
    peak = int(coverage.argmax())
    if coverage[peak] < MIN_PRESENCE * samples:
        return {**empty, "samples": samples, "hit_rate": round(coverage[peak] / samples, 2)}

    # Grow the band over neighbouring rows that carried text in at least two frames,
    # so a second caption line is included but a one-off three-line card is not.
    top = bottom = peak
    while top > 0 and coverage[top - 1] >= 2:
        top -= 1
    while bottom < ROWS - 1 and coverage[bottom + 1] >= 2:
        bottom += 1
    bottom += 1

    in_band = [[line for line in lines if line[0] < bottom and line[1] > top] for lines in frames]
    wordings = {" ".join(_tokens(line[2])) for lines in in_band for line in lines}
    hit_rate = round(sum(1 for lines in in_band if lines) / samples, 2)
    if len(wordings) < MIN_DISTINCT:
        return {**empty, "samples": samples, "hit_rate": hit_rate}   # static text: a logo or a title

    matched = sum(1 for lines in in_band if any(line[3] for line in lines))
    with_text = sum(1 for lines in in_band if lines)
    example = next(line[2] for lines in in_band for line in lines)
    return {
        "detected": True,
        "kind": "verbatim" if matched >= 0.6 * with_text else "summary",
        "hit_rate": hit_rate,
        "samples": samples,
        "band": [round(max(0.0, top / ROWS - BAND_PAD), 3), round(min(1.0, bottom / ROWS + BAND_PAD), 3)],
        "example": example,
    }

