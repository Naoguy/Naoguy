"""Board construction and outline extraction.

Three ways in, one representation out. Whatever the source, the result is an
outline plus cutouts expressed in the board object's local XY, with the board's
top surface at a known local Z. Everything downstream is identical regardless of
how the shape arrived.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import bpy
from mathutils import Vector

from ..core import geometry
from ..core.geometry import Ring, Vec2
from ..data import properties
from ..shading import materials
from . import meshkit

BOARD_COLLECTION = "PCB"


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _evaluated_mesh(
    obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph
) -> Optional[bpy.types.Mesh]:
    """Mesh with modifiers applied. Curves and text evaluate to meshes too."""
    try:
        evaluated = obj.evaluated_get(depsgraph)
        return evaluated.to_mesh()
    except (RuntimeError, AttributeError):
        return None


def _release_mesh(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> None:
    try:
        obj.evaluated_get(depsgraph).to_mesh_clear()
    except (RuntimeError, AttributeError):
        pass


def outline_from_object(
    obj: bpy.types.Object,
    depsgraph: bpy.types.Depsgraph,
    section_z: Optional[float] = None,
) -> Tuple[List[Ring], str]:
    """Pull rings out of any object, in that object's local space.

    Tries three strategies in order of how well they preserve intent:

    1. **Wire edges** — a curve, or a mesh with no faces. The edges *are* the
       outline. This is the drawn-outline path.
    2. **Boundary edges** — a flat or open mesh. Edges with exactly one adjacent
       face bound the surface, which is the outline of a board-shaped plane.
    3. **Planar section** — a closed solid has no boundary edges, so slice it and
       chain the resulting segments. This is what makes "point at an existing
       object" work on real CAD geometry.

    Returns the rings and a short label naming the strategy used, so the
    operator can tell the user what it did.
    """
    mesh = _evaluated_mesh(obj, depsgraph)
    if mesh is None:
        return ([], "none")

    try:
        verts = [(v.co.x, v.co.y) for v in mesh.vertices]
        heights = [v.co.z for v in mesh.vertices]

        face_count_per_edge = {}
        for polygon in mesh.polygons:
            for key in polygon.edge_keys:
                ordered = (min(key), max(key))
                face_count_per_edge[ordered] = face_count_per_edge.get(ordered, 0) + 1

        all_edges = [(e.vertices[0], e.vertices[1]) for e in mesh.edges]

        if not mesh.polygons:
            chosen = all_edges
            method = "wire edges"
        else:
            boundary = [
                (a, b)
                for a, b in all_edges
                if face_count_per_edge.get((min(a, b), max(a, b)), 0) == 1
            ]
            if boundary:
                chosen = boundary
                method = "boundary edges"
            else:
                z = section_z
                if z is None:
                    z = (min(heights) + max(heights)) * 0.5
                rings = _planar_section(mesh, z)
                return (rings, f"planar section at z={z:.2f}")

        loops = geometry.loops_from_edges(chosen)
        rings = [
            [verts[i] for i in loop] for loop in loops if len(loop) >= 3
        ]
        return (rings, method)
    finally:
        _release_mesh(obj, depsgraph)


def _planar_section(mesh: bpy.types.Mesh, z: float) -> List[Ring]:
    """Slice a closed mesh at ``z`` and chain the cut segments into rings."""
    coords = [v.co for v in mesh.vertices]
    segments: List[Tuple[Vec2, Vec2]] = []

    for polygon in mesh.polygons:
        crossings: List[Vec2] = []
        indices = list(polygon.vertices)
        for i, vi in enumerate(indices):
            a = coords[vi]
            b = coords[indices[(i + 1) % len(indices)]]
            if (a.z - z) * (b.z - z) < 0.0:
                t = (z - a.z) / (b.z - a.z)
                crossings.append((a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t))
            elif abs(a.z - z) < 1e-9:
                crossings.append((a.x, a.y))

        if len(crossings) >= 2:
            segments.append((crossings[0], crossings[1]))

    if not segments:
        return []

    # Chain segments by snapping endpoints to a tolerance grid.
    tolerance = 1e-4

    def key(point: Vec2) -> Tuple[int, int]:
        return (int(round(point[0] / tolerance)), int(round(point[1] / tolerance)))

    points: dict = {}
    edges: List[Tuple[int, int]] = []
    for a, b in segments:
        for point in (a, b):
            points.setdefault(key(point), (len(points), point))
        ia = points[key(a)][0]
        ib = points[key(b)][0]
        if ia != ib:
            edges.append((ia, ib))

    lookup = {index: point for index, point in points.values()}
    loops = geometry.loops_from_edges(edges)
    return [[lookup[i] for i in loop] for loop in loops if len(loop) >= 3]


def extract_shape(
    obj: bpy.types.Object,
    depsgraph: bpy.types.Depsgraph,
    section_z: Optional[float] = None,
) -> Tuple[Ring, List[Ring], str]:
    """Extraction plus outline/cutout classification."""
    rings, method = outline_from_object(obj, depsgraph, section_z)
    outer, holes = geometry.classify_rings(rings)
    return (outer, holes, method)


# --------------------------------------------------------------------------
# Board building
# --------------------------------------------------------------------------


def board_collection() -> bpy.types.Collection:
    return meshkit.ensure_collection(BOARD_COLLECTION)


def apply_board_materials(obj: bpy.types.Object, props) -> None:
    materials.build_board_materials(
        mask_color=props.mask_color, finish=props.finish, gloss=props.gloss
    )
    materials.build_part_materials()
    materials.assign(obj, (materials.MAT_SOLDERMASK, materials.MAT_SUBSTRATE))


def build_board(
    name: str,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]],
    thickness: float,
    matrix: Optional[object] = None,
    existing: Optional[bpy.types.Object] = None,
) -> Optional[bpy.types.Object]:
    """Create or rebuild a board object from an outline."""
    mesh = meshkit.solid_board_mesh(
        f"{name}_mesh",
        outer,
        holes,
        thickness=thickness,
        top_material=0,
        edge_material=1,
    )
    if mesh is None:
        return None

    collection = board_collection()

    if existing is None:
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        if matrix is not None:
            obj.matrix_world = matrix
    else:
        obj = existing
        old = obj.data
        obj.data = mesh
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)

    props = obj.pcb_board
    props.is_board = True
    props.thickness = thickness

    properties.store_outline(obj, outer, holes)
    apply_board_materials(obj, props)

    return obj


def adopt_object_as_board(
    obj: bpy.types.Object,
    depsgraph: bpy.types.Depsgraph,
    section_z: Optional[float] = None,
) -> Tuple[bool, str]:
    """Turn an existing object into a board without rebuilding its geometry.

    The object keeps its own topology, transform and UVs. Only the outline is
    derived — detail generation needs it to know where the board's limits are —
    and materials are applied on top. This is the path for "I already modelled
    the shape, make it a PCB".
    """
    outer, holes, method = extract_shape(obj, depsgraph, section_z)
    if len(outer) < 3:
        return (False, "Could not find an outline on that object.")

    problems = geometry.validate_shape(outer, holes)
    if any("self-intersect" in p for p in problems):
        return (False, "; ".join(problems))

    props = obj.pcb_board
    props.is_board = True
    props.source_type = "OBJECT"
    props.source_object = obj

    # The object's own top surface is where detail has to sit.
    if obj.type == "MESH" and obj.data.vertices:
        props.surface_z = max(v.co.z for v in obj.data.vertices)
        props.thickness = max(
            0.1,
            props.surface_z - min(v.co.z for v in obj.data.vertices),
        )
    else:
        props.surface_z = 0.0

    properties.store_outline(obj, outer, holes)
    apply_board_materials(obj, props)

    return (True, f"Adopted board via {method}.")


def board_surface_z(board: bpy.types.Object) -> float:
    """Local Z of the board's top surface."""
    props = getattr(board, "pcb_board", None)
    return getattr(props, "surface_z", 0.0) if props else 0.0


def is_planar(board: bpy.types.Object, tolerance: float = 0.05) -> bool:
    """Whether the board's top surface is flat.

    Non-planar boards get detail projected onto them with a shrinkwrap rather
    than laid on a flat plane.
    """
    if board.type != "MESH" or not board.data.vertices:
        return True

    top = board_surface_z(board)
    near_top = [
        v.co.z for v in board.data.vertices if v.co.z > top - tolerance * 4.0
    ]
    if len(near_top) < 3:
        return True
    return (max(near_top) - min(near_top)) <= tolerance


def outline_signature(
    outer: Sequence[Vec2], holes: Sequence[Sequence[Vec2]]
) -> str:
    """Cheap fingerprint used to notice an outline changed under the parts."""
    import hashlib

    digest = hashlib.sha1()
    for point in outer:
        digest.update(f"{point[0]:.4f},{point[1]:.4f};".encode())
    for hole in holes:
        digest.update(b"|")
        for point in hole:
            digest.update(f"{point[0]:.4f},{point[1]:.4f};".encode())
    return digest.hexdigest()[:16]


def make_rounded_rect(
    width: float, height: float, radius: float = 0.0, segments: int = 8
) -> Ring:
    """A starter outline, for the Add Board operator."""
    radius = max(0.0, min(radius, min(width, height) * 0.5))
    hw, hh = width * 0.5, height * 0.5

    if radius <= 1e-6:
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]

    ring: Ring = []
    corners = (
        (hw - radius, hh - radius, 0.0),
        (-hw + radius, hh - radius, math.pi * 0.5),
        (-hw + radius, -hh + radius, math.pi),
        (hw - radius, -hh + radius, math.pi * 1.5),
    )
    for cx, cy, start in corners:
        for i in range(segments + 1):
            angle = start + (math.pi * 0.5) * (i / segments)
            ring.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return ring
