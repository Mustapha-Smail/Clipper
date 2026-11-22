import json
import os
import textwrap
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from clipper import jobs
from clipper.db import session_scope
from clipper.db.models import Script, Storyboard
from clipper.jobs import Reporter
from clipper.settings import Settings
from clipper.studio import animate, blender_render

FIXTURE = Path(__file__).with_name("fixtures") / "world3d_pile_hammer.json"


def proof():
    value = json.loads(FIXTURE.read_text())
    return value["scenes"], value["word_count"]


def fake_blender(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "fake-blender"
    executable.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    executable.chmod(0o755)
    return executable


def test_proof_plan_resolves_deterministically_for_vertical_and_horizontal():
    scenes, word_count = proof()
    vertical = blender_render.resolved_plan(
        scenes, word_count=word_count, width=1080, height=1920
    )
    again = blender_render.resolved_plan(
        scenes, word_count=word_count, width=1080, height=1920
    )
    horizontal = blender_render.resolved_plan(
        scenes, word_count=word_count, width=1920, height=1080
    )

    assert vertical == again
    assert vertical["plan_hash"] != horizontal["plan_hash"]
    assert vertical["operations"][0] == {
        "word": 0,
        "verb": "add",
        "args": ["plank", "center", "medium", "board"],
    }
    assert {operation["verb"] for operation in vertical["operations"]} == {
        "add", "scale", "strike", "camera", "explode", "label", "cutaway", "cycle"
    }
    # At 150 wpm and 24 fps, the last beat starts at 20 seconds.
    assert vertical["operations"][-1]["word"] == 50


def test_bridge_runs_world3d_clean_and_refuses_any_issue():
    scenes = [{"start_word": 0, "note": "bad", "lines": ["0 add mystery center as thing"]}]
    with pytest.raises(blender_render.BlenderRenderError, match="unknown asset 'mystery'"):
        blender_render.resolved_plan(scenes, word_count=3, width=1080, height=1920)


def test_bridge_refuses_a_clean_operation_not_in_the_proof_renderer():
    scenes = [{
        "start_word": 0,
        "note": "moving",
        "lines": ["0 add nail center as pin", "1 move pin right"],
    }]
    with pytest.raises(blender_render.BlenderRenderError, match="does not support 'move'"):
        blender_render.resolved_plan(scenes, word_count=3, width=1080, height=1920)


def test_bridge_lists_all_unsupported_valid_operations_before_blender_starts():
    scenes = [{
        "start_word": 0,
        "note": "future actions",
        "lines": [
            "0 add nail center as pin",
            "1 move pin right",
            "2 rotate pin half",
        ],
    }]
    with pytest.raises(blender_render.BlenderRenderError, match="'move'.*'rotate'"):
        blender_render.resolved_plan(scenes, word_count=3, width=1080, height=1920)


def test_adapter_supports_exact_p2_action_vocabulary():
    scenes = [{
        "start_word": 0,
        "note": "all proof actions",
        "lines": [
            "0 add nail center small as pin",
            "1 add hammer right medium as tool",
            "2 lift tool",
            "3 drop tool pin",
            "4 strike tool pin",
            "5 scale pin large",
            "6 add pile_hammer center large as driver",
            "7 explode driver",
            "8 cutaway driver",
            "9 label driver \"DRIVER\"",
            "10 cycle driver 2",
            "11 camera orbit driver",
        ],
    }]
    plan = blender_render.resolved_plan(scenes, word_count=12, width=1080, height=1920)

    assert {operation["verb"] for operation in plan["operations"]} == {
        "add", "scale", "lift", "drop", "strike", "explode", "cutaway", "label", "cycle", "camera"
    }


def test_plan_and_command_are_contained_by_render_directory(tmp_path):
    scenes, word_count = proof()
    root = tmp_path / "render"
    plan = blender_render.write_plan(
        scenes, root, word_count=word_count, width=540, height=960, resolution_percentage=50
    )
    cmd = blender_render.command("/opt/blender", plan, root)
    assert cmd[:3] == ["/opt/blender", "--background", "--factory-startup"]
    # A Python error in the renderer must fail the process instead of exiting 0.
    assert cmd[cmd.index("--python-exit-code") + 1] == "1"
    assert cmd[-4:] == ["--plan", str(plan), "--output", str(root.resolve())]
    with pytest.raises(blender_render.BlenderRenderError, match="must stay inside"):
        blender_render.command("/opt/blender", tmp_path / "outside.json", root)


def test_missing_blender_has_an_actionable_native_error(tmp_path, monkeypatch):
    scenes, word_count = proof()
    plan = blender_render.write_plan(scenes, tmp_path, word_count=word_count, width=540, height=960)
    monkeypatch.setattr(blender_render.shutil, "which", lambda _name: None)
    with pytest.raises(blender_render.BlenderRenderError, match="Blender worker image"):
        blender_render.run_plan(plan, tmp_path, Reporter())


def test_success_promotes_partials_and_reads_measured_manifest(tmp_path):
    scenes, word_count = proof()
    plan = blender_render.write_plan(scenes, tmp_path, word_count=word_count, width=540, height=960)
    binary = fake_blender(tmp_path, """
        import json
        import pathlib
        import sys
        args = sys.argv
        out = pathlib.Path(args[args.index("--output") + 1])
        plan = json.loads(pathlib.Path(args[args.index("--plan") + 1]).read_text())
        (out / "scene.part.mp4").write_bytes(b"video")
        manifest = {
            "blender_version": "4.5.16", "engine": "BLENDER_EEVEE_NEXT",
            "frame_end": 494, "render_seconds": 1.25, "plan_hash": plan["plan_hash"]
        }
        (out / "manifest.part.json").write_text(json.dumps(manifest))
        print("CLIPPER_PROGRESS 494 494", flush=True)
    """)

    result = blender_render.run_plan(plan, tmp_path, Reporter(), blender_binary=str(binary), timeout=3)

    assert result.video.read_bytes() == b"video"
    assert result.manifest["blender_version"] == "4.5.16"
    assert (tmp_path / "manifest.json").is_file()
    assert not (tmp_path / "scene.part.mp4").exists()


def test_failure_removes_partials_but_preserves_previous_good_render(tmp_path):
    scenes, word_count = proof()
    plan = blender_render.write_plan(scenes, tmp_path, word_count=word_count, width=540, height=960)
    (tmp_path / "scene.mp4").write_bytes(b"previous")
    (tmp_path / "manifest.json").write_text('{"previous": true}')
    binary = fake_blender(tmp_path, """
        import pathlib
        import sys
        out = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
        (out / "scene.part.mp4").write_bytes(b"partial")
        print("compact failure", flush=True)
        raise SystemExit(4)
    """)

    with pytest.raises(blender_render.BlenderRenderError, match="compact failure"):
        blender_render.run_plan(plan, tmp_path, Reporter(), blender_binary=str(binary), timeout=3)

    assert (tmp_path / "scene.mp4").read_bytes() == b"previous"
    assert json.loads((tmp_path / "manifest.json").read_text()) == {"previous": True}
    assert not (tmp_path / "scene.part.mp4").exists()


def test_missing_output_reports_the_renderers_own_error(tmp_path):
    scenes, word_count = proof()
    plan = blender_render.write_plan(scenes, tmp_path, word_count=word_count, width=540, height=960)
    binary = fake_blender(tmp_path, """
        import sys
        print("Blender 4.5.16", flush=True)
        print("AttributeError: 'NoneType' object has no attribute 'color'", flush=True)
        print("Blender quit", flush=True)
    """)

    with pytest.raises(blender_render.BlenderRenderError, match="no attribute 'color'"):
        blender_render.run_plan(plan, tmp_path, Reporter(), blender_binary=str(binary), timeout=3)


def test_failure_detail_prefers_the_python_exception_over_blenders_generic_line():
    tail = [
        "    scene.world.color = (0.025, 0.035, 0.05)",
        "AttributeError: 'NoneType' object has no attribute 'color'",
        "Error: script failed, file: '/app/render_scene.py', exiting.",
        "Blender quit",
    ]
    assert blender_render._failure_detail(tail) == "AttributeError: 'NoneType' object has no attribute 'color'"
    assert blender_render._failure_detail(["CLIPPER_ERROR unknown camera move", *tail]) == "unknown camera move"


def test_renderer_configures_a_world_after_the_empty_factory_reset(tmp_path):
    from unittest import mock

    from clipper.blender import render_scene

    bpy = mock.MagicMock()
    bpy.context.scene.world = None
    world = mock.MagicMock()
    bpy.data.worlds.new.return_value = world
    render = {"fps": 24, "words_per_minute": 150, "width": 540, "height": 960,
              "resolution_percentage": 50, "samples": 8}
    scene = render_scene.Scene(bpy, {"render": render, "operations": []}, tmp_path)
    scene.add_camera = lambda: None  # a stand-in cannot turn tuples into Blender vectors

    scene.configure()

    bpy.ops.wm.read_factory_settings.assert_called_once_with(use_empty=True)
    assert bpy.context.scene.world is world
    assert world.color == (0.025, 0.035, 0.05)


def test_camera_above_its_target_looks_down_at_it(tmp_path):
    """Blender's camera looks along -Z: tilt 0 is straight down, 90 degrees is level."""
    import math
    from types import SimpleNamespace

    from clipper.blender import render_scene

    render = {"fps": 24, "words_per_minute": 150, "width": 540, "height": 960,
              "resolution_percentage": 50, "samples": 8}
    scene = render_scene.Scene(None, {"render": render, "operations": []}, tmp_path)
    camera = SimpleNamespace(location=SimpleNamespace(x=0.0, y=-10.0, z=10.0), rotation_euler=None)
    scene.track(camera, (0.0, 0.0, 0.0))
    tilt, _, turn = camera.rotation_euler
    assert math.isclose(tilt, math.radians(45))  # down, not up at the sky
    assert math.isclose(turn, 0.0, abs_tol=1e-9)  # facing +Y, toward the target
    label = SimpleNamespace(location=SimpleNamespace(x=0.0, y=0.0, z=0.0), rotation_euler=None)
    scene.face(label, (0.0, -10.0, 10.0))
    tilt, _, turn = label.rotation_euler
    # +Z rotated by tilt about X, then by turn about Z, must point at the camera.
    front = (math.sin(turn) * math.sin(tilt), -math.cos(turn) * math.sin(tilt), math.cos(tilt))
    toward_camera = (0.0, -math.sqrt(0.5), math.sqrt(0.5))
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(front, toward_camera, strict=True))


class CancellingReporter(Reporter):
    def progress(self, fraction, message=None):
        raise jobs.JobCancelled


def test_cancellation_kills_process_group_and_cleans_partial(tmp_path):
    scenes, word_count = proof()
    plan = blender_render.write_plan(scenes, tmp_path, word_count=word_count, width=540, height=960)
    binary = fake_blender(tmp_path, """
        import pathlib
        import subprocess
        import sys
        import time
        out = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
        (out / "scene.part.mp4").write_bytes(b"partial")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        (out / "child.pid").write_text(str(child.pid))
        print("CLIPPER_PROGRESS 1 100", flush=True)
        time.sleep(30)
    """)
    started = time.monotonic()

    with pytest.raises(jobs.JobCancelled):
        blender_render.run_plan(plan, tmp_path, CancellingReporter(), blender_binary=str(binary), timeout=3)

    assert time.monotonic() - started < 6
    assert not (tmp_path / "scene.part.mp4").exists()
    child_pid = int((tmp_path / "child.pid").read_text())
    # killpg targets both processes. A just-reaped process may briefly remain as
    # a zombie, so inspect its process group instead of relying on kill(pid, 0).
    with pytest.raises(ProcessLookupError):
        os.getpgid(child_pid)


def test_timeout_kills_blender_and_cleans_partial(tmp_path):
    scenes, word_count = proof()
    plan = blender_render.write_plan(scenes, tmp_path, word_count=word_count, width=540, height=960)
    binary = fake_blender(tmp_path, """
        import pathlib
        import sys
        import time
        out = pathlib.Path(sys.argv[sys.argv.index("--output") + 1])
        (out / "scene.part.mp4").write_bytes(b"partial")
        time.sleep(30)
    """)

    with pytest.raises(blender_render.BlenderRenderError, match="render limit"):
        blender_render.run_plan(plan, tmp_path, Reporter(), blender_binary=str(binary), timeout=0.1)

    assert not (tmp_path / "scene.part.mp4").exists()


def test_success_snapshot_records_exact_blender_facts_only_after_composition(app_env):
    facts = {
        "blender_version": "4.5.16",
        "engine": "BLENDER_EEVEE_NEXT",
        "asset_pack_version": 1,
        "plan_hash": "abc123",
        "render_width": 1080,
        "render_height": 1920,
        "resolution_percentage": 100,
        "samples": 32,
        "render_seconds": 123.4,
        "peak_intermediate_bytes": 123456,
    }
    with session_scope() as db:
        script = Script(title="Mechanism", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(script_id=script.id, title="Mechanism", style="3d", look="3d", words=[], scenes=[])
        db.add(board)
        db.flush()
        snapshot = animate.render_snapshot(board, Settings(), renderer={**facts, "mutable_extra": "ignored"})

    assert {key: snapshot[key] for key in facts} == facts
    assert "mutable_extra" not in snapshot


def test_failed_3d_composition_preserves_previous_video_and_snapshot(app_env):
    previous = app_env / "previous.mp4"
    previous.write_bytes(b"good")
    with session_scope() as db:
        script = Script(title="Mechanism", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(
            script_id=script.id,
            title="Mechanism",
            style="3d",
            look="3d",
            words=[{"w": "Look", "s": 0.0, "e": 0.5, "section": "a"}],
            scenes=[{"start_word": 0, "note": "nail", "lines": ["0 add nail center as nail"]}],
            video_path=str(previous),
            render_meta={"version": 1, "plan_hash": "previous"},
        )
        db.add(board)
        db.flush()
        board_id = board.id

    with pytest.raises(RuntimeError, match="voice take"):
        animate.render(board_id, Settings(), Reporter())

    with session_scope() as db:
        board = db.get(Storyboard, board_id)
        assert board.video_path == str(previous)
        assert board.render_meta == {"version": 1, "plan_hash": "previous"}
    assert previous.read_bytes() == b"good"


def test_3d_composer_seeks_exact_source_frame_when_output_skips(monkeypatch, tmp_path):
    import cv2

    source = tmp_path / "scene.mp4"
    source.write_bytes(b"placeholder")

    class Capture:
        def __init__(self, _path):
            self.position = 0

        def isOpened(self):
            return True

        def get(self, prop):
            return 10 if prop == cv2.CAP_PROP_FRAME_COUNT else 24 if prop == cv2.CAP_PROP_FPS else self.position

        def set(self, prop, value):
            assert prop == cv2.CAP_PROP_POS_FRAMES
            self.position = int(value)

        def read(self):
            frame = np.full((2, 2, 3), self.position, np.uint8)
            self.position += 1
            return True, frame

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", Capture)
    board = {
        "format": "short",
        "start": 0.0,
        "end": 1.0,
        "words": [{"w": "Look", "s": 0.0, "e": 0.5}],
        "scenes": [],
        "scene_3d": str(source),
    }
    composer = animate.ThreeDComposer(board, Settings(captions_enabled=False))
    composer.layout = replace(composer.layout, width=2, height=2)
    assert int(composer._read(0.0)[0, 0, 0]) == 0
    assert int(composer._read(2 / 24)[0, 0, 0]) == 2
    composer.close()


def test_cached_preview_is_served_and_marked_stale_after_scene_edit(app_env):
    scenes = [{"start_word": 0, "note": "nail", "lines": ["0 add nail center as nail"]}]
    with session_scope() as db:
        script = Script(title="Mechanism", sections=[])
        db.add(script)
        db.flush()
        board = Storyboard(
            script_id=script.id,
            title="Mechanism",
            format="short",
            style="3d",
            look="3d",
            start=0,
            end=1,
            words=[{"w": "Look", "s": 0.0, "e": 0.5, "section": "a"}],
            scenes=scenes,
        )
        db.add(board)
        db.flush()
        board_id = board.id
        root = blender_render.render_dir(board)

    digest = blender_render.scene_hash(scenes)
    preview_path = root / "previews" / digest / "shot-001.png"
    preview_path.parent.mkdir(parents=True)
    Image.new("RGB", (360, 640), "navy").save(preview_path)
    (root / "previews.json").write_text(json.dumps({
        "scene_hash": digest,
        "shots": [{"shot": 1, "at": 0.0, "file": str(preview_path.relative_to(root))}],
    }))

    png, stale = blender_render.preview(board_id, 0.0, 180)
    assert png[:4] == b"\x89PNG" and stale is False
    with session_scope() as db:
        assert blender_render.preview_status(db.get(Storyboard, board_id)) == {
            "available": True,
            "built_at": None,
            "stale": False,
        }
    with session_scope() as db:
        db.get(Storyboard, board_id).scenes = [{**scenes[0], "note": "edited nail"}]
    _, stale = blender_render.preview(board_id, 0.0, 180)
    assert stale is True
    with session_scope() as db:
        assert blender_render.preview_status(db.get(Storyboard, board_id))["stale"] is True
