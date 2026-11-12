"""Turn CMU motion-capture clips (BVH) into moves for the ink figure.

A developer tool, run by hand; the app only reads its output,
`clipper/assets/moves.json`. Each move is a list of poses at 24 frames a
second in the figure's own terms (torso lean, head tilt, the two angles of
each arm and leg, how far the hips moved and rose), measured from the 3D
skeleton seen from the side (walks, jumps, kicks) or from the front (waves,
jumping jacks, cartwheels).

The CMU Graphics Lab Motion Capture Database (mocap.cs.cmu.edu), in Bruce
Hahne's BVH conversion, is free for research and commercial projects. The
database was created with funding from NSF EIA-0196217.

    uv run python tools/mocap_import.py BVH_FOLDER             # write moves.json
    uv run python tools/mocap_import.py BVH_FOLDER --preview OUT  # stick-figure strips to pick windows
"""

import json
import math
import sys
from pathlib import Path

import numpy as np

FPS = 24
LEG = 80.0          # the figure's thigh, in its drawing units

# Columns of a frame: 0 lean, 1 tilt, 2-3 back arm, 4-5 front arm, 6-7 back leg, 8-9 front leg,
# 10 dx (how far the hips travelled), 11 air (how high the lowest point of the body is off the ground).
# How each move's window is chosen in its clip:
#   ("cycle", start, end)           one stride from that stretch, to loop
#   ("range", start, end)           those seconds
#   ("peak", key, before, after)    around the frame where `key` is largest
#   ("busy", columns, seconds)      the stretch where those columns move most
MOVES = {
    "walk": ("07_01", "side", ("cycle", 1.0, 4.0), "walking"),
    "run": ("09_01", "side", ("cycle", 0.0, 9.0), "running"),
    "march": ("20_06", "side", ("cycle", 1.0, 4.0), "marching like a soldier"),
    "zombie": ("20_08", "side", ("cycle", 0.5, 4.0), "a stiff zombie shuffle"),
    "jump": ("13_39", "side", ("peak", "lift", 1.0, 0.8), "a jump on the spot"),
    "kick": ("10_01", "side", ("peak", "kick", 0.8, 0.7), "kicking a ball"),
    "signal": ("13_26", "front", ("busy", [4, 5], 2.0), "big two-armed waving, like signalling a plane"),
    "laugh": ("13_14", "front", ("busy", [0, 1], 2.5), "laughing, doubled up"),
    "dance": ("60_01", "front", ("busy", [2, 4, 6, 8], 3.0), "a few dance steps"),
    "cartwheel": ("49_06", "front", ("peak", "upside_down", 1.0, 1.0), "a cartwheel"),
    "throw": ("33_01", "side", ("peak", "arm_speed", 0.8, 0.6), "throwing a ball"),
    "pickup": ("26_09", "side", ("peak", "crouch", 1.3, 1.3), "bending down to pick something up"),
    "stumble": ("22_12", "side", ("range", 0.0, 1.6), "stumbling forward"),
    "jacks": ("13_29", "front", ("range", 0.0, 2.5), "jumping jacks"),
}
KEYS = {
    "lift": lambda f: f[:, 11],
    "crouch": lambda f: np.abs(f[:, 0]) + np.abs(f[:, 6] - f[:, 7]),        # bent over, knees bent
    "kick": lambda f: np.maximum(f[:, 6], f[:, 8]),
    "upside_down": lambda f: np.abs((f[:, 0] + 180) % 360 - 180),
    "arm_speed": lambda f: np.abs(np.gradient(np.maximum(f[:, 2], f[:, 4]))),
}


def window(frames: np.ndarray, select: tuple) -> np.ndarray:
    kind = select[0]
    if kind in ("cycle", "range"):
        part = frames[int(select[1] * FPS):int(select[2] * FPS)]
        return cycle(part) if kind == "cycle" else part
    if kind == "peak":
        _, key, before, after = select
        at = int(np.argmax(KEYS[key](frames)))
        return frames[max(0, at - int(before * FPS)):at + int(after * FPS)]
    _, columns, seconds = select
    n = int(seconds * FPS)
    motion = np.abs(np.diff(frames[:, columns], axis=0)).sum(axis=1)
    totals = np.convolve(motion, np.ones(n), mode="valid")
    at = int(np.argmax(totals))
    return frames[at:at + n]


# --- reading BVH ----------------------------------------------------------------------------

class Joint:
    def __init__(self, name: str, parent: "Joint | None"):
        self.name, self.parent = name, parent
        self.offset = np.zeros(3)
        self.channels: list[str] = []
        self.children: list[Joint] = []
        self.first = 0      # index of its first channel in a frame


def read_bvh(path: Path) -> tuple[list[Joint], np.ndarray, float]:
    tokens = path.read_text().split()
    joints: list[Joint] = []
    stack: list[Joint] = []
    i = 0
    channel_count = 0
    while tokens[i] != "MOTION":
        word = tokens[i]
        if word in ("ROOT", "JOINT"):
            joint = Joint(tokens[i + 1], stack[-1] if stack else None)
            if joint.parent:
                joint.parent.children.append(joint)
            joints.append(joint)
            i += 2
        elif word == "End":                      # an end site: a joint without channels
            joint = Joint(f"{stack[-1].name}_end", stack[-1])
            stack[-1].children.append(joint)
            joints.append(joint)
            i += 2
        elif word == "{":
            stack.append(joints[-1])
            i += 1
        elif word == "}":
            stack.pop()
            i += 1
        elif word == "OFFSET":
            stack[-1].offset = np.array([float(x) for x in tokens[i + 1:i + 4]])
            i += 4
        elif word == "CHANNELS":
            n = int(tokens[i + 1])
            stack[-1].channels = tokens[i + 2:i + 2 + n]
            stack[-1].first = channel_count
            channel_count += n
            i += 2 + n
        else:
            i += 1
    frames = int(tokens[i + 2])
    frame_time = float(tokens[i + 5])
    data = np.array(tokens[i + 6:i + 6 + frames * channel_count], dtype=float).reshape(frames, channel_count)
    return joints, data, frame_time


def _axis(name: str, deg: np.ndarray) -> np.ndarray:
    r = np.radians(deg)
    c, s = np.cos(r), np.sin(r)
    one, zero = np.ones_like(r), np.zeros_like(r)
    if name == "X":
        rows = [[one, zero, zero], [zero, c, -s], [zero, s, c]]
    elif name == "Y":
        rows = [[c, zero, s], [zero, one, zero], [-s, zero, c]]
    else:
        rows = [[c, -s, zero], [s, c, zero], [zero, zero, one]]
    return np.stack([np.stack(r, axis=-1) for r in rows], axis=-2)     # (frames, 3, 3)


def positions(joints: list[Joint], data: np.ndarray) -> dict[str, np.ndarray]:
    """World position of every joint in every frame: name -> (frames, 3)."""
    frames = len(data)
    rot: dict[str, np.ndarray] = {}
    pos: dict[str, np.ndarray] = {}
    for joint in joints:          # parents always come before their children
        local = np.broadcast_to(np.eye(3), (frames, 3, 3)).copy()
        move = np.zeros((frames, 3))
        for k, channel in enumerate(joint.channels):
            column = data[:, joint.first + k]
            if channel.endswith("rotation"):
                local = local @ _axis(channel[0], column)
            else:
                move[:, "XYZ".index(channel[0])] = column
        if joint.parent is None:
            rot[joint.name] = local
            pos[joint.name] = joint.offset + move
        else:
            parent_rot = rot[joint.parent.name]
            pos[joint.name] = pos[joint.parent.name] + parent_rot @ joint.offset
            rot[joint.name] = parent_rot @ local
    return pos


# --- from 3D to the figure's angles -----------------------------------------------------------

def angles(pos: dict[str, np.ndarray], view: str) -> np.ndarray:
    """Per frame: lean, tilt, back arm (upper, lower), front arm, back leg, front leg, dx, lift."""
    up = np.array([0.0, 1.0, 0.0])
    left = pos["LeftUpLeg"] - pos["RightUpLeg"]
    left[:, 1] = 0
    left = left.mean(axis=0)
    left /= np.linalg.norm(left)
    forward = np.cross(left, up)
    axis = forward if view == "side" else -left    # front view: the figure's right is "forward"

    def flat(name: str) -> np.ndarray:
        p = pos[name]
        return np.stack([p @ axis, p @ up], axis=1)       # (frames, 2): along, up

    def angle(a: str, b: str) -> np.ndarray:
        d = flat(b) - flat(a)
        return np.degrees(np.arctan2(d[:, 0], -d[:, 1]))   # 0 = straight down, + = forward

    thigh = np.linalg.norm(pos["LeftLeg"][0] - pos["LeftUpLeg"][0])
    scale = LEG / thigh
    hips = flat("Hips")
    # how far the lowest point of the body (toes, or hands in a cartwheel) is off the ground
    lowest = np.min(np.stack([flat(j)[:, 1] for j in ("LeftToeBase", "RightToeBase", "LeftHand", "RightHand",
                                                       "LeftFoot", "RightFoot")]), axis=0)
    ground = np.percentile(lowest, 5)
    torso = flat("Neck") - hips
    lean = np.degrees(np.arctan2(torso[:, 0], torso[:, 1]))
    head = flat("Head") - flat("Neck")
    tilt = np.degrees(np.arctan2(head[:, 0], head[:, 1])) - lean
    columns = [lean, tilt,
               angle("LeftArm", "LeftForeArm"), angle("LeftForeArm", "LeftHand"),
               angle("RightArm", "RightForeArm"), angle("RightForeArm", "RightHand"),
               angle("LeftUpLeg", "LeftLeg"), angle("LeftLeg", "LeftFoot"),
               angle("RightUpLeg", "RightLeg"), angle("RightLeg", "RightFoot"),
               (hips[:, 0] - hips[0, 0]) * scale,
               np.maximum(0, (lowest - ground) * scale - 4)]
    return np.stack(columns, axis=1)


def resample(frames: np.ndarray, frame_time: float) -> np.ndarray:
    step = 1 / FPS / frame_time
    idx = np.arange(0, len(frames) - 1, step)
    lo = np.floor(idx).astype(int)
    k = (idx - lo)[:, None]
    a, b = frames[lo], frames[np.minimum(lo + 1, len(frames) - 1)]
    diff = (b - a + 180) % 360 - 180                       # angles take the short way round
    diff[:, 10:] = (b - a)[:, 10:]
    return a + diff * k


def cycle(frames: np.ndarray) -> np.ndarray:
    """One full stride of a walk or run, so it loops without a jump."""
    thigh = frames[:, 6] - frames[:, 8]                   # left thigh relative to right
    thigh = thigh - thigh.mean()
    best, period = -1.0, len(frames)
    for lag in range(8, len(frames) // 2):
        score = float(np.dot(thigh[:-lag], thigh[lag:]) / (len(thigh) - lag))
        if score > best:
            best, period = score, lag
    start = int(np.argmax(thigh[:period]))
    return frames[start:start + period]


def convert(folder: Path) -> dict:
    out = {"fps": FPS, "columns": ["lean", "tilt", "back_arm_upper", "back_arm_lower", "front_arm_upper",
                                   "front_arm_lower", "back_leg_upper", "back_leg_lower", "front_leg_upper",
                                   "front_leg_lower", "dx", "air"],
           "source": "CMU Graphics Lab Motion Capture Database (mocap.cs.cmu.edu), BVH by B. Hahne; "
                     "created with funding from NSF EIA-0196217",
           "moves": {}}
    for name, (clip, view, select, what) in MOVES.items():
        joints, data, frame_time = read_bvh(folder / f"{clip}.bvh")
        frames = resample(angles(positions(joints, data[1:]), view), frame_time)    # frame 0 is a T-pose
        loop = select[0] == "cycle"
        frames = window(frames, select).copy()
        frames[:, 10] -= frames[0, 10]
        out["moves"][name] = {"clip": clip, "view": view, "loop": loop, "what": what,
                              "frames": np.round(frames, 1).tolist()}
        print(f"{name:10s} {clip} {view:5s} {len(frames) / FPS:5.2f} s{' loop' if loop else ''}")
    return out


# --- previews ------------------------------------------------------------------------------

def preview(folder: Path, out: Path) -> None:
    """A strip of stick figures every quarter second for each clip, with times, to choose windows."""
    from PIL import Image, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    for name, (clip, view, select, _what) in MOVES.items():
        joints, data, frame_time = read_bvh(folder / f"{clip}.bvh")
        frames = window(resample(angles(positions(joints, data[1:]), view), frame_time), select)
        step = 3
        picks = frames[::step][:96]
        cols = 24
        image = Image.new("RGB", (cols * 90, ((len(picks) + cols - 1) // cols) * 140), "white")
        draw = ImageDraw.Draw(image)
        for n, f in enumerate(picks):
            ox, oy = (n % cols) * 90 + 45, (n // cols) * 140 + 70
            for x, y in skeleton(f, 0.22):
                draw.line([(ox + a, oy + b) for a, b in zip(x, y, strict=True)], fill="black", width=2)
            draw.text((ox - 40, oy + 52), f"{n * step / FPS:.2f}", fill="red")
        image.save(out / f"{name}.png")


def skeleton(f, k: float):
    """Line segments of the figure for one frame (for previews only)."""
    def d(deg):
        r = math.radians(deg)
        return math.sin(r), math.cos(r)
    hip = (0.0, -f[11] * k)
    up = d(180 - f[0])
    neck = (hip[0] + up[0] * 140 * k, hip[1] + up[1] * 140 * k)
    head = d(180 - f[0] - f[1])
    top = (neck[0] + head[0] * 60 * k, neck[1] + head[1] * 60 * k)
    segs = [((hip[0], neck[0]), (hip[1], neck[1])), ((neck[0], top[0]), (neck[1], top[1]))]
    for a, b, origin in ((2, 3, neck), (4, 5, neck), (6, 7, hip), (8, 9, hip)):
        u, low = d(f[a]), d(f[b])
        mid = (origin[0] + u[0] * 74 * k, origin[1] + u[1] * 74 * k)
        end = (mid[0] + low[0] * 74 * k, mid[1] + low[1] * 74 * k)
        segs += [((origin[0], mid[0]), (origin[1], mid[1])), ((mid[0], end[0]), (mid[1], end[1]))]
    return segs


if __name__ == "__main__":
    source = Path(sys.argv[1])
    if "--preview" in sys.argv:
        preview(source, Path(sys.argv[sys.argv.index("--preview") + 1]))
    else:
        target = Path(__file__).resolve().parents[1] / "clipper" / "assets" / "moves.json"
        target.write_text(json.dumps(convert(source), separators=(",", ":")))
        print(f"wrote {target} ({target.stat().st_size // 1024} kB)")
