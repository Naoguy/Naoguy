"""Headless end-to-end smoke test.

Runs the whole pipeline inside a real Blender: build a board, place components,
bind one to an external target, scatter filler, generate detail, run a wire, and
verify the outline-change path re-solves anchors.

Run with the ``bpy`` module::

    python3 tests/smoke_blender.py

or with a Blender binary::

    blender --background --python tests/smoke_blender.py
"""

from __future__ import annotations

import math
import os
import sys
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILURES = []
CHECKS = 0


def check(condition, message):
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(message)
        print(f"  FAIL  {message}")
    else:
        print(f"  ok    {message}")


def section(title):
    print(f"\n=== {title} ===")


def main() -> int:
    import pcb_generator
    from pcb_generator.build import components as components_build
    from pcb_generator.build import detail as detail_build
    from pcb_generator.data import properties
    from pcb_generator.parts import library

    section("register")
    pcb_generator.register()
    check(hasattr(bpy.types.Object, "pcb_board"), "board properties registered")
    check(hasattr(bpy.types.Scene, "pcb"), "scene properties registered")
    check("add_board" in dir(bpy.ops.pcb), "operators registered")

    # Clean slate.
    bpy.ops.wm.read_factory_settings(use_empty=True)

    section("scene units")
    bpy.ops.pcb.setup_scene()
    check(
        abs(bpy.context.scene.unit_settings.scale_length - 0.001) < 1e-9,
        "scene set to millimetres",
    )

    section("add board")
    bpy.ops.pcb.add_board(width=44.0, height=28.0, corner_radius=3.0, thickness=1.0)
    board = properties.find_board(bpy.context)
    check(board is not None, "board created")
    check(board.pcb_board.is_board, "board flagged")

    outer, holes = properties.load_outline(board)
    check(len(outer) > 8, f"outline stored ({len(outer)} points)")
    check(len(board.data.polygons) > 0, "board mesh has faces")
    check(len(board.data.materials) == 2, "board has mask and substrate slots")

    zs = [v.co.z for v in board.data.vertices]
    check(abs(max(zs)) < 1e-6, "top surface at local z=0")
    check(abs(min(zs) + 1.0) < 1e-6, "board extends down by its thickness")

    section("place a notable component")
    usb = components_build.create_component(
        board, "usb_c", position=(0.0, -12.0), anchor="FREE"
    )
    check(usb is not None, "USB-C created")
    check(usb.pcb_component.ref == "J1", f"auto ref J1 (got {usb.pcb_component.ref})")
    check(len(usb.data.polygons) > 0, "USB-C has geometry")

    section("target binding — the button-cap case")
    cap = bpy.data.objects.new("ButtonCap", None)
    bpy.context.scene.collection.objects.link(cap)
    cap.location = (12.0, 6.0, 20.0)

    switch = components_build.create_component(
        board, "tact_6mm_50", anchor="TARGET", target=cap
    )
    check(switch is not None, "tactile switch created")
    check(
        abs(switch.location.x - 12.0) < 1e-5 and abs(switch.location.y - 6.0) < 1e-5,
        f"switch inherited cap XY (got {switch.location.x:.2f}, {switch.location.y:.2f})",
    )
    check(abs(switch.location.z) < 1e-6, "switch sits on the board surface, not the cap")

    cap.location = (-8.0, -4.0, 20.0)
    bpy.context.view_layer.update()
    components_build.resolve_all(board)
    check(
        abs(switch.location.x + 8.0) < 1e-5 and abs(switch.location.y + 4.0) < 1e-5,
        "switch followed the cap after it moved",
    )

    section("edge binding")
    header = components_build.create_component(
        board, "header_1x4", position=(22.0, 0.0), anchor="EDGE"
    )
    check(header is not None, "edge-anchored header created")
    check(
        header.pcb_component.anchor == "EDGE" and 0.0 <= header.pcb_component.edge_t <= 1.0,
        f"edge parameter stored (t={header.pcb_component.edge_t:.3f})",
    )
    before = (header.location.x, header.location.y)

    section("scatter")
    count = components_build.generate_scatter(board)
    check(count > 10, f"scattered {count} parts")

    scattered = components_build.scatter_objects(board)
    check(len(scattered) == count, "scatter objects tracked")

    meshes = {obj.data.name for obj in scattered}
    check(
        len(meshes) < len(scattered),
        f"part meshes shared ({len(meshes)} meshes for {len(scattered)} objects)",
    )

    from pcb_generator.core import geometry as core_geometry

    outside = [
        obj
        for obj in scattered
        if not core_geometry.point_in_shape(
            (obj.location.x, obj.location.y), outer, holes
        )
    ]
    check(not outside, f"all scattered parts inside the outline ({len(outside)} outside)")

    keepouts, _ = components_build.collect_occupancy(board, include_scatter=False)
    collisions = [
        obj
        for obj in scattered
        if any(k.contains((obj.location.x, obj.location.y)) for k in keepouts)
    ]
    check(not collisions, f"scatter avoided placed parts ({len(collisions)} hits)")

    section("detail generation")
    stats = detail_build.generate(board)
    check(stats.traces > 0, f"routed {stats.traces} trace runs")
    check(stats.vias > 0, f"placed {stats.vias} vias")
    check(stats.pads > 0, f"placed {stats.pads} pads")

    traces_top = bpy.data.objects.get(
        detail_build.detail_object_name(board, detail_build.SUFFIX_TRACES_TOP)
    )
    check(traces_top is not None, "top trace object built")
    check(len(traces_top.data.polygons) > 0, "trace mesh has faces")
    check(traces_top.parent is board, "traces parented to the board")

    for suffix in (
        detail_build.SUFFIX_VIAS,
        detail_build.SUFFIX_PADS,
        detail_build.SUFFIX_SILK,
        detail_build.SUFFIX_POUR,
    ):
        obj = bpy.data.objects.get(detail_build.detail_object_name(board, suffix))
        check(obj is not None and len(obj.data.polygons) > 0, f"{suffix} built")

    section("determinism")
    first = len(traces_top.data.polygons)
    detail_build.generate(board)
    traces_top = bpy.data.objects.get(
        detail_build.detail_object_name(board, detail_build.SUFFIX_TRACES_TOP)
    )
    check(
        len(traces_top.data.polygons) == first,
        "same seed regenerates the same trace count",
    )

    mesh_count_before = len(bpy.data.meshes)
    detail_build.generate(board)
    check(
        len(bpy.data.meshes) <= mesh_count_before,
        "regeneration does not leak mesh datablocks",
    )

    section("outline change re-solves anchors")
    from pcb_generator.build import board as board_build

    new_outline = board_build.make_rounded_rect(60.0, 28.0, 3.0)
    properties.store_outline(board, new_outline, [])
    board_build.build_board(
        board.name, new_outline, [], thickness=board.pcb_board.thickness, existing=board
    )
    components_build.resolve_all(board)

    after = (header.location.x, header.location.y)
    check(after != before, "edge-anchored header moved with the wider outline")
    check(
        abs(switch.location.x + 8.0) < 1e-5,
        "target-anchored switch stayed with its cap",
    )

    section("wires")
    driver = bpy.data.objects.new("AudioDriver", None)
    bpy.context.scene.collection.objects.link(driver)
    driver.location = (0.0, 40.0, 8.0)

    from pcb_generator.build import wires as wires_build

    wire = wires_build.create_wire(
        board,
        start_world=usb.matrix_world.translation.copy(),
        end_world=driver.matrix_world.translation.copy(),
        start_parent=board,
        end_parent=driver,
        gauge=0.5,
        slack=0.3,
    )
    check(wire is not None and wire.type == "CURVE", "wire curve created")
    check(wire.data.bevel_depth > 0.0, "wire has thickness")
    check(len(wire.modifiers) == 2, "wire hooked to both endpoints")

    sag = wire.data.splines[0].bezier_points[1].co.z
    ends = (
        wire.data.splines[0].bezier_points[0].co.z
        + wire.data.splines[0].bezier_points[2].co.z
    ) * 0.5
    check(sag < ends, "wire sags between its endpoints")

    section("adopt an existing object as a board")
    bpy.ops.mesh.primitive_plane_add(size=20.0, location=(0.0, 0.0, 50.0))
    plane = bpy.context.active_object
    ok, message = board_build.adopt_object_as_board(
        plane, bpy.context.evaluated_depsgraph_get()
    )
    check(ok, f"plane adopted as a board ({message})")
    check(plane.pcb_board.is_board, "adopted object flagged as a board")

    adopted_outer, _ = properties.load_outline(plane)
    check(len(adopted_outer) == 4, f"outline extracted ({len(adopted_outer)} points)")

    adopted_stats = detail_build.generate(plane)
    check(adopted_stats.traces > 0, "detail generated on the adopted board")

    section("extract from a solid via planar section")
    bpy.ops.mesh.primitive_cylinder_add(radius=15.0, depth=4.0, location=(80.0, 0.0, 0.0))
    solid = bpy.context.active_object
    section_outer, _, method = board_build.extract_shape(
        solid, bpy.context.evaluated_depsgraph_get(), section_z=0.0
    )
    check(len(section_outer) > 8, f"sliced a closed solid ({method})")

    section("part library")
    check(len(library.all_parts()) >= 30, f"{len(library.all_parts())} parts registered")
    built = 0
    for part in library.all_parts():
        mesh = library.build_mesh(part.key)
        if mesh is not None and len(mesh.polygons) > 0:
            built += 1
        else:
            FAILURES.append(f"part {part.key} built no geometry")
    check(built == len(library.all_parts()), f"every part builds geometry ({built})")

    section("cleanup")
    detail_build.clear_detail(board)
    check(
        bpy.data.objects.get(
            detail_build.detail_object_name(board, detail_build.SUFFIX_TRACES_TOP)
        )
        is None,
        "detail cleared",
    )

    removed = components_build.clear_scatter(board)
    check(removed > 0, f"scatter cleared ({removed} objects)")

    pcb_generator.unregister()
    check(not hasattr(bpy.types.Scene, "pcb"), "unregistered cleanly")

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("\nFailures:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
