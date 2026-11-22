"""Application-side bridge to the trusted Blender 3D renderer.

The storyboard cleaner owns the language and referential validation.  This
module accepts its canonical parsed lines (never raw model text), writes the
small resolved render contract, and supervises the Blender process.  The
Blender-side script still uses an allow-list as a defence in depth boundary.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from ..db import session_scope
from ..db.models import Storyboard, utcnow
from ..jobs import Reporter
from . import voice as voice_mod

CONTRACT_VERSION = 1
ASSET_PACK_VERSION = 1
DEFAULT_FPS = 24
DEFAULT_SAMPLES = 32
DEFAULT_TIMEOUT = 60 * 60
RUNNER = Path(__file__).parents[1] / "blender" / "render_scene.py"
DIMENSIONS = {"short": (1080, 1920), "long": (1920, 1080)}
# Mesa's CPU fallbacks: what Eevee reports when the container has no GPU.
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "lavapipe")
PREVIEW_WIDTH = 360
SUPPORTED_VERBS = frozenset(
    {"add", "scale", "lift", "drop", "strike", "explode", "cutaway", "label", "cycle", "camera"}
)


class BlenderRenderError(RuntimeError):
    """A compact, user-actionable Blender render failure."""


@dataclass(frozen=True)
class RenderResult:
    video: Path
    manifest: dict[str, Any]
    plan: Path


def scene_hash(scenes: Iterable[Any]) -> str:
    """Hash only the canonical saved scene contract, for preview staleness."""
    encoded = json.dumps(list(scenes), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _inside(path: Path, root: Path) -> Path:
    """Return a resolved path only when it is contained by ``root``."""
    path, root = path.resolve(), root.resolve()
    try:
        path.relative_to(root)
    except ValueError as err:
        raise BlenderRenderError(f"3D render path must stay inside {root}") from err
    return path


def _canonical_line(line: Any) -> dict[str, Any]:
    """Convert one *already parsed* world3d line to the renderer contract.

    The intentionally tiny adapter is the only coupling to ``world3d.Line``.
    Strings are refused so no model or editor text can bypass
    ``world3d.clean`` and ``world3d.parsed``.
    """
    if isinstance(line, (str, bytes)):
        raise TypeError("Blender rendering requires canonical world3d lines, not raw text")
    word, verb = getattr(line, "word", None), getattr(line, "verb", None)
    if not isinstance(word, int) or isinstance(word, bool) or word < 0:
        raise TypeError("A canonical world3d line must have a non-negative integer word")
    if not isinstance(verb, str) or not verb:
        raise TypeError("A canonical world3d line must have a verb")
    if verb == "add":
        args: list[str | int | float] = [line.asset, line.slot, line.size, line.ident]
    elif verb == "scale":
        args = [line.target, line.size or line.scale]
    elif verb in {"lift", "explode", "cutaway"}:
        args = [line.target]
    elif verb in {"drop", "strike"}:
        args = [line.target, *([line.target2] if line.target2 else [])]
    elif verb == "label":
        args = [line.target, line.text]
    elif verb == "cycle":
        args = [line.target, line.count]
    elif verb == "camera":
        args = [line.mode, *([line.target] if line.target else [])]
    else:
        raise BlenderRenderError(f"The Blender proof renderer does not support '{verb}' yet")
    return {"word": word, "verb": verb, "args": args}


def _scene_lines(scenes: Iterable[Any], word_count: int) -> list[dict[str, Any]]:
    from . import world3d

    canonical, issues = world3d.clean(list(scenes), word_count)
    if issues:
        raise BlenderRenderError("The 3D plan is not renderable: " + issues[0])
    parsed = [line for scene in canonical for line in world3d.parsed(scene)]
    unsupported = sorted({line.verb for line in parsed if line.verb not in SUPPORTED_VERBS})
    if unsupported:
        detail = (
            f"'{unsupported[0]}'"
            if len(unsupported) == 1
            else ", ".join(f"'{verb}'" for verb in unsupported)
        )
        raise BlenderRenderError(
            "The Blender beta does not support "
            + detail
            + (" operations" if len(unsupported) > 1 else "")
            + " yet"
            + ". Edit those lines or use the supported P2 actions."
        )
    operations: list[dict[str, Any]] = []
    operations.extend(_canonical_line(line) for line in parsed)
    return operations


def resolved_plan(
    scenes: Iterable[Any],
    *,
    word_count: int,
    width: int,
    height: int,
    fps: int = DEFAULT_FPS,
    words_per_minute: float = 150.0,
    resolution_percentage: int = 100,
    samples: int = DEFAULT_SAMPLES,
    seed: int = 3,
) -> dict[str, Any]:
    """Build the deterministic JSON value consumed by Blender.

    Geometry, slots, materials and paths are not part of this value: they are
    code-owned by the Blender runner.  ``width``/``height`` may use either of
    Clipper's format contracts; arbitrary dimensions are useful only for the
    deliberately explicit proof benchmark.
    """
    if width < 64 or height < 64 or fps not in range(1, 121):
        raise ValueError("Invalid 3D render dimensions or frame rate")
    if not 10 <= resolution_percentage <= 100 or not 1 <= samples <= 256:
        raise ValueError("Invalid 3D render quality")
    if word_count < 1:
        raise ValueError("A 3D render needs at least one narration word")
    operations = _scene_lines(scenes, word_count)
    body: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "asset_pack_version": ASSET_PACK_VERSION,
        "render": {
            "width": width,
            "height": height,
            "fps": fps,
            "words_per_minute": words_per_minute,
            "resolution_percentage": resolution_percentage,
            "samples": samples,
            "seed": seed,
        },
        "operations": operations,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {**body, "plan_hash": hashlib.sha256(encoded).hexdigest()}


def write_plan(scenes: Iterable[Any], render_dir: Path, **options: Any) -> Path:
    """Atomically write a resolved plan beneath its storyboard render dir."""
    root = render_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = _inside(root / "blender-plan.json", root)
    part = _inside(path.with_suffix(".part.json"), root)
    part.write_text(json.dumps(resolved_plan(scenes, **options), indent=2) + "\n")
    part.replace(path)
    return path


def command(blender: str, plan: Path, render_dir: Path) -> list[str]:
    """Construct the fixed, shell-free Blender invocation."""
    root = render_dir.resolve()
    plan = _inside(plan, root)
    runner = RUNNER.resolve()
    if not runner.is_file():
        raise BlenderRenderError("The packaged Blender renderer is missing")
    return [
        blender,
        "--background",
        "--factory-startup",
        # Without this Blender exits 0 when the script raises, which hides the
        # real error behind "finished without producing its video".
        "--python-exit-code",
        "1",
        "--python",
        str(runner),
        "--",
        "--plan",
        str(plan),
        "--output",
        str(root),
    ]


def _stop_group(proc: subprocess.Popen[str], grace: float = 4.0) -> None:
    """Terminate Blender and descendants, escalating after a short grace."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - production Blender worker is Linux
            proc.terminate()
        proc.wait(timeout=grace)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if proc.poll() is None:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:  # pragma: no cover
                proc.kill()
            proc.wait()


def _cleanup_partials(root: Path) -> None:
    """Remove only renderer-owned partials, never a previous good render."""
    for name in ("scene.part.mp4", "scene.part.mkv", "manifest.part.json", "blender-scene.part.blend"):
        path = _inside(root / name, root)
        if path.is_file():
            path.unlink()
    frames = _inside(root / "frames.part", root)
    if frames.is_dir():
        shutil.rmtree(frames)


def _duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"{minutes} min" if minutes <= 60 else f"{minutes // 60} h {minutes % 60:02d} min"


class Pace:
    """Measure Blender's real speed and stop a render that cannot finish in time.

    The first frame includes start-up and shader compilation, so the rate is
    taken from the first written frame onwards and only trusted after
    ``SETTLE`` more frames. It is cumulative, so one slow frame does not stop
    a render; a scene that grows heavier is caught as soon as the average says so.
    """

    SETTLE = 5

    def __init__(self, limit: float, rep: Reporter):
        self.limit = limit
        self.rep = rep
        self.first: tuple[float, int] | None = None
        self.announced = False

    def frame(self, done: int, total: int, elapsed: float) -> str:
        message = f"Rendering 3D frame {done} of {total}"
        if self.first is None:
            self.first = (elapsed, done)
            return message
        frames = done - self.first[1]
        if frames < self.SETTLE:
            return message
        rate = (elapsed - self.first[0]) / frames
        left = rate * max(0, total - done)
        if elapsed + left > self.limit:
            raise BlenderRenderError(
                f"Stopped early: at {rate:.1f} s a frame, the {total} frames would take about "
                f"{_duration(elapsed + left)}, past the {_duration(self.limit)} limit. Lower the 3D render size "
                "or samples in Settings, raise the time limit, or give the worker a GPU."
            )
        if not self.announced:
            self.announced = True
            self.rep.info(f"Blender renders {rate:.1f} s a frame: about {_duration(elapsed + left)} for {total} frames")
        return f"{message}, about {_duration(left)} left"


def _failure_detail(tail: Iterable[str]) -> str:
    """The renderer's own error line when there is one, else Blender's last lines."""
    lines = list(tail)
    ours = [line.split("CLIPPER_ERROR ", 1)[1] for line in lines if "CLIPPER_ERROR " in line]
    if ours:
        return ours[-1].strip()[:400]
    # A Python traceback ends with "SomethingError: message"; Blender then adds
    # a generic "Error: script failed, file: ..." that says nothing useful.
    raised = [line for line in lines
              if re.match(r"^[A-Za-z_.]*(Error|Exception)\b.*:", line) and not line.startswith("Error: ")]
    if raised:
        return raised[-1][:400]
    return " | ".join(lines)[-800:]


def run_plan(
    plan: Path,
    render_dir: Path,
    rep: Reporter,
    *,
    blender_binary: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> RenderResult:
    """Run one resolved plan and return its immutable measured facts.

    ``rep.progress`` is called even while Blender is quiet.  A ``JobReporter``
    therefore notices a cancellation, whose exception unwinds through the
    ``finally`` block and kills the complete child process group.
    """
    root = render_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan = _inside(plan, root)
    blender = blender_binary or shutil.which("blender")
    if not blender:
        raise BlenderRenderError(
            "3D rendering needs Blender in the worker. Use the Blender worker image; "
            "native planning and editing still work without it."
        )
    cmd = command(blender, plan, root)
    _cleanup_partials(root)
    tail: deque[str] = deque(maxlen=16)
    pace = Pace(timeout, rep)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            bufsize=1,
        )
    except OSError as err:
        raise BlenderRenderError(
            f"Could not start Blender at '{blender}': {str(err)[:240]}"
        ) from err
    selector = selectors.DefaultSelector()
    assert proc.stdout is not None
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        try:
            while proc.poll() is None:
                if time.monotonic() - started > timeout:
                    raise BlenderRenderError(f"Blender exceeded the {timeout:g} second render limit")
                ready = selector.select(timeout=0.5)
                if ready:
                    line = proc.stdout.readline().strip()
                    if line:
                        tail.append(line)
                        if line.startswith("CLIPPER_PROGRESS "):
                            _, done, total = line.split(maxsplit=2)
                            message = pace.frame(int(done), int(total), time.monotonic() - started)
                            rep.progress(int(done) / max(1, int(total)), message)
                else:
                    rep.progress(None, "Rendering the 3D scene")
            # Drain the compact final diagnostics without ever blocking a live process.
            for line in proc.stdout:
                if line.strip():
                    tail.append(line.strip())
        finally:
            selector.close()
            _stop_group(proc)
    except BaseException:
        _cleanup_partials(root)
        raise
    if proc.returncode:
        _cleanup_partials(root)
        detail = _failure_detail(tail) or f"exit code {proc.returncode}"
        raise BlenderRenderError(f"Blender could not render the 3D scene: {detail}")
    manifest_part = _inside(root / "manifest.part.json", root)
    video_part = _inside(root / "scene.part.mp4", root)
    if not manifest_part.is_file() or not video_part.is_file():
        _cleanup_partials(root)
        detail = _failure_detail(tail)
        raise BlenderRenderError(
            "Blender finished without producing its video and manifest" + (f": {detail}" if detail else "")
        )
    try:
        manifest = json.loads(manifest_part.read_text())
    except (OSError, json.JSONDecodeError) as err:
        _cleanup_partials(root)
        raise BlenderRenderError("Blender produced an unreadable render manifest") from err
    expected_hash = json.loads(plan.read_text()).get("plan_hash")
    if manifest.get("plan_hash") != expected_hash:
        _cleanup_partials(root)
        raise BlenderRenderError("Blender manifest does not match the validated 3D plan")
    video = _inside(root / "scene.mp4", root)
    manifest_path = _inside(root / "manifest.json", root)
    video_part.replace(video)
    manifest_part.replace(manifest_path)
    gpu = str(manifest.get("gpu_renderer", "unknown"))
    rep.info(
        f"Blender {manifest.get('blender_version', 'unknown')}, {manifest.get('engine', 'Eevee')} on {gpu}: "
        f"{manifest.get('frame_end', '?')} frames in {manifest.get('render_seconds', 0):.1f}s"
    )
    if any(name in gpu.lower() for name in SOFTWARE_RENDERERS):
        rep.warn(
            f"Blender drew on the CPU ({gpu}): no GPU reached the worker, so 3D renders are slow. "
            "Pass /dev/dri to the worker container to use the machine's graphics."
        )
    return RenderResult(video=video, manifest=manifest, plan=plan)


def render(
    scenes: Iterable[Any],
    render_dir: Path,
    rep: Reporter,
    *,
    word_count: int,
    width: int,
    height: int,
    blender_binary: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    **quality: Any,
) -> RenderResult:
    """Materialize canonical scenes then supervise their Blender render."""
    plan = write_plan(
        scenes, render_dir, word_count=word_count, width=width, height=height, **quality
    )
    return run_plan(plan, render_dir, rep, blender_binary=blender_binary, timeout=timeout)


def render_dir(board: Storyboard | dict[str, Any]) -> Path:
    """The existing storyboard media folder; final output remains `short.mp4`/`long.mp4`."""
    script_id = board.script_id if isinstance(board, Storyboard) else board["script_id"]
    board_id = board.id if isinstance(board, Storyboard) else board["id"]
    return voice_mod.script_media_dir(script_id) / f"storyboard-{board_id}"


def _plan_options(board: dict[str, Any], settings: Any) -> dict[str, Any]:
    width, height = DIMENSIONS[board["format"]]
    duration = max(0.1, float(board["end"]) - float(board["start"]))
    return {
        "word_count": len(board["words"]),
        "width": width,
        "height": height,
        # The proof renderer still has a words-per-minute clock. Matching it
        # to the chosen take keeps its last operation aligned with the voice;
        # final captions and sound use the exact per-word timeline below.
        "words_per_minute": max(1.0, len(board["words"]) * 60.0 / duration),
        # The composer scales the 3D layer to the final size and draws captions
        # afterwards at full size, so a smaller render softens only the 3D shapes.
        "resolution_percentage": settings.studio_3d_render_scale,
        "samples": settings.studio_3d_samples,
    }


def _board_data(storyboard_id: int) -> dict[str, Any]:
    with session_scope() as db:
        board = db.get(Storyboard, storyboard_id)
        if board is None:
            raise BlenderRenderError(f"Storyboard {storyboard_id} no longer exists")
        if board.style != "3d":
            raise BlenderRenderError("The Blender build stage accepts 3D storyboards only")
        return {
            "id": board.id,
            "script_id": board.script_id,
            "format": board.format,
            "start": board.start,
            "end": board.end,
            "words": list(board.words or []),
            "scenes": list(board.scenes or []),
        }


def _cached_result(plan: Path, root: Path) -> RenderResult | None:
    video, manifest_path = root / "scene.mp4", root / "manifest.json"
    if not video.is_file() or not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
        expected = json.loads(plan.read_text())["plan_hash"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    if manifest.get("plan_hash") != expected:
        return None
    return RenderResult(video=_inside(video, root), manifest=manifest, plan=plan)


def _preview_times(scenes: list[dict], words: list[dict]) -> list[float]:
    times = []
    for shot in scenes:
        word = max(0, min(int(shot.get("start_word", 0)), len(words) - 1))
        times.append(float(words[word].get("s", 0)))
    return times


def _write_previews(
    result: RenderResult, scenes: list[dict], words: list[dict], root: Path, rep: Reporter | None = None
) -> None:
    """Cache one low-resolution still per shot without starting Blender again."""
    import cv2

    digest = scene_hash(scenes)
    folder = _inside(root / "previews" / digest, root)
    index = _inside(root / "previews.json", root)
    times = _preview_times(scenes, words)
    if index.is_file():
        try:
            current = json.loads(index.read_text())
        except (OSError, json.JSONDecodeError):
            current = {}
        cached = current.get("shots", [])
        try:
            complete = len(cached) == len(scenes) and all(
                _inside(root / item["file"], root).is_file() for item in cached
            )
        except (KeyError, TypeError, BlenderRenderError):
            complete = False
        if current.get("scene_hash") == digest and complete:
            return
    folder.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(result.video))
    if not capture.isOpened():
        raise BlenderRenderError("The 3D scene rendered, but its cached previews could not be decoded")
    shots = []
    try:
        for number, at in enumerate(times, 1):
            capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at) * 1000)
            ok, frame = capture.read()
            if not ok:
                # A late shot may be beyond the proof renderer's last motion;
                # the compositor freezes the final frame, so preview it too.
                capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) - 1))
                ok, frame = capture.read()
            if not ok:
                raise BlenderRenderError("The 3D scene rendered, but a cached shot preview could not be decoded")
            height, width = frame.shape[:2]
            target_h = max(1, round(height * PREVIEW_WIDTH / width))
            small = cv2.resize(frame, (PREVIEW_WIDTH, target_h), interpolation=cv2.INTER_AREA)
            path = _inside(folder / f"shot-{number:03d}.png", root)
            part = _inside(path.with_suffix(".part.png"), root)
            if not cv2.imwrite(str(part), small):
                raise BlenderRenderError("Could not save the cached 3D previews")
            part.replace(path)
            shots.append({"shot": number, "at": round(at, 3), "file": str(path.relative_to(root))})
            if rep is not None:
                rep.progress(number / len(times), f"Cached 3D preview {number} of {len(times)}")
    finally:
        capture.release()
        for partial in folder.glob("*.part.png"):
            partial.unlink(missing_ok=True)
    value = {
        "scene_hash": digest,
        "plan_hash": result.manifest.get("plan_hash"),
        "built_at": utcnow().isoformat(),
        "shots": shots,
    }
    part = _inside(index.with_suffix(".part.json"), root)
    part.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    part.replace(index)


def build(storyboard_id: int, rep: Reporter) -> RenderResult:
    """Materialize or reuse the trusted 3D intermediate and cached shot stills."""
    board = _board_data(storyboard_id)
    if not board["words"]:
        raise BlenderRenderError("The 3D storyboard has no timed narration words")
    if not board["scenes"]:
        raise BlenderRenderError("The 3D storyboard has no scenes")
    from .. import settings as settings_mod

    settings = settings_mod.load()
    root = render_dir(board)
    options = _plan_options(board, settings)
    plan = write_plan(board["scenes"], root, **options)
    from . import world3d

    report = world3d.complexity(board["scenes"])
    assets = {
        line.asset
        for shot in board["scenes"]
        for line in world3d.parsed(shot)
        if line.verb == "add"
    }
    rep.info(
        f"3D plan: {report['units']} complexity units ({report['band']}), "
        f"{report['objects']} objects and {len(assets)} code-owned asset types"
    )
    result = _cached_result(plan, root)
    if result is None:
        width, height = options["width"], options["height"]
        scale = options["resolution_percentage"]
        rep.info(
            f"Rendering the 3D layer at {width * scale // 100}x{height * scale // 100} ({scale}% of the video), "
            f"{options['samples']} samples, limit {_duration(settings.studio_3d_max_minutes * 60)}"
        )
        result = run_plan(plan, root, rep, timeout=settings.studio_3d_max_minutes * 60)
    else:
        rep.info(
            f"Reusing Blender {result.manifest.get('blender_version', 'unknown')} scene "
            f"for plan {str(result.manifest.get('plan_hash', ''))[:12]}"
        )
    _write_previews(result, board["scenes"], board["words"], root, rep)
    rep.info(f"Cached {len(board['scenes'])} representative 3D shot previews")
    return result


def preview(storyboard_id: int, at: float, width: int) -> tuple[bytes, bool]:
    """Return the nearest cached shot still and whether saved scenes have changed."""
    board = _board_data(storyboard_id)
    root = render_dir(board).resolve()
    index_path = _inside(root / "previews.json", root)
    if not index_path.is_file():
        raise FileNotFoundError(index_path)
    try:
        index = json.loads(index_path.read_text())
        shots = index["shots"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as err:
        raise FileNotFoundError(index_path) from err
    if not shots:
        raise FileNotFoundError(index_path)
    selected = max((shot for shot in shots if float(shot["at"]) <= at), key=lambda shot: shot["at"], default=shots[0])
    path = _inside(root / selected["file"], root)
    if not path.is_file():
        raise FileNotFoundError(path)
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((width, max(1, round(width * 16 / 9))))
        out = io.BytesIO()
        image.save(out, "PNG", optimize=True)
    return out.getvalue(), index.get("scene_hash") != scene_hash(board["scenes"])


def preview_status(board: Storyboard | dict[str, Any]) -> dict[str, Any]:
    """Cheap editor facts for the cached-preview badge and rebuild action."""
    scenes = list(board.scenes or []) if isinstance(board, Storyboard) else list(board.get("scenes") or [])
    root = render_dir(board).resolve()
    index_path = _inside(root / "previews.json", root)
    try:
        value = json.loads(index_path.read_text())
        shots = value.get("shots") or []
        available = bool(shots) and all(_inside(root / item["file"], root).is_file() for item in shots)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, BlenderRenderError):
        value, available = {}, False
    return {
        "available": available,
        "built_at": value.get("built_at") if available else None,
        "stale": available and value.get("scene_hash") != scene_hash(scenes),
    }
