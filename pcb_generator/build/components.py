"""Component instantiation, anchoring and scatter.

Two populations share one code path here:

* **Precisely placed** parts — ports, buttons, connectors — carry anchors and
  survive regeneration. They claim keep-out space first.
* **Scattered** filler — passives and small ICs — is decoration, regenerated
  wholesale from a seed and never individually edited.

Both are real objects sharing cached mesh datablocks, so several hundred
scattered 0402s cost one mesh between them.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import bpy
from mathutils import Euler, Vector

from ..core import placement as core_placement
from ..core import scatter as core_scatter
from ..core.routing import Keepout
from ..core.scatter import ScatterConfig
from ..data import properties
from ..parts import library
from ..parts.library import PartDef
from ..shading import materials
from . import board as board_build
from . import meshkit

Vec2 = Tuple[float, float]

SCATTER_FLAG = "pcb_scatter"
SCATTER_KEY = "pcb_part_key"
SUFFIX_SCATTER = "_Scatter"


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------


def part_for(obj: bpy.types.Object) -> Optional[PartDef]:
    """Part definition behind a component or scattered object."""
    if properties.is_component(obj):
        return library.get(obj.pcb_component.part_key)
    key = obj.get(SCATTER_KEY)
    return library.get(key) if key else None


def scatter_collection_name(board: bpy.types.Object) -> str:
    return f"{board.name}{SUFFIX_SCATTER}"


def scatter_objects(board: bpy.types.Object) -> List[bpy.types.Object]:
    collection = bpy.data.collections.get(scatter_collection_name(board))
    if collection is None:
        return []
    return [obj for obj in collection.objects if obj.get(SCATTER_FLAG)]


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------


def _component_z(board: bpy.types.Object, side: str) -> float:
    surface = board_build.board_surface_z(board)
    if side == "BOTTOM":
        return surface - board.pcb_board.thickness
    return surface


def apply_transform(
    obj: bpy.types.Object,
    board: bpy.types.Object,
    position: Vec2,
    rotation: float,
    side: str = "TOP",
) -> None:
    obj.location = (position[0], position[1], _component_z(board, side))
    if side == "BOTTOM":
        # Flip so the part's body extends away from the board's underside.
        obj.rotation_euler = Euler((math.pi, 0.0, rotation), "XYZ")
    else:
        obj.rotation_euler = Euler((0.0, 0.0, rotation), "XYZ")


def anchor_spec_for(
    obj: bpy.types.Object, board: bpy.types.Object
) -> core_placement.AnchorSpec:
    """Build a core anchor spec from a component's properties.

    The target object's world position is converted into board space here, so
    the solver itself never needs to know about Blender transforms.
    """
    comp = obj.pcb_component

    target_position: Optional[Vec2] = None
    if comp.anchor == "TARGET" and comp.target is not None:
        local = board.matrix_world.inverted() @ comp.target.matrix_world.translation
        target_position = (local.x, local.y)

    return core_placement.AnchorSpec(
        mode=comp.anchor,
        position=(obj.location.x, obj.location.y),
        rotation=comp.rotation,
        edge_t=comp.edge_t,
        edge_offset=comp.edge_offset,
        align_to_edge=comp.align_to_edge,
        target_position=target_position,
        target_offset=tuple(comp.target_offset),
        snap_pitch=comp.snap_pitch,
    )


def resolve_component(
    obj: bpy.types.Object, board: bpy.types.Object, outer: Sequence[Vec2]
) -> core_placement.Placement:
    """Re-solve one component's anchor and move it."""
    spec = anchor_spec_for(obj, board)
    result = core_placement.resolve(spec, outer)
    apply_transform(obj, board, result.position, result.rotation, obj.pcb_component.side)
    return result


def resolve_all(board: bpy.types.Object) -> Tuple[int, List[str]]:
    """Re-solve every component on a board. Returns count and any warnings."""
    meshkit.sync_transforms()
    outer, _ = properties.load_outline(board)
    warnings: List[str] = []
    count = 0

    for obj in properties.board_components(board):
        result = resolve_component(obj, board, outer)
        count += 1
        if not result.resolved and result.note:
            warnings.append(f"{obj.pcb_component.ref or obj.name}: {result.note}")

    return (count, warnings)


def create_component(
    board: bpy.types.Object,
    part_key: str,
    position: Vec2 = (0.0, 0.0),
    rotation: float = 0.0,
    anchor: str = "FREE",
    target: Optional[bpy.types.Object] = None,
    side: str = "TOP",
) -> Optional[bpy.types.Object]:
    """Add a precisely placed component to a board."""
    part = library.get(part_key)
    if part is None:
        return None

    materials.build_part_materials()
    mesh = library.build_mesh(part_key)
    if mesh is None:
        return None

    ref = properties.next_ref(board, part.ref_prefix)
    obj = bpy.data.objects.new(f"{board.name}_{ref}_{part_key}", mesh)

    collection = board_build.board_collection()
    collection.objects.link(obj)

    comp = obj.pcb_component
    comp.is_component = True
    comp.part_key = part_key
    comp.ref = ref
    comp.board = board
    comp.anchor = anchor
    comp.side = side
    comp.rotation = rotation
    comp.target = target

    outer, _ = properties.load_outline(board)

    if anchor == "EDGE" and len(outer) >= 3:
        spec = core_placement.bind_to_edge(outer, position)
        comp.edge_t = spec.edge_t
        # Push the part clear of the edge by half its depth so it sits on the
        # board rather than straddling the boundary.
        comp.edge_offset = -part.depth * 0.5
    elif anchor == "TARGET" and target is not None:
        comp.target_offset = (0.0, 0.0)

    obj.parent = board
    obj.matrix_parent_inverse = board.matrix_world.inverted()

    if anchor == "FREE":
        apply_transform(obj, board, position, rotation, side)
    else:
        # A target may have been moved moments ago; its world matrix has to be
        # current before the anchor reads it.
        meshkit.sync_transforms()
        resolve_component(obj, board, outer)

    return obj


def delete_component(obj: bpy.types.Object) -> None:
    """Remove a component object only.

    Part meshes are cached and shared between every instance of that part, so
    the datablock must outlive any single component.
    """
    bpy.data.objects.remove(obj, do_unlink=True)
    library.clear_cached_meshes()


# --------------------------------------------------------------------------
# Keep-outs and pads
# --------------------------------------------------------------------------


def _footprint_keepout(
    obj: bpy.types.Object, part: PartDef, margin: float
) -> Keepout:
    return Keepout(
        center=(obj.location.x, obj.location.y),
        width=part.width + margin * 2.0,
        height=part.depth + margin * 2.0,
        rotation=obj.rotation_euler.z,
    )


def _pads_for(obj: bpy.types.Object, part: PartDef) -> List[Vec2]:
    rotation = obj.rotation_euler.z
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    return [
        (
            obj.location.x + ox * cos_r - oy * sin_r,
            obj.location.y + ox * sin_r + oy * cos_r,
        )
        for ox, oy in part.pad_offsets
    ]


def collect_occupancy(
    board: bpy.types.Object, include_scatter: bool = True
) -> Tuple[List[Keepout], List[Vec2]]:
    """Keep-outs and pad anchors for everything sitting on a board.

    Reading this back from the scene rather than caching it means routing always
    works against what is actually there, however the parts got placed.
    """
    keepouts: List[Keepout] = []
    pads: List[Vec2] = []

    for obj in properties.board_components(board):
        part = part_for(obj)
        if part is None:
            continue
        if obj.pcb_component.side != "TOP":
            continue
        keepouts.append(
            _footprint_keepout(obj, part, obj.pcb_component.keepout_margin)
        )
        pads.extend(_pads_for(obj, part))

    if include_scatter:
        for obj in scatter_objects(board):
            part = part_for(obj)
            if part is None:
                continue
            keepouts.append(_footprint_keepout(obj, part, 0.2))
            pads.extend(_pads_for(obj, part))

    return (keepouts, pads)


# --------------------------------------------------------------------------
# Scatter
# --------------------------------------------------------------------------


def clear_scatter(board: bpy.types.Object) -> int:
    collection = bpy.data.collections.get(scatter_collection_name(board))
    if collection is None:
        return 0

    removed = 0
    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1

    bpy.data.collections.remove(collection)
    library.clear_cached_meshes()
    return removed


def generate_scatter(board: bpy.types.Object) -> int:
    """Populate the board with filler parts."""
    props = board.pcb_board
    outer, holes = properties.load_outline(board)
    if len(outer) < 3:
        return 0

    clear_scatter(board)

    # Precisely placed parts claim their space before filler goes anywhere.
    keepouts, _ = collect_occupancy(board, include_scatter=False)

    specs = library.scatter_specs()
    config = ScatterConfig(
        density=props.scatter_density,
        edge_margin=props.scatter_margin,
        spacing=props.scatter_spacing,
        cluster_tightness=props.scatter_clustering,
        seed=props.scatter_seed,
    )

    result = core_scatter.scatter_parts(outer, holes, keepouts, specs, config)
    if not result.items:
        return 0

    materials.build_part_materials()

    parent_collection = board_build.board_collection()
    collection = meshkit.ensure_collection(
        scatter_collection_name(board), parent_collection
    )

    surface_z = board_build.board_surface_z(board)
    parent_inverse = board.matrix_world.inverted()

    for index, item in enumerate(result.items):
        mesh = library.build_mesh(item.key)
        if mesh is None:
            continue

        obj = bpy.data.objects.new(f"{board.name}_s{index:04d}_{item.key}", mesh)
        obj[SCATTER_FLAG] = True
        obj[SCATTER_KEY] = item.key

        collection.objects.link(obj)
        obj.parent = board
        obj.matrix_parent_inverse = parent_inverse
        obj.location = (item.position[0], item.position[1], surface_z)
        obj.rotation_euler = Euler((0.0, 0.0, item.rotation), "XYZ")

    return len(result.items)
