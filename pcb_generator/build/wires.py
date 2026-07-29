"""Point-to-point wire runs.

Thin discrete wires — board to driver, board to anything. A curve with a bevel,
hooked to empties at both ends so the wire follows when either end moves.

The detail that matters is slack. A wire drawn as a straight line between two
points reads as a CAD annotation; the same wire with a little sag and a lift out
of its solder joint reads as something a person installed.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import bpy
from mathutils import Vector

from ..shading import materials
from . import board as board_build
from . import meshkit

WIRE_COLLECTION_SUFFIX = "_Wires"
HOOK_PREFIX = "PCB_WireEnd"


def wire_collection(board: bpy.types.Object) -> bpy.types.Collection:
    return meshkit.ensure_collection(
        f"{board.name}{WIRE_COLLECTION_SUFFIX}", board_build.board_collection()
    )


def _make_endpoint(
    name: str,
    location: Vector,
    parent: Optional[bpy.types.Object],
    collection: bpy.types.Collection,
) -> bpy.types.Object:
    """An empty marking one end of a wire.

    Binding to empties rather than to mesh vertices means the wire survives the
    board being regenerated or the target being remodelled — the endpoints are
    the contract, not whatever geometry happened to be under them.
    """
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = "SPHERE"
    empty.empty_display_size = 0.6
    collection.objects.link(empty)
    empty.location = location

    if parent is not None:
        empty.parent = parent
        empty.matrix_parent_inverse = parent.matrix_world.inverted()

    return empty


def create_wire(
    board: bpy.types.Object,
    start_world: Vector,
    end_world: Vector,
    start_parent: Optional[bpy.types.Object] = None,
    end_parent: Optional[bpy.types.Object] = None,
    gauge: float = 0.4,
    slack: float = 0.25,
    color: Tuple[float, float, float, float] = (0.02, 0.02, 0.022, 1.0),
) -> bpy.types.Object:
    """Build a wire between two world-space points."""
    collection = wire_collection(board)

    index = 1
    while bpy.data.objects.get(f"{board.name}_Wire{index}") is not None:
        index += 1
    name = f"{board.name}_Wire{index}"

    start_local = (
        start_parent.matrix_world.inverted() @ start_world
        if start_parent
        else start_world
    )
    end_local = (
        end_parent.matrix_world.inverted() @ end_world if end_parent else end_world
    )

    hook_a = _make_endpoint(f"{HOOK_PREFIX}_{index}_A", start_local, start_parent, collection)
    hook_b = _make_endpoint(f"{HOOK_PREFIX}_{index}_B", end_local, end_parent, collection)

    curve = bpy.data.curves.new(f"{name}_curve", "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12
    curve.bevel_depth = gauge * 0.5
    curve.bevel_resolution = 4
    curve.use_fill_caps = True

    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(2)

    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)

    obj["pcb_wire_slack"] = slack
    obj["pcb_wire_a"] = hook_a.name
    obj["pcb_wire_b"] = hook_b.name

    material = materials.build_wire_material(color)
    obj.data.materials.append(material)

    # The hooks were parented a moment ago; flush before reading their matrices.
    meshkit.sync_transforms()
    update_wire(obj)

    # Hooks drive the first and last control points; the middle one is
    # recomputed from them, which is what produces the sag.
    for hook, point_index in ((hook_a, 0), (hook_b, 2)):
        modifier = obj.modifiers.new(f"Hook_{point_index}", "HOOK")
        modifier.object = hook
        modifier.vertex_indices_set([point_index])

    return obj


def update_wire(obj: bpy.types.Object) -> bool:
    """Recompute a wire's shape from its endpoint empties."""
    if obj is None or obj.type != "CURVE":
        return False

    hook_a = bpy.data.objects.get(obj.get("pcb_wire_a", ""))
    hook_b = bpy.data.objects.get(obj.get("pcb_wire_b", ""))
    if hook_a is None or hook_b is None:
        return False

    slack = float(obj.get("pcb_wire_slack", 0.25))

    to_local = obj.matrix_world.inverted()
    start = to_local @ hook_a.matrix_world.translation
    end = to_local @ hook_b.matrix_world.translation

    span = (end - start).length
    if span < 1e-6:
        return False

    # Sag hangs below the chord, and the run lifts out of each end rather than
    # leaving at a hard angle.
    middle = (start + end) * 0.5
    middle.z -= span * slack * 0.5

    spline = obj.data.splines[0]
    points = spline.bezier_points

    lift = Vector((0.0, 0.0, span * slack * 0.35))
    handle_span = span * 0.28

    for point, location in zip(points, (start, middle, end)):
        point.co = location
        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"

    direction = (middle - start).normalized()
    points[0].handle_left = start - direction * handle_span * 0.4 + lift
    points[0].handle_right = start + direction * handle_span * 0.4 + lift

    chord = (end - start).normalized()
    points[1].handle_left = middle - chord * handle_span
    points[1].handle_right = middle + chord * handle_span

    direction = (end - middle).normalized()
    points[2].handle_left = end - direction * handle_span * 0.4 + lift
    points[2].handle_right = end + direction * handle_span * 0.4 + lift

    obj.data.update_tag()
    return True


def update_all_wires(board: bpy.types.Object) -> int:
    collection = bpy.data.collections.get(f"{board.name}{WIRE_COLLECTION_SUFFIX}")
    if collection is None:
        return 0
    meshkit.sync_transforms()
    return sum(1 for obj in collection.objects if update_wire(obj))
