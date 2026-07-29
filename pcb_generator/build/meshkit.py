"""Mesh construction helpers shared by the board and detail builders."""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

import bmesh
import bpy
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

Vec2 = Tuple[float, float]


def tessellate(
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
) -> Tuple[List[Vec2], List[Tuple[int, int, int]]]:
    """Triangulate a polygon with holes.

    Blender's own tessellator handles multiple contours, which is exactly the
    hole case — worth using rather than hand-rolling ear clipping with hole
    bridging.
    """
    if len(outer) < 3:
        return ([], [])

    contours = [[Vector((p[0], p[1], 0.0)) for p in outer]]
    for hole in holes:
        if len(hole) >= 3:
            contours.append([Vector((p[0], p[1], 0.0)) for p in hole])

    points: List[Vec2] = []
    for contour in contours:
        points.extend((v.x, v.y) for v in contour)

    try:
        triangles = tessellate_polygon(contours)
    except (RuntimeError, ValueError):
        return (points, [])

    return (points, [tuple(t) for t in triangles])


def polygon_mesh(
    name: str,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    z: float = 0.0,
    material_index: int = 0,
) -> Optional[bpy.types.Mesh]:
    """A flat triangulated surface at height ``z``."""
    points, triangles = tessellate(outer, holes)
    if not triangles:
        return None

    mesh = bpy.data.meshes.new(name)
    verts = [(p[0], p[1], z) for p in points]
    mesh.from_pydata(verts, [], [list(t) for t in triangles])
    mesh.update()

    if material_index:
        for polygon in mesh.polygons:
            polygon.material_index = material_index

    return mesh


def solid_board_mesh(
    name: str,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    thickness: float = 1.6,
    top_material: int = 0,
    edge_material: int = 1,
) -> Optional[bpy.types.Mesh]:
    """Build the board solid.

    Local Z is arranged so the top surface sits at ``z = 0`` and the board
    extends downward. Everything placed on the board — parts, traces,
    silkscreen — can then sit at or just above zero without knowing the
    thickness.

    Faces are split by normal into the soldermask-covered top and bottom and the
    bare routed substrate around the edges, because a board's cut edge showing
    raw FR4 is one of the details that reads instantly as wrong when missing.
    """
    points, triangles = tessellate(outer, holes)
    if not triangles:
        return None

    bm = bmesh.new()
    try:
        verts = [bm.verts.new((p[0], p[1], 0.0)) for p in points]
        bm.verts.ensure_lookup_table()

        for tri in triangles:
            try:
                bm.faces.new((verts[tri[0]], verts[tri[1]], verts[tri[2]]))
            except ValueError:
                # Duplicate face from a degenerate contour; harmless.
                continue

        bm.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

        # Top faces must point up before solidifying downward.
        if bm.faces and bm.faces[0].normal.z < 0.0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])

        bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=abs(thickness))

        # solidify's direction convention is not worth relying on. Shift the
        # result so the top surface always lands at local z = 0, whichever way
        # it extruded, and everything downstream can place detail at zero.
        top_z = max((v.co.z for v in bm.verts), default=0.0)
        if abs(top_z) > 1e-9:
            bmesh.ops.translate(bm, vec=Vector((0.0, 0.0, -top_z)), verts=bm.verts[:])

        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        for face in bm.faces:
            face.material_index = (
                top_material if abs(face.normal.z) > 0.5 else edge_material
            )

        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
    finally:
        bm.free()

    mesh.update()
    return mesh


def ribbon_mesh(
    name: str,
    polylines: Iterable[Tuple[Sequence[Vec2], float]],
    z: float = 0.0,
    material_index: int = 0,
) -> Optional[bpy.types.Mesh]:
    """Flat ribbons of given width following each polyline.

    Corners are handled by mitring along the angle bisector, which keeps runs
    continuous through 45-degree turns instead of leaving notches at every bend.
    """
    verts: List[Tuple[float, float, float]] = []
    faces: List[List[int]] = []

    for points, width in polylines:
        if len(points) < 2 or width <= 0.0:
            continue

        half = width * 0.5
        left: List[Vec2] = []
        right: List[Vec2] = []

        for index, point in enumerate(points):
            if index == 0:
                direction = _unit(points[1], points[0])
                normal = (-direction[1], direction[0])
                scale = 1.0
            elif index == len(points) - 1:
                direction = _unit(points[-1], points[-2])
                normal = (-direction[1], direction[0])
                scale = 1.0
            else:
                d0 = _unit(point, points[index - 1])
                d1 = _unit(points[index + 1], point)
                n0 = (-d0[1], d0[0])
                n1 = (-d1[1], d1[0])
                normal = _norm((n0[0] + n1[0], n0[1] + n1[1]))
                if normal is None:
                    normal = n0
                    scale = 1.0
                else:
                    cos_half = normal[0] * n0[0] + normal[1] * n0[1]
                    # Clamp the mitre so hairpins do not spike outward.
                    scale = min(1.0 / cos_half, 3.0) if abs(cos_half) > 1e-3 else 3.0

            offset = (normal[0] * half * scale, normal[1] * half * scale)
            left.append((point[0] + offset[0], point[1] + offset[1]))
            right.append((point[0] - offset[0], point[1] - offset[1]))

        base = len(verts)
        for point in left:
            verts.append((point[0], point[1], z))
        for point in right:
            verts.append((point[0], point[1], z))

        count = len(left)
        for i in range(count - 1):
            a = base + i
            b = base + i + 1
            c = base + count + i + 1
            d = base + count + i
            faces.append([a, b, c, d])

    if not faces:
        return None

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    if material_index:
        for polygon in mesh.polygons:
            polygon.material_index = material_index

    return mesh


def disc_mesh(
    name: str,
    centers: Sequence[Vec2],
    radius: float,
    z: float = 0.0,
    segments: int = 10,
    material_index: int = 0,
    inner_radius: float = 0.0,
) -> Optional[bpy.types.Mesh]:
    """Flat discs or annuli at each centre. Used for vias and round pads."""
    if not centers or radius <= 0.0:
        return None

    ring = [
        (
            math.cos(2.0 * math.pi * i / segments),
            math.sin(2.0 * math.pi * i / segments),
        )
        for i in range(segments)
    ]

    verts: List[Tuple[float, float, float]] = []
    faces: List[List[int]] = []

    for center in centers:
        base = len(verts)
        if inner_radius > 0.0:
            for cos_a, sin_a in ring:
                verts.append(
                    (center[0] + cos_a * radius, center[1] + sin_a * radius, z)
                )
            for cos_a, sin_a in ring:
                verts.append(
                    (
                        center[0] + cos_a * inner_radius,
                        center[1] + sin_a * inner_radius,
                        z,
                    )
                )
            for i in range(segments):
                nxt = (i + 1) % segments
                faces.append(
                    [base + i, base + nxt, base + segments + nxt, base + segments + i]
                )
        else:
            verts.append((center[0], center[1], z))
            for cos_a, sin_a in ring:
                verts.append(
                    (center[0] + cos_a * radius, center[1] + sin_a * radius, z)
                )
            for i in range(segments):
                nxt = (i + 1) % segments
                faces.append([base, base + 1 + i, base + 1 + nxt])

    if not faces:
        return None

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    if material_index:
        for polygon in mesh.polygons:
            polygon.material_index = material_index

    return mesh


def rect_outline_polylines(
    center: Vec2,
    width: float,
    height: float,
    rotation: float = 0.0,
    gap: float = 0.0,
) -> List[Sequence[Vec2]]:
    """Silkscreen-style rectangle around a footprint.

    With ``gap`` above zero the rectangle is broken at the middle of each side,
    which is how real silkscreen outlines are drawn so they do not run over
    adjacent pads.
    """
    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)
    hw, hh = width * 0.5, height * 0.5

    def place(x: float, y: float) -> Vec2:
        return (
            center[0] + x * cos_r - y * sin_r,
            center[1] + x * sin_r + y * cos_r,
        )

    if gap <= 0.0:
        corners = [place(-hw, -hh), place(hw, -hh), place(hw, hh), place(-hw, hh)]
        return [corners + [corners[0]]]

    g = min(gap, min(hw, hh) * 0.8)
    return [
        [place(-hw, -hh), place(-g, -hh)],
        [place(g, -hh), place(hw, -hh)],
        [place(hw, -hh), place(hw, -g)],
        [place(hw, g), place(hw, hh)],
        [place(hw, hh), place(g, hh)],
        [place(-g, hh), place(-hw, hh)],
        [place(-hw, hh), place(-hw, g)],
        [place(-hw, -g), place(-hw, -hh)],
    ]


def _unit(a: Vec2, b: Vec2) -> Vec2:
    dx, dy = a[0] - b[0], a[1] - b[1]
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _norm(v: Vec2) -> Optional[Vec2]:
    length = math.hypot(v[0], v[1])
    if length < 1e-12:
        return None
    return (v[0] / length, v[1] / length)


# --------------------------------------------------------------------------
# Object lifecycle
# --------------------------------------------------------------------------


def replace_object(
    name: str,
    mesh: Optional[bpy.types.Mesh],
    parent: Optional[bpy.types.Object],
    collection: bpy.types.Collection,
    material_names: Sequence[str] = (),
) -> Optional[bpy.types.Object]:
    """Create or replace a generated child object, discarding its old mesh.

    Generated detail is rebuilt constantly. Replacing the mesh datablock rather
    than the object keeps the object's name, selection state and any modifiers
    the user added, and stops the file filling with orphaned meshes.
    """
    existing = bpy.data.objects.get(name)

    if mesh is None:
        if existing is not None:
            remove_object(existing)
        return None

    if existing is None:
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
    else:
        obj = existing
        old = obj.data
        obj.data = mesh
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)
        if obj.name not in collection.objects:
            for other in list(obj.users_collection):
                other.objects.unlink(obj)
            collection.objects.link(obj)

    for material_name in material_names:
        material = bpy.data.materials.get(material_name)
        if material is not None and material.name not in obj.data.materials:
            obj.data.materials.append(material)

    if parent is not None and obj.parent is not parent:
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()

    return obj


def remove_object(obj: Optional[bpy.types.Object]) -> None:
    """Delete an object and its mesh if nothing else uses it."""
    if obj is None:
        return
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if isinstance(data, bpy.types.Mesh) and data.users == 0:
        bpy.data.meshes.remove(data)


def sync_transforms() -> None:
    """Flush pending transform changes so ``matrix_world`` is current.

    Blender does not recompute an object's world matrix when you assign to
    ``location`` or set a parent — it happens on the next depsgraph evaluation.
    Anchor resolution and wire building both read ``matrix_world`` immediately
    after such changes, so they have to force the update or they silently read
    stale identity matrices.
    """
    try:
        bpy.context.view_layer.update()
    except AttributeError:
        pass


def ensure_collection(
    name: str, parent: Optional[bpy.types.Collection] = None
) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    host = parent or bpy.context.scene.collection
    if collection.name not in host.children:
        try:
            host.children.link(collection)
        except RuntimeError:
            pass
    return collection
