"""A lightweight pre-caption observer for accidental visually empty runs.

Frames are sampled in the existing render loop, never decoded or drawn twice.
Background colour and texture come from the border; foreground occupancy and
edge density are measured only in excess of that border baseline. This is
advice, not a render or approval gate.
"""

from typing import Protocol

import numpy as np
from PIL import Image

SAMPLE_HZ = 6
SAMPLE_WIDTH = 180
MIN_EMPTY_SECONDS = 0.5
OCCUPANCY_MIN = 0.008  # about 260 pixels at 180x320: a small readable drawn object
EDGE_MIN = 0.003  # excess strong edges beyond a textured border baseline
BORDER = 4


class FrameObserver(Protocol):
    def observe(self, t: float, frame) -> None: ...

    def finish(self) -> dict: ...


def _rgb(frame) -> np.ndarray:
    if isinstance(frame, Image.Image):
        image = frame.convert("RGB")
    else:
        data = np.asarray(frame)
        if data.ndim != 3 or data.shape[2] < 3:
            raise ValueError("frame is not an RGB image")
        image = Image.fromarray(data[..., :3].astype(np.uint8), "RGB")
    if image.width > SAMPLE_WIDTH:
        image = image.resize((SAMPLE_WIDTH, max(1, round(image.height * SAMPLE_WIDTH / image.width))), Image.BILINEAR)
    return np.asarray(image, dtype=np.float32)


def measure(frame) -> dict[str, float | bool]:
    """Foreground occupancy and edge density relative to the frame's own border background."""
    rgb = _rgb(frame)
    h, w = rgb.shape[:2]
    border_mask = np.zeros((h, w), bool)
    edge = min(BORDER, max(1, min(h, w) // 4))
    border_mask[:edge] = border_mask[-edge:] = True
    border_mask[:, :edge] = border_mask[:, -edge:] = True
    interior = ~border_mask
    border = rgb[border_mask]
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - background, axis=2)
    colour_threshold = max(12.0, float(np.percentile(distance[border_mask], 95)) + 6.0)
    colour = distance > colour_threshold
    border_occupancy = float(colour[border_mask].mean())
    occupancy = max(0.0, float(colour[interior].mean()) - 1.5 * border_occupancy)

    grey = rgb.mean(axis=2)
    gradient = np.hypot(np.diff(grey, axis=1, append=grey[:, -1:]), np.diff(grey, axis=0, append=grey[-1:, :]))
    gradient_threshold = max(14.0, float(np.percentile(gradient[border_mask], 95)) + 5.0)
    strong = gradient > gradient_threshold
    border_edges = float(strong[border_mask].mean())
    edge_density = max(0.0, float(strong[interior].mean()) - 1.5 * border_edges)
    meaningful = occupancy >= OCCUPANCY_MIN or edge_density >= EDGE_MIN
    return {"occupancy": round(occupancy, 4), "edge_density": round(edge_density, 4), "meaningful": meaningful}


class Observer:
    """Collect six measurements a second and, for silent loops, compare the seam."""

    def __init__(self, words: list[dict], voice_duration: float, loop: bool = False):
        self.words = words
        self.voice_duration = max(0.0, voice_duration)
        self.loop = loop
        self.next_sample = 0.0
        self.samples: list[dict] = []
        self.problem = ""
        self.first_frame: np.ndarray | None = None
        self.last_frame: np.ndarray | None = None

    def _active(self, t: float) -> bool:
        return any(float(word.get("s", 0)) - 0.08 <= t <= float(word.get("e", 0)) + 0.08 for word in self.words)

    def observe(self, t: float, frame) -> None:
        if t + 1e-6 < self.next_sample or t >= self.voice_duration:
            return
        self.next_sample = (int(t * SAMPLE_HZ) + 1) / SAMPLE_HZ
        try:
            result = measure(frame)
            self.samples.append({"at": round(t, 3), "active": self._active(t), **result})
            if self.loop:
                sampled = _rgb(frame)
                if self.first_frame is None:
                    self.first_frame = sampled
                self.last_frame = sampled
        except Exception as err:  # noqa: BLE001 - advisory QA can never fail a render
            self.problem = f"Visual QA stopped: {str(err)[:160]}"

    def finish(self) -> dict:
        try:
            first = next((sample["at"] for sample in self.samples if sample["meaningful"]), None)
            runs, start, last = [], None, None
            for sample in self.samples:
                empty = sample["active"] and not sample["meaningful"]
                contiguous = last is not None and sample["at"] - last <= 1.5 / SAMPLE_HZ
                if empty and (start is None or contiguous):
                    start = sample["at"] if start is None else start
                    last = sample["at"]
                    continue
                if start is not None:
                    self._keep_run(runs, start, last)
                start, last = (sample["at"], sample["at"]) if empty else (None, None)
            if start is not None:
                self._keep_run(runs, start, last)
            warnings = [
                f"Near-empty visuals from {run['start_s']:.1f} to {run['end_s']:.1f} s while narration is active."
                for run in runs
            ]
            seam = None
            if self.loop and self.first_frame is not None and self.last_frame is not None:
                difference = float(np.abs(self.first_frame - self.last_frame).mean() / 255.0)
                seam = {
                    "difference": round(difference, 4),
                    "similarity": round(1.0 - difference, 4),
                    "passed": difference <= 0.08,
                    "first_sample_s": self.samples[0]["at"],
                    "last_sample_s": self.samples[-1]["at"],
                }
                if not seam["passed"]:
                    warnings.append(
                        "The final visual differs from the opening; restore the opening composition "
                        "before the loop ends."
                    )
            return {
                "sample_hz": SAMPLE_HZ,
                "samples": len(self.samples),
                "first_visual_event_s": first,
                "empty_runs": runs,
                "loop_seam": seam,
                "warnings": warnings,
                "problem": self.problem or None,
            }
        except Exception as err:  # noqa: BLE001 - advisory QA can never fail a render
            return {
                "sample_hz": SAMPLE_HZ,
                "samples": len(self.samples),
                "first_visual_event_s": None,
                "empty_runs": [],
                "loop_seam": None,
                "warnings": [],
                "problem": f"Visual QA could not be summarized: {str(err)[:160]}",
            }

    @staticmethod
    def _keep_run(runs: list[dict], start: float, last: float) -> None:
        end = last + 1 / SAMPLE_HZ
        duration = end - start
        if duration + 0.005 >= MIN_EMPTY_SECONDS:  # sample timestamps are stored to milliseconds
            runs.append({"start_s": round(start, 2), "end_s": round(end, 2), "duration_s": round(duration, 2)})
