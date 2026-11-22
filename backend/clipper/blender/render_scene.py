"""Render Clipper's resolved 3D contract in Blender 4.5.

Run only through ``blender --background --factory-startup --python ... --``.
This file is standalone because Blender uses its own Python.  It reads no
application settings and accepts no asset, script, URL, material or output path
from a storyboard operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

CONTRACT_VERSION = 1
ASSET_PACK_VERSION = 1
ENGINE = "BLENDER_EEVEE_NEXT"
SLOTS = {
    "center": (0.0, 0.0, 0.8),
    "left": (-2.8, 0.1, 0.8),
    "right": (2.8, 0.1, 0.8),
    "top": (0.0, 0.0, 4.2),
    # Even "bottom" stays above the bottom-third caption reserve in the
    # vertical rig.  These are composition names, not literal frame edges.
    "bottom": (0.0, 0.0, 1.0),
    "top_left": (-2.5, 0.0, 3.2),
    "top_right": (2.5, 0.0, 3.2),
    "bottom_left": (-2.5, 0.0, 1.0),
    "bottom_right": (2.5, 0.0, 1.0),
}
SIZES = {"small": 0.65, "medium": 1.0, "large": 1.55}
# The part a cutaway opens, for every asset world3d.CAPABILITIES lets a plan
# cut away (a test keeps the two lists equal). Parts are named ROOT_PART.
CUTAWAY_PARTS = {
    "cylinder": "shell",
    "pile_hammer": "shell",
    "pump": "body",
    "house": "body",
    "wall": "wall",
    "pipe": "pipe",
}
PALETTE = {
    "iron": (0.15, 0.21, 0.25, 1.0),
    "steel": (0.34, 0.43, 0.48, 1.0),
    "wood": (0.48, 0.26, 0.11, 1.0),
    "accent": (0.93, 0.47, 0.12, 1.0),
    "ground": (0.56, 0.61, 0.55, 1.0),
    "water": (0.08, 0.42, 0.62, 0.72),
    "light": (0.88, 0.91, 0.90, 1.0),
    "dark": (0.055, 0.075, 0.09, 1.0),
    "label": (1.0, 0.84, 0.28, 1.0),
    "ignition": (1.0, 0.24, 0.025, 1.0),
}
ALLOWED_VERBS = {
    "add", "scale", "lift", "drop", "strike", "explode", "cutaway", "label", "cycle", "camera"
}


def fail(message: str) -> None:
    print(f"CLIPPER_ERROR {message}", file=sys.stderr, flush=True)
    raise SystemExit(2)


def contained(path: Path, root: Path) -> Path:
    path, root = path.resolve(), root.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        fail(f"path escapes render directory: {path}")
    return path


def read_contract(plan_path: Path, root: Path) -> dict:
    plan_path = contained(plan_path, root)
    if not plan_path.is_file():
        fail("validated plan is missing")
    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, json.JSONDecodeError):
        fail("validated plan is unreadable")
    if plan.get("contract_version") != CONTRACT_VERSION:
        fail("unsupported render contract version")
    if plan.get("asset_pack_version") != ASSET_PACK_VERSION:
        fail("unsupported 3D asset pack version")
    expected = plan.get("plan_hash")
    unsigned = {key: value for key, value in plan.items() if key != "plan_hash"}
    actual = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected != actual:
        fail("validated plan hash does not match its contents")
    operations = plan.get("operations")
    if not isinstance(operations, list):
        fail("validated plan has no operations")
    previous = -1
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != {"word", "verb", "args"}:
            fail("malformed resolved operation")
        word, verb, args = operation["word"], operation["verb"], operation["args"]
        if not isinstance(word, int) or isinstance(word, bool) or word < previous:
            fail("operations must use ordered non-negative word numbers")
        if verb not in ALLOWED_VERBS or not isinstance(args, list):
            fail("operation is outside the renderer allow-list")
        if any(not isinstance(value, (str, int, float, bool)) and value is not None for value in args):
            fail("operation contains a non-scalar value")
        previous = word
    return plan


class Scene:
    def __init__(self, bpy, plan: dict, output: Path):
        self.bpy = bpy
        self.plan = plan
        self.output = output
        self.objects = {}
        self.kinds = {}
        self.materials = {}
        self.labels = 0
        self.schedule = []
        self.render = plan["render"]
        self.fps = int(self.render["fps"])
        self.word_frames = self.fps * 60.0 / float(self.render["words_per_minute"])
        self.duration = max(6, round(self.fps * 0.55))
        random.seed(int(self.render.get("seed", 3)))

    def material(self, name: str, *, metallic: float = 0.0, roughness: float = 0.58, emission: float = 0.0):
        if name in self.materials:
            return self.materials[name]
        material = self.bpy.data.materials.new(name=f"mat_{name}")
        material.diffuse_color = PALETTE[name]
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = PALETTE[name]
        shader.inputs["Roughness"].default_value = roughness
        shader.inputs["Metallic"].default_value = metallic
        if emission:
            emission_input = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
            strength_input = shader.inputs.get("Emission Strength")
            if emission_input:
                emission_input.default_value = PALETTE[name]
            if strength_input:
                strength_input.default_value = emission
        if PALETTE[name][3] < 1:
            shader.inputs["Alpha"].default_value = PALETTE[name][3]
            material.surface_render_method = "DITHERED"
        self.materials[name] = material
        return material

    def root(self, object_id: str, location) -> object:
        root = self.bpy.data.objects.new(object_id, None)
        root.empty_display_type = "PLAIN_AXES"
        root.location = location
        self.bpy.context.collection.objects.link(root)
        return root

    def cube(self, root, name: str, location, scale, material: str):
        self.bpy.ops.mesh.primitive_cube_add(size=1, location=location)
        obj = self.bpy.context.object
        obj.name = name
        obj.scale = scale
        obj.data.materials.append(self.material(material, metallic=0.6 if material in ("iron", "steel") else 0))
        obj.parent = root
        return obj

    def cylinder(self, root, name: str, location, radius, depth, material: str, vertices: int = 32):
        self.bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=location)
        obj = self.bpy.context.object
        obj.name = name
        obj.data.materials.append(self.material(material, metallic=0.65 if material in ("iron", "steel") else 0))
        obj.parent = root
        return obj

    def asset(self, asset: str, object_id: str, slot: str, size: str):
        if object_id in self.objects:
            fail(f"duplicate object id reached Blender: {object_id}")
        if slot not in SLOTS or size not in SIZES:
            fail("unknown semantic slot or size reached Blender")
        root = self.root(object_id, SLOTS[slot])
        factor = SIZES[size]
        root.scale = (factor, factor, factor)
        builders = {
            "ground": self.build_ground,
            "plank": self.build_plank,
            "nail": self.build_nail,
            "hammer": self.build_hammer,
            "pile": self.build_pile,
            "ram": self.build_ram,
            "iron_block": self.build_ram,
            "anvil": self.build_anvil,
            "cylinder": self.build_cylinder,
            "guide_rods": self.build_guides,
            "crane": self.build_crane,
            "house": self.build_house,
            "wall": self.build_wall,
            "fence": self.build_fence,
            "bridge": self.build_bridge,
            "pump": self.build_pump,
            "pipe": self.build_pipe,
            "water": self.build_water,
            "water_plane": self.build_water,
            "arrow": self.build_arrow,
            "pile_hammer": self.build_pile_hammer,
        }
        builder = builders.get(asset)
        if builder is None:
            fail(f"unknown procedural asset reached Blender: {asset}")
        builder(root)
        self.objects[object_id] = root
        self.kinds[object_id] = asset
        return root

    def build_ground(self, root):
        self.cube(root, f"{root.name}_ground", (0, 0, -0.15), (5.5, 4.0, 0.15), "ground")

    def build_plank(self, root):
        self.cube(root, f"{root.name}_plank", (0, 0, 0), (2.2, 0.75, 0.18), "wood")

    def build_nail(self, root):
        self.cylinder(root, f"{root.name}_shaft", (0, 0, 0.55), 0.10, 1.15, "steel", 20)
        self.cylinder(root, f"{root.name}_head", (0, 0, 1.14), 0.24, 0.10, "steel", 20)

    def build_hammer(self, root):
        handle = self.cylinder(root, f"{root.name}_handle", (0, 0, 0.8), 0.11, 1.6, "wood", 20)
        handle.rotation_euler[1] = math.radians(-18)
        self.cube(root, f"{root.name}_head", (-0.24, 0, 1.6), (0.62, 0.22, 0.24), "iron")

    def build_pile(self, root):
        self.cylinder(root, f"{root.name}_pile", (0, 0, 1.4), 0.32, 3.2, "steel", 28)

    def build_ram(self, root):
        self.cylinder(root, f"{root.name}_ram", (0, 0, 1.6), 0.56, 1.8, "iron", 28)

    def build_anvil(self, root):
        self.cube(root, f"{root.name}_anvil", (0, 0, 0.4), (0.85, 0.7, 0.4), "iron")

    def build_cylinder(self, root):
        self.cylinder(root, f"{root.name}_shell", (0, 0, 1.9), 0.95, 3.8, "accent", 40)
        self.cylinder(root, f"{root.name}_bore", (0, 0, 1.9), 0.63, 3.86, "dark", 40)
        self.bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.46, location=(0, 0, 0.72))
        ignition = self.bpy.context.object
        ignition.name = f"{root.name}_ignition"
        ignition.data.materials.append(self.material("ignition", emission=5.0))
        ignition.scale = (0.03, 0.03, 0.03)
        ignition.parent = root

    def build_guides(self, root):
        for x in (-0.88, 0.88):
            self.cylinder(root, f"{root.name}_{'l' if x < 0 else 'r'}", (x, 0, 2.0), 0.12, 4.0, "steel", 20)

    def build_crane(self, root):
        self.cube(root, f"{root.name}_mast", (0, 0, 1.7), (0.22, 0.22, 1.7), "accent")
        boom = self.cube(root, f"{root.name}_boom", (1.2, 0, 3.3), (1.45, 0.16, 0.16), "accent")
        boom.rotation_euler[1] = math.radians(-10)

    def build_house(self, root):
        self.cube(root, f"{root.name}_body", (0, 0, 0.7), (0.9, 0.75, 0.7), "light")
        roof = self.cube(root, f"{root.name}_roof", (0, 0, 1.55), (1.05, 0.9, 0.16), "accent")
        roof.rotation_euler[1] = math.radians(22)

    def build_wall(self, root):
        self.cube(root, f"{root.name}_wall", (0, 0, 0.75), (1.8, 0.24, 0.75), "light")

    def build_fence(self, root):
        for x in (-1.4, -0.7, 0, 0.7, 1.4):
            self.cube(root, f"{root.name}_post_{x}", (x, 0, 0.65), (0.06, 0.06, 0.65), "wood")
        self.cube(root, f"{root.name}_rail", (0, 0, 0.75), (1.55, 0.05, 0.07), "wood")

    def build_bridge(self, root):
        self.cube(root, f"{root.name}_deck", (0, 0, 0.35), (2.2, 0.65, 0.16), "wood")
        for x in (-1.9, 1.9):
            self.cube(root, f"{root.name}_pier_{x}", (x, 0, 0), (0.14, 0.6, 0.5), "iron")

    def build_pump(self, root):
        self.cylinder(root, f"{root.name}_body", (0, 0, 0.7), 0.65, 1.4, "accent", 32)
        self.cylinder(root, f"{root.name}_motor", (0, 0, 1.5), 0.34, 0.6, "iron", 28)

    def build_pipe(self, root):
        pipe = self.cylinder(root, f"{root.name}_pipe", (0, 0, 0.8), 0.18, 2.4, "steel", 24)
        pipe.rotation_euler[1] = math.radians(90)

    def build_water(self, root):
        self.cube(root, f"{root.name}_water", (0, 0, 0.05), (2.5, 2.0, 0.05), "water")

    def build_arrow(self, root):
        self.cylinder(root, f"{root.name}_shaft", (0, 0, 0.65), 0.07, 1.3, "accent", 20)
        self.bpy.ops.mesh.primitive_cone_add(vertices=20, radius1=0.24, radius2=0, depth=0.48, location=(0, 0, 1.52))
        head = self.bpy.context.object
        head.name = f"{root.name}_head"
        head.data.materials.append(self.material("accent"))
        head.parent = root

    def build_pile_hammer(self, root):
        self.build_guides(root)
        self.build_cylinder(root)
        self.build_ram(root)
        self.build_anvil(root)

    def configure(self):
        bpy = self.bpy
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.render.engine = ENGINE
        # H.264 needs even sides, so the scaled size is rounded down to even
        # pixels here rather than left to Blender's percentage.
        percentage = int(self.render["resolution_percentage"])
        scene.render.resolution_x = max(2, int(self.render["width"]) * percentage // 200 * 2)
        scene.render.resolution_y = max(2, int(self.render["height"]) * percentage // 200 * 2)
        scene.render.resolution_percentage = 100
        scene.render.fps = self.fps
        scene.render.image_settings.file_format = "FFMPEG"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.ffmpeg.constant_rate_factor = "PERC_LOSSLESS"
        scene.render.film_transparent = False
        scene.render.image_settings.color_mode = "RGB"
        scene.eevee.taa_render_samples = int(self.render["samples"])
        # use_empty removes every world, so the scene has none until we add one.
        if scene.world is None:
            scene.world = bpy.data.worlds.new("clipper_world")
        scene.world.color = (0.025, 0.035, 0.05)
        scene.render.filepath = str(self.output / "scene.part.mp4")
        scene.render.image_settings.color_depth = "8"
        self.add_lighting()
        self.add_camera()

    def add_lighting(self):
        bpy = self.bpy
        bpy.ops.object.light_add(type="AREA", location=(4.5, -5.5, 9.0))
        key = bpy.context.object
        key.name = "key_light"
        key.data.energy = 1100
        key.data.shape = "DISK"
        key.data.size = 5.0
        bpy.ops.object.light_add(type="AREA", location=(-5.0, 1.0, 5.0))
        fill = bpy.context.object
        fill.name = "fill_light"
        fill.data.energy = 700
        fill.data.size = 4.0
        bpy.ops.object.light_add(type="AREA", location=(0, 5.0, 6.5))
        rim = bpy.context.object
        rim.name = "rim_light"
        rim.data.energy = 900
        rim.data.size = 3.0

    def track(self, obj, point):
        """Point the object's -Z axis (a camera's view) at ``point``.

        Tilt is measured from straight down: 0 looks down, 90 degrees looks level.
        """
        dx, dy, dz = point[0] - obj.location.x, point[1] - obj.location.y, point[2] - obj.location.z
        obj.rotation_euler = (math.atan2(math.sqrt(dx * dx + dy * dy), -dz), 0, math.atan2(dy, dx) - math.pi / 2)

    def face(self, obj, point):
        """Turn the object's +Z axis (a text's front) toward ``point``."""
        loc = obj.location
        self.track(obj, (2 * loc.x - point[0], 2 * loc.y - point[1], 2 * loc.z - point[2]))

    def add_camera(self):
        bpy = self.bpy
        bpy.ops.object.camera_add(location=(9.5, -14.0, 8.2))
        camera = bpy.context.object
        camera.name = "camera_main"
        camera.data.type = "PERSP"
        # About 37 degrees of frame height in both formats (vertical fits the
        # 24 mm sensor height, horizontal the 36 mm width). The old 58/52 mm
        # lenses cut the assets and their labels off.
        camera.data.lens = 30 if self.render["width"] > self.render["height"] else 35
        camera.data.sensor_fit = "VERTICAL" if self.render["height"] > self.render["width"] else "HORIZONTAL"
        self.track(camera, (0, 0, 1.8))
        bpy.context.scene.camera = camera
        self.camera = camera
        # The proof uses perspective motion, but both fixed code-owned rigs are
        # present for shot previews and future deliberate orthographic cuts.
        ortho_data = bpy.data.cameras.new("camera_ortho_data")
        ortho_data.type = "ORTHO"
        ortho_data.ortho_scale = 8.5
        ortho = bpy.data.objects.new("camera_ortho", ortho_data)
        ortho.location = (8.0, -12.0, 7.5)
        bpy.context.collection.objects.link(ortho)
        self.track(ortho, (0, 0, 1.8))
        self.ortho_camera = ortho

    def frame(self, word: int) -> int:
        return 1 + round(word * self.word_frames)

    def key(self, obj, frame: int, paths=("location",)):
        for path in paths:
            obj.keyframe_insert(data_path=path, frame=frame)

    def require(self, object_id: str):
        obj = self.objects.get(object_id)
        if obj is None:
            fail(f"unknown object id reached Blender: {object_id}")
        return obj

    def animate(self, operation: dict):
        verb, args, start = operation["verb"], operation["args"], self.frame(operation["word"])
        end = start + self.duration
        self.schedule.append({"frame": start, "word": operation["word"], "verb": verb, "args": args})
        if verb == "add":
            if len(args) != 4:
                fail("add operation has wrong arity")
            asset, slot, size, object_id = map(str, args)
            obj = self.asset(asset, object_id, slot, size)
            obj.scale = tuple(value * 0.02 for value in obj.scale)
            self.key(obj, start, ("scale",))
            obj.scale = tuple(value * 50 for value in obj.scale)
            self.key(obj, end, ("scale",))
        elif verb == "scale":
            if len(args) != 2:
                fail("scale operation has wrong arity")
            obj, value = self.require(str(args[0])), args[1]
            factor = SIZES.get(str(value))
            if factor is None:
                factor = float(value)
            self.key(obj, start, ("scale",))
            obj.scale = tuple(value * factor for value in obj.scale)
            self.key(obj, end, ("scale",))
        elif verb == "lift":
            obj = self.require(str(args[0]))
            self.key(obj, start)
            obj.location.z += 2.3
            self.key(obj, end)
        elif verb in ("drop", "strike"):
            if len(args) < 1:
                fail(f"{verb} operation has wrong arity")
            obj = self.require(str(args[0]))
            target = self.require(str(args[1])) if len(args) > 1 else None
            self.key(obj, start)
            if verb == "strike":
                obj.rotation_euler[1] -= math.radians(58)
                self.key(obj, end, ("location", "rotation_euler"))
            else:
                obj.location.z = (target.location.z + 1.1) if target else max(0.8, obj.location.z - 2.3)
                self.key(obj, end)
        elif verb == "explode":
            root = self.require(str(args[0]))
            for index, child in enumerate(sorted(root.children, key=lambda item: item.name)):
                self.key(child, start)
                angle = 2 * math.pi * index / max(1, len(root.children))
                child.location.x += 1.4 * math.cos(angle)
                child.location.y += 1.4 * math.sin(angle)
                child.location.z += 0.3 * (index % 3)
                self.key(child, end)
        elif verb == "cutaway":
            object_id = str(args[0])
            root = self.require(object_id)
            kind = self.kinds.get(object_id)
            name = f"{root.name}_{CUTAWAY_PARTS.get(kind)}"
            # Blender adds ".001" when a name is already taken.
            shell = next((child for child in root.children
                          if child.name == name or child.name.startswith(name + ".")), None)
            if shell is None:
                fail(f"cutaway target '{object_id}' ({kind}) has no code-defined part to open")
            self.key(shell, start, ("scale",))
            shell.scale.y *= 0.38
            self.key(shell, end, ("scale",))
        elif verb == "label":
            if len(args) != 2:
                fail("label operation has wrong arity")
            self.add_label(str(args[0]), str(args[1]), start, end)
        elif verb == "cycle":
            if len(args) != 2:
                fail("cycle operation has wrong arity")
            self.cycle(str(args[0]), int(args[1]), start)
        elif verb == "camera":
            if not args:
                fail("camera operation has wrong arity")
            self.move_camera(str(args[0]), str(args[1]) if len(args) > 1 else None, start, end)

    def add_label(self, target_id: str, text: str, start: int, end: int):
        if len(text) > 48 or any(ord(char) < 32 for char in text):
            fail("unsafe label text reached Blender")
        target = self.require(target_id)
        self.labels += 1
        curve = self.bpy.data.curves.new(f"label_curve_{self.labels:02d}", "FONT")
        curve.body = text
        curve.align_x = "CENTER"
        curve.size = 0.46
        curve.extrude = 0.012
        obj = self.bpy.data.objects.new(f"label_{target_id}_{self.labels:02d}", curve)
        obj.data.materials.append(self.material("label", roughness=0.4))
        # Above the object's highest point: a label placed at a fixed height sat
        # inside tall assets (the pile hammer's shell) and could not be seen.
        obj.location = (target.location.x, target.location.y, self.top(target) + 0.55)
        self.bpy.context.collection.objects.link(obj)
        self.face(obj, self.camera.location)
        obj.scale = (0.01, 0.01, 0.01)
        self.key(obj, start, ("scale",))
        obj.scale = (1, 1, 1)
        self.key(obj, end, ("scale",))

    def top(self, root) -> float:
        """World height of an asset's highest point, from its parts' own boxes.

        Read from the stored values, not the evaluated scene, so the scale
        keyframes of an entrance do not shrink the answer.
        """
        best = root.location.z + 2.2 * root.scale.z
        for child in root.children:
            height = max(corner[2] for corner in child.bound_box) * abs(child.scale.z)
            best = max(best, root.location.z + root.scale.z * (child.location.z + height))
        return best

    def cycle(self, target_id: str, count: int, start: int):
        if count not in range(1, 7):
            fail("cycle count is outside renderer bounds")
        root = self.require(target_id)
        moving = next((child for child in root.children if "ram" in child.name), root)
        ignition = next((child for child in root.children if "ignition" in child.name), None)
        step = max(6, round(self.fps * 0.65))
        base = moving.location.z
        for index in range(count):
            frame = start + step * index
            moving.location.z = base
            self.key(moving, frame)
            moving.location.z = base + 1.25
            self.key(moving, frame + step // 2)
            moving.location.z = base
            self.key(moving, frame + step)
            if ignition is not None:
                ignition.scale = (0.03, 0.03, 0.03)
                self.key(ignition, frame, ("scale",))
                ignition.scale = (1.0, 1.0, 1.0)
                self.key(ignition, frame + step - 2, ("scale",))
                ignition.scale = (0.03, 0.03, 0.03)
                self.key(ignition, frame + step, ("scale",))

    def move_camera(self, move: str, target_id: str | None, start: int, end: int):
        target = tuple(self.require(target_id).location) if target_id else (0, 0, 1.8)
        camera = self.camera
        self.key(camera, start, ("location", "rotation_euler"))
        if move == "wide":
            camera.location = (target[0] + 11.5, target[1] - 16.5, target[2] + 9.5)
        elif move == "push" or move == "close":
            camera.location = (target[0] + 5.0, target[1] - 8.0, target[2] + 4.2)
        elif move == "pull":
            camera.location = (target[0] + 13.0, target[1] - 18.5, target[2] + 10.0)
        elif move == "orbit":
            camera.location = (target[0] - 9.5, target[1] - 11.5, target[2] + 7.0)
        elif move in ("follow", "cut"):
            camera.location = (target[0] + 7.0, target[1] - 10.5, target[2] + 5.0)
        else:
            fail(f"unknown camera move reached Blender: {move}")
        self.track(camera, (target[0], target[1], target[2] + 0.8))
        self.key(camera, end, ("location", "rotation_euler"))

    def run(self):
        self.configure()
        last_frame = 1
        for operation in self.plan["operations"]:
            self.animate(operation)
            last_frame = max(last_frame, self.frame(operation["word"]) + self.duration)
        scene = self.bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = last_frame
        return last_frame


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    output = Path(args.output).resolve()
    if not output.is_dir():
        fail("render directory does not exist")
    plan = read_contract(Path(args.plan), output)
    try:
        import bpy
    except ImportError:
        fail("this renderer must run inside Blender")

    started = time.monotonic()
    renderer = Scene(bpy, plan, output)
    frame_end = renderer.run()
    total = frame_end

    def progress(scene):
        print(f"CLIPPER_PROGRESS {scene.frame_current} {total}", flush=True)

    bpy.app.handlers.render_write.append(progress)
    try:
        bpy.ops.render.render(animation=True)
    except Exception as err:
        fail(f"render failed: {str(err)[:300]}")
    source = contained(output / "scene.part.mp4", output)
    if not source.is_file():
        fail("Blender did not produce its intermediate video")
    elapsed = time.monotonic() - started
    try:  # Only known once Eevee has rendered: before that background mode has no GPU context.
        import gpu

        gpu_renderer = f"{gpu.platform.renderer_get()} ({gpu.platform.backend_type_get()})"
    except Exception:  # noqa: BLE001 - a diagnostic must never fail a finished render
        gpu_renderer = "unknown"
    manifest = {
        "gpu_renderer": gpu_renderer,
        "contract_version": CONTRACT_VERSION,
        "blender_version": bpy.app.version_string,
        "engine": ENGINE,
        "asset_pack_version": ASSET_PACK_VERSION,
        "frame_start": 1,
        "frame_end": frame_end,
        "fps": renderer.fps,
        "render_width": int(renderer.render["width"]),
        "render_height": int(renderer.render["height"]),
        "resolution_percentage": int(renderer.render["resolution_percentage"]),
        "samples": int(renderer.render["samples"]),
        "render_seconds": round(elapsed, 3),
        "peak_intermediate_bytes": source.stat().st_size,
        "plan_hash": plan["plan_hash"],
        "object_ids": sorted(renderer.objects),
        "keyframe_schedule": renderer.schedule,
    }
    part = contained(output / "manifest.part.json", output)
    part.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    print(f"CLIPPER_DONE {source}", flush=True)


if __name__ == "__main__":
    main()
