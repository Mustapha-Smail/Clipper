"""Decide, frame by frame, how a source is fitted into the clip's frame.

Two layouts:
  TRACK  crop a window of the clip's shape, centred on the speaker's face
  FIT    show the whole frame over a blurred copy of itself

A source that already has the clip's shape (a 16:9 video cut into horizontal
clips) fills the frame as it is: there is nothing to decide and nothing to scan.

The clip is split into shots at hard cuts. Each shot gets one layout: TRACK when
one face clearly dominates (or several faces fit inside the crop together), FIT
when there is no face or when two people sit too far apart to share a crop.
Without speaker detection, guessing which of two distant people is talking would
be wrong half the time, so FIT is the safe answer there.
"""

import os
import subprocess
import urllib.request
from dataclasses import dataclass

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # must be set before cv2 loads

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from .config import FACE_MODEL, FACE_MODEL_URL, FORMATS, FPS
from .ffutil import require

ANALYSIS_W = 640
SAMPLE_EVERY = 5          # run face detection on every 5th frame (6 per second)
CUT_THRESHOLD = 28.0      # mean absolute grey-level change that counts as a hard cut
MIN_SHOT_FRAMES = FPS // 2
MIN_FACE_W = 0.05         # ignore faces narrower than 5% of the frame (background people)
SAME_FACE_DX = 0.12       # detections this close horizontally belong to the same person
STATIC_RANGE = 0.06       # a face that moves less than this gets a locked-off crop
KEEP = 0.8                # a source narrower than the clip is cropped if this much of it stays, else FIT
VERTICAL = FORMATS["vertical"]


@dataclass
class SubtitleFix:
    mode: str                    # "cover" (blur the band) or "crop" (cut the frame above it)
    band: tuple[float, float]    # top and bottom of the subtitles, as fractions of source height


@dataclass
class FramePlan:
    track: np.ndarray  # bool per frame: True = TRACK, False = FIT
    cx: np.ndarray     # crop centre per frame, as a fraction of source width

    def at(self, i: int) -> tuple[bool, float]:
        if len(self.track) == 0:
            return False, 0.5
        i = min(i, len(self.track) - 1)
        return bool(self.track[i]), float(self.cx[i])


def crop_fraction(width: int, height: int, size: tuple[int, int] = VERTICAL) -> float:
    """Width of the crop window (the shape of a `size` clip) as a fraction of the source width."""
    return min(1.0, (height * size[0] / size[1]) / width)


def _detector(width: int, height: int):
    if not FACE_MODEL.exists():
        FACE_MODEL.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL)
    return cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (width, height), 0.6, 0.3, 50)


def _plan_shot(samples: list[tuple[int, list[tuple[float, float]]]], n_frames: int,
               first_frame: int, crop_frac: float) -> tuple[bool, np.ndarray]:
    """samples: (frame index, [(face centre x, face area)]) for one shot."""
    fit = (False, np.full(n_frames, 0.5))
    if not samples:
        return fit

    # Group detections into people by horizontal position.
    people: list[dict] = []
    for frame_idx, faces in samples:
        used = set()
        for cx, area in sorted(faces, key=lambda f: -f[1]):
            best = None
            for k, person in enumerate(people):
                dist = abs(np.mean(person["cx"]) - cx)
                if k not in used and dist < SAME_FACE_DX and (best is None or dist < best[1]):
                    best = (k, dist)
            if best is None:
                people.append({"cx": [], "frames": [], "area": 0.0})
                best = (len(people) - 1, 0.0)
            used.add(best[0])
            person = people[best[0]]
            person["cx"].append(cx)
            person["frames"].append(frame_idx)
            person["area"] += area

    people = [p for p in people if len(p["frames"]) >= 0.3 * len(samples)]
    if not people:
        return fit

    main = max(people, key=lambda p: p["area"])
    rivals = [p for p in people if p is not main and p["area"] >= 0.5 * main["area"]]
    half = crop_frac / 2

    def clamp(values):
        return np.clip(values, half, 1 - half)

    if rivals:
        centres = [float(np.median(p["cx"])) for p in [main, *rivals]]
        if max(centres) - min(centres) > 0.6 * crop_frac:
            return fit
        middle = (max(centres) + min(centres)) / 2
        return True, clamp(np.full(n_frames, middle))

    xs = np.array(main["cx"])
    if xs.max() - xs.min() < STATIC_RANGE or len(xs) < 3:
        return True, clamp(np.full(n_frames, float(np.median(xs))))

    # The speaker moves: follow with a one-second moving average so the crop glides.
    frames = np.arange(first_frame, first_frame + n_frames)
    path = np.interp(frames, main["frames"], xs)
    window = min(FPS, n_frames)
    padded = np.pad(path, (window // 2, window - 1 - window // 2), mode="edge")
    smooth = np.convolve(padded, np.ones(window) / window, mode="valid")
    return True, clamp(smooth)


def analyze(src, start: float, duration: float, width: int, height: int,
            crop_height: int | None = None, size: tuple[int, int] = VERTICAL) -> FramePlan:
    """Scan the clip at low resolution and return the per-frame layout plan.

    `crop_height` is the usable source height when the bottom is cut off to
    remove subtitles; it narrows the crop window the face has to fit in.
    `size` is the clip's (width, height). When the crop window is as wide as the
    source there is no side to choose, so the scan is skipped and the plan is empty.
    """
    crop_frac = crop_fraction(width, crop_height or height, size)
    if crop_frac >= 1.0:
        return FramePlan(track=np.zeros(0, dtype=bool), cx=np.zeros(0))
    aw = ANALYSIS_W
    ah = max(2, int(round(aw * height / width / 2)) * 2)
    detector = _detector(aw, ah)
    proc = subprocess.Popen([
        require("ffmpeg"), "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
        "-vf", f"fps={FPS},scale={aw}:{ah}", "-an",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    frame_bytes = aw * ah * 3
    cuts = [0]
    samples: list[tuple[int, list[tuple[float, float]]]] = []
    previous = None
    i = 0
    while True:
        buf = proc.stdout.read(frame_bytes)
        if len(buf) < frame_bytes:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(ah, aw, 3)
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(grey, (64, 36), interpolation=cv2.INTER_AREA).astype(np.int16)
        if (previous is not None and i - cuts[-1] >= MIN_SHOT_FRAMES
                and np.abs(small - previous).mean() > CUT_THRESHOLD):
            cuts.append(i)
        previous = small

        if i % SAMPLE_EVERY == 0:
            _, found = detector.detect(frame)
            faces = []
            for x, _y, w, h in (found[:, :4] if found is not None else []):
                if w / aw >= MIN_FACE_W:
                    faces.append(((x + w / 2) / aw, (w / aw) * (h / ah)))
            samples.append((i, faces))
        i += 1
    proc.wait()

    n_total = i
    track = np.zeros(n_total, dtype=bool)
    cx = np.full(n_total, 0.5)
    bounds = cuts + [n_total]
    for first, last in zip(bounds[:-1], bounds[1:], strict=False):
        if last <= first:
            continue
        shot_samples = [s for s in samples if first <= s[0] < last]
        is_track, path = _plan_shot(shot_samples, last - first, first, crop_frac)
        track[first:last] = is_track
        cx[first:last] = path
    return FramePlan(track=track, cx=cx)


def usable_height(height: int, fix: SubtitleFix | None) -> int:
    """Source rows that remain once a "crop" fix has removed the subtitle band."""
    if fix and fix.mode == "crop":
        return max(2, int(fix.band[0] * height))
    return height


def _blur_rows(out: np.ndarray, top: int, bottom: int) -> None:
    """Smear a horizontal strip of the output so text in it becomes unreadable."""
    out_h, out_w = out.shape[:2]
    top, bottom = max(0, top), min(out_h, bottom)
    if bottom - top < 4:
        return
    strip = out[top:bottom]
    small = cv2.resize(strip, (out_w // 16, max(1, (bottom - top) // 16)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 3)
    smooth = cv2.resize(small, (out_w, bottom - top), interpolation=cv2.INTER_LINEAR)
    out[top:bottom] = (smooth * 0.8).astype(np.uint8)


def compose(frame: np.ndarray, is_track: bool, cx: float,
            fix: SubtitleFix | None = None, size: tuple[int, int] = VERTICAL) -> np.ndarray:
    """Build one output frame of `size` (width, height) from a source frame."""
    out_w, out_h = size
    full_height = frame.shape[0]
    frame = frame[:usable_height(full_height, fix)]
    height, width = frame.shape[:2]
    crop_w = min(width, int(round(height * out_w / out_h)))
    crop_h = min(height, int(round(crop_w * out_h / out_w)))
    y0 = (height - crop_h) // 2

    # A source that already has the clip's shape, or nearly, needs no layout decision.
    if is_track or (crop_w == width and crop_h >= KEEP * height):
        x0 = int(round(cx * width - crop_w / 2))
        x0 = max(0, min(width - crop_w, x0))
        crop = frame[y0:y0 + crop_h, x0:x0 + crop_w]
        out = cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
        scale, shift = out_h / crop_h, -y0 * out_h / crop_h
    else:
        # FIT: blurred, darkened copy in the background, whole frame in the middle.
        x0 = (width - crop_w) // 2
        background = cv2.resize(frame[y0:y0 + crop_h, x0:x0 + crop_w], (out_w // 4, out_h // 4),
                                interpolation=cv2.INTER_AREA)
        background = cv2.GaussianBlur(background, (0, 0), 10)
        out = cv2.resize(background, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        out = (out * 0.55).astype(np.uint8)

        fit = min(out_w / width, out_h / height)
        fit_w = min(out_w, int(round(width * fit / 2)) * 2)
        fit_h = min(out_h, int(round(height * fit / 2)) * 2)
        left, top = (out_w - fit_w) // 2, (out_h - fit_h) // 2
        out[top:top + fit_h, left:left + fit_w] = cv2.resize(frame, (fit_w, fit_h), interpolation=cv2.INTER_CUBIC)
        scale, shift = fit_h / height, top

    if fix and fix.mode == "cover":
        # Map the band from source rows to output rows for whichever layout was used.
        _blur_rows(out, int(fix.band[0] * full_height * scale + shift),
                   int(np.ceil(fix.band[1] * full_height * scale + shift)))
    return out
