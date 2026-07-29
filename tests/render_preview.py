"""Render a preview board.

Not a test — a look check. A visualisation tool is only working if the output
reads as hardware, and that cannot be asserted, only looked at.

    python3 tests/render_preview.py [output.png]
"""

from __future__ import annotations

import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUTPUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pcb_preview.png"
SAMPLES = int(os.environ.get("PCB_SAMPLES", "48"))
RES = int(os.environ.get("PCB_RES", "1100"))


def build_scene():
    import pcb_generator
    from pcb_generator.build import board as board_build
    from pcb_generator.build import components as components_build
    from pcb_generator.build import detail as detail_build
    from pcb_generator.data import properties

    pcb_generator.register()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.pcb.setup_scene()

    # A headset-ish board: rounded, with a cutout for the driver.
    outline = board_build.make_rounded_rect(46.0, 30.0, 4.0)
    hole = [(9.0, -5.0), (9.0, 5.0), (19.0, 5.0), (19.0, -5.0)]  # clockwise

    board = board_build.build_board("PCB", outline, [hole], thickness=1.0)

    props = board.pcb_board
    props.mask_color = os.environ.get("PCB_MASK", "MATTE_BLACK")
    props.finish = "ENIG"
    props.gloss = 0.3
    props.detail_seed = 7
    props.trace_density = 1.4
    props.scatter_seed = 3
    props.scatter_density = 1.5
    props.edge_margin = 1.0
    board_build.apply_board_materials(board, props)

    # Notable parts, placed deliberately.
    components_build.create_component(
        board, "usb_c", position=(-23.0, 0.0), anchor="EDGE"
    )
    components_build.create_component(
        board, "tact_6mm_50", position=(-6.0, 10.0), anchor="FREE"
    )
    components_build.create_component(
        board, "tact_6mm_50", position=(2.0, 10.0), anchor="FREE"
    )
    components_build.create_component(
        board, "fpc_10", position=(-4.0, -11.0), anchor="FREE", rotation=math.pi
    )
    components_build.create_component(
        board, "qfn32", position=(-12.0, -2.0), anchor="FREE"
    )
    components_build.create_component(
        board, "mount_m2", position=(20.0, 12.0), anchor="FREE"
    )

    components_build.generate_scatter(board)
    stats = detail_build.generate(board)
    print(
        f"board: {stats.traces} trace runs, {stats.vias} vias, "
        f"{stats.pads} pads, {len(components_build.scatter_objects(board))} scattered"
    )
    return board


def setup_render(board):
    scene = bpy.context.scene

    scene.render.engine = "CYCLES"
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.cycles.device = "CPU"
    scene.render.resolution_x = RES
    scene.render.resolution_y = int(RES * 0.68)
    scene.render.film_transparent = False

    # World: soft neutral studio grey.
    world = bpy.data.worlds.new("PCBWorld")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.05, 0.055, 0.06, 1.0)
    bg.inputs["Strength"].default_value = 1.0

    # Three-quarter view, close enough to read the traces.
    camera_data = bpy.data.cameras.new("Camera")
    camera_data.lens = 62.0
    camera = bpy.data.objects.new("Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    target = Vector((0.0, 0.0, 0.0))
    camera.location = Vector((44.0, -58.0, 52.0))
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Key light, large and soft so the soldermask reads as a sheen rather than a
    # hotspot, plus a cool rim to separate the board from the background.
    key_data = bpy.data.lights.new("Key", "AREA")
    key_data.energy = 260000.0
    key_data.size = 90.0
    key_data.color = (1.0, 0.96, 0.90)
    key = bpy.data.objects.new("Key", key_data)
    scene.collection.objects.link(key)
    key.location = (40.0, -46.0, 62.0)
    key.rotation_euler = (Vector((0, 0, 0)) - key.location).to_track_quat(
        "-Z", "Y"
    ).to_euler()

    rim_data = bpy.data.lights.new("Rim", "AREA")
    rim_data.energy = 90000.0
    rim_data.size = 70.0
    rim_data.color = (0.72, 0.82, 1.0)
    rim = bpy.data.objects.new("Rim", rim_data)
    scene.collection.objects.link(rim)
    rim.location = (-48.0, 40.0, 34.0)
    rim.rotation_euler = (Vector((0, 0, 0)) - rim.location).to_track_quat(
        "-Z", "Y"
    ).to_euler()

    fill_data = bpy.data.lights.new("Fill", "AREA")
    fill_data.energy = 40000.0
    fill_data.size = 120.0
    fill = bpy.data.objects.new("Fill", fill_data)
    scene.collection.objects.link(fill)
    fill.location = (-30.0, -50.0, 30.0)
    fill.rotation_euler = (Vector((0, 0, 0)) - fill.location).to_track_quat(
        "-Z", "Y"
    ).to_euler()

    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"


def main():
    board = build_scene()
    setup_render(board)

    bpy.context.scene.render.filepath = OUTPUT
    bpy.ops.render.render(write_still=True)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
