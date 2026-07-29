"""Procedural component meshes.

Parts are generated rather than shipped as modelled assets. That keeps the
extension self-contained, makes every part parametric, and means the library
can be extended by adding a function rather than authoring geometry.

Each part is built once into a cached mesh datablock and then shared by every
object that uses it, so scattering four hundred 0402s costs four hundred object
headers and exactly one mesh.

All parts are built in millimetres, centred on the origin in XY, sitting on the
board surface at ``z = 0`` and extending upward. Every part mesh carries the
same four material slots (see :mod:`..shading.materials`) so one assignment path
serves the whole library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import bmesh
import bpy
from mathutils import Vector

from ..shading import materials
from ..shading.materials import (
    SLOT_BODY,
    SLOT_CERAMIC,
    SLOT_LED,
    SLOT_METAL,
    SLOT_PLASTIC,
)

MESH_PREFIX = "PCBPart_"

Vec2 = Tuple[float, float]


# --------------------------------------------------------------------------
# bmesh helpers
# --------------------------------------------------------------------------


def _mark(bm: bmesh.types.BMesh, first_new: int, slot: int) -> None:
    """Assign a material slot to every face added since ``first_new``.

    Tracked by index rather than by comparing against a set of the previous
    faces: BMesh element identity does not survive the table reallocation that
    adding geometry triggers, so a set-membership test silently matches
    everything and leaves the whole part on slot zero.
    """
    bm.faces.ensure_lookup_table()
    for index in range(first_new, len(bm.faces)):
        bm.faces[index].material_index = slot


def _box(
    bm: bmesh.types.BMesh,
    center: Sequence[float],
    size: Sequence[float],
    slot: int = SLOT_BODY,
) -> List[bmesh.types.BMVert]:
    before = len(bm.faces)
    verts = bmesh.ops.create_cube(bm, size=1.0)["verts"]
    bmesh.ops.scale(bm, vec=Vector(size), verts=verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=verts)
    _mark(bm, before, slot)
    return verts


def _cyl(
    bm: bmesh.types.BMesh,
    center: Sequence[float],
    radius: float,
    depth: float,
    slot: int = SLOT_BODY,
    segments: int = 16,
) -> List[bmesh.types.BMVert]:
    before = len(bm.faces)
    verts = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=depth,
    )["verts"]
    bmesh.ops.translate(bm, vec=Vector(center), verts=verts)
    _mark(bm, before, slot)
    return verts


def _bevel_verts(
    bm: bmesh.types.BMesh,
    verts: Sequence[bmesh.types.BMVert],
    offset: float,
    slot: int = SLOT_BODY,
) -> None:
    """Soften corners. Small bevels catch highlights and are most of what makes
    generated parts stop looking like untouched primitives.

    The slot has to be passed in: bevel creates new faces, and they do not
    inherit the material of the faces they were cut from.
    """
    if offset <= 0.0:
        return
    before = len(bm.faces)
    bmesh.ops.bevel(
        bm,
        geom=list(verts),
        offset=offset,
        segments=2,
        profile=0.5,
        affect="VERTICES",
        clamp_overlap=True,
    )
    _mark(bm, before, slot)


def _leg_row(
    bm: bmesh.types.BMesh,
    count: int,
    pitch: float,
    x_offset: float,
    leg_size: Sequence[float],
    z: float,
) -> None:
    """A row of gull-wing style legs, simplified to small boxes."""
    span = (count - 1) * pitch
    for i in range(count):
        y = -span * 0.5 + i * pitch
        _box(bm, (x_offset, y, z), leg_size, SLOT_METAL)


# --------------------------------------------------------------------------
# Part definitions
# --------------------------------------------------------------------------


@dataclass
class PartDef:
    key: str
    label: str
    category: str
    width: float
    """Footprint extent along local X, millimetres."""

    depth: float
    """Footprint extent along local Y, millimetres."""

    height: float
    """How far the part stands off the board."""

    builder: Callable[[bmesh.types.BMesh], None]
    pad_offsets: Tuple[Vec2, ...] = ()
    ref_prefix: str = "U"
    notable: bool = False
    """Notable parts appear in the precise-placement menu; filler parts do not."""

    description: str = ""


_REGISTRY: Dict[str, PartDef] = {}


def register(part: PartDef) -> PartDef:
    _REGISTRY[part.key] = part
    return part


def get(key: str) -> Optional[PartDef]:
    return _REGISTRY.get(key)


def all_parts() -> List[PartDef]:
    return list(_REGISTRY.values())


def notable_parts() -> List[PartDef]:
    return [p for p in _REGISTRY.values() if p.notable]


def filler_parts() -> List[PartDef]:
    return [p for p in _REGISTRY.values() if not p.notable]


# Blender reads enum item strings straight from Python memory, so the built
# list has to be kept alive or the UI shows garbage. Caching it is the standard
# guard, and it doubles as the single source of truth for every part dropdown.
_ENUM_CACHE: Dict[bool, List[Tuple[str, str, str]]] = {}


def enum_items(notable_only: bool = True) -> List[Tuple[str, str, str]]:
    """Parts as Blender enum items, grouped into categories by separators."""
    cached = _ENUM_CACHE.get(notable_only)
    if cached is not None:
        return cached

    items: List[Tuple[str, str, str]] = []
    source = notable_parts() if notable_only else all_parts()

    first = True
    for category in categories():
        parts = [p for p in source if p.category == category]
        if not parts:
            continue

        # Separators go *between* categories only. A leading separator becomes
        # item zero, so the enum's default resolves to an empty identifier and
        # anything reading it without opening the dropdown gets nothing.
        if not first:
            items.append(("", category, ""))
        first = False

        for part in parts:
            items.append(
                (
                    part.key,
                    part.label,
                    part.description
                    or f"{part.width:.1f} x {part.depth:.1f} x {part.height:.1f} mm",
                )
            )

    if not items:
        items.append(("NONE", "No parts", ""))

    _ENUM_CACHE[notable_only] = items
    return items


def categories() -> List[str]:
    seen: List[str] = []
    for part in _REGISTRY.values():
        if part.category not in seen:
            seen.append(part.category)
    return seen


# --------------------------------------------------------------------------
# Passives
# --------------------------------------------------------------------------


def _chip_passive(length: float, width: float, height: float, body_slot: int):
    """Two-terminal chip package: a body with plated end caps."""

    def build(bm: bmesh.types.BMesh) -> None:
        cap = length * 0.24
        _box(bm, (0.0, 0.0, height * 0.5), (length - cap, width, height), body_slot)
        for sign in (-1.0, 1.0):
            _box(
                bm,
                (sign * (length - cap) * 0.5, 0.0, height * 0.5),
                (cap, width * 1.02, height * 1.02),
                SLOT_METAL,
            )

    return build


def _chip_pads(length: float) -> Tuple[Vec2, ...]:
    return ((-length * 0.45, 0.0), (length * 0.45, 0.0))


register(PartDef("passive_0402", "0402 chip", "Passives", 1.0, 0.5, 0.35,
                 _chip_passive(1.0, 0.5, 0.35, SLOT_BODY),
                 _chip_pads(1.0), ref_prefix="R"))
register(PartDef("passive_0603", "0603 chip", "Passives", 1.6, 0.8, 0.45,
                 _chip_passive(1.6, 0.8, 0.45, SLOT_BODY),
                 _chip_pads(1.6), ref_prefix="R"))
register(PartDef("passive_0805", "0805 chip", "Passives", 2.0, 1.25, 0.55,
                 _chip_passive(2.0, 1.25, 0.55, SLOT_CERAMIC),
                 _chip_pads(2.0), ref_prefix="C"))
register(PartDef("passive_1206", "1206 chip", "Passives", 3.2, 1.6, 0.65,
                 _chip_passive(3.2, 1.6, 0.65, SLOT_CERAMIC),
                 _chip_pads(3.2), ref_prefix="C"))
register(PartDef("tantalum_cap", "Tantalum cap", "Passives", 3.2, 1.6, 1.6,
                 _chip_passive(3.2, 1.6, 1.6, SLOT_BODY),
                 _chip_pads(3.2), ref_prefix="C"))


def _led_smd(bm: bmesh.types.BMesh) -> None:
    _box(bm, (0.0, 0.0, 0.25), (1.2, 0.8, 0.5), SLOT_CERAMIC)
    for sign in (-1.0, 1.0):
        _box(bm, (sign * 0.7, 0.0, 0.22), (0.35, 0.82, 0.44), SLOT_METAL)
    _box(bm, (0.0, 0.0, 0.52), (0.9, 0.6, 0.12), SLOT_LED)


register(PartDef("led_0603", "0603 LED", "Passives", 1.6, 0.8, 0.65, _led_smd,
                 _chip_pads(1.6), ref_prefix="D"))


def _inductor_smd(bm: bmesh.types.BMesh) -> None:
    verts = _box(bm, (0.0, 0.0, 0.6), (2.5, 2.0, 1.2), SLOT_BODY)
    _bevel_verts(bm, verts, 0.18)


register(PartDef("inductor_smd", "SMD inductor", "Passives", 2.5, 2.0, 1.2,
                 _inductor_smd, ref_prefix="L"))


def _electrolytic_cap(bm: bmesh.types.BMesh) -> None:
    _cyl(bm, (0.0, 0.0, 0.3), 2.5, 0.6, SLOT_PLASTIC, segments=20)
    _cyl(bm, (0.0, 0.0, 2.9), 2.4, 4.6, SLOT_METAL, segments=20)
    # Vent cross on the can top.
    _box(bm, (0.0, 0.0, 5.2), (4.0, 0.35, 0.08), SLOT_BODY)
    _box(bm, (0.0, 0.0, 5.2), (0.35, 4.0, 0.08), SLOT_BODY)


register(PartDef("electrolytic_cap", "Electrolytic cap", "Passives", 5.0, 5.0, 5.3,
                 _electrolytic_cap, ref_prefix="C"))


def _crystal_smd(bm: bmesh.types.BMesh) -> None:
    _box(bm, (0.0, 0.0, 0.15), (3.2, 2.5, 0.3), SLOT_PLASTIC)
    verts = _box(bm, (0.0, 0.0, 0.55), (2.9, 2.2, 0.55), SLOT_METAL)
    _bevel_verts(bm, verts, 0.12, SLOT_METAL)


register(PartDef("crystal_smd", "SMD crystal", "Passives", 3.2, 2.5, 0.85,
                 _crystal_smd, ref_prefix="Y"))


def _test_point(bm: bmesh.types.BMesh) -> None:
    _cyl(bm, (0.0, 0.0, 0.05), 0.6, 0.1, SLOT_METAL, segments=12)


register(PartDef("test_point", "Test point", "Passives", 1.2, 1.2, 0.1,
                 _test_point, ref_prefix="TP"))


# --------------------------------------------------------------------------
# Semiconductors
# --------------------------------------------------------------------------


def _sot23(bm: bmesh.types.BMesh) -> None:
    _box(bm, (0.0, 0.0, 0.55), (1.3, 2.9, 1.1), SLOT_BODY)
    for y in (-0.95, 0.95):
        _box(bm, (-0.95, y, 0.1), (0.6, 0.4, 0.15), SLOT_METAL)
    _box(bm, (0.95, 0.0, 0.1), (0.6, 0.4, 0.15), SLOT_METAL)


register(PartDef("sot23", "SOT-23", "Semiconductors", 2.9, 1.3, 1.25, _sot23,
                 ((-0.5, -0.95), (-0.5, 0.95), (0.5, 0.0)), ref_prefix="Q"))


def _soic(pins: int, body_len: float):
    per_side = pins // 2

    def build(bm: bmesh.types.BMesh) -> None:
        _box(bm, (0.0, 0.0, 0.85), (body_len, 3.9, 1.4), SLOT_BODY)
        for x in (-body_len * 0.5 - 0.45, body_len * 0.5 + 0.45):
            _leg_row(bm, per_side, 1.27, x, (0.9, 0.45, 0.2), 0.12)
        # Pin-1 dimple.
        _cyl(
            bm,
            (-body_len * 0.5 + 0.6, 3.9 * 0.5 - 0.6, 1.5),
            0.28,
            0.12,
            SLOT_PLASTIC,
            segments=10,
        )

    return build


register(PartDef("soic8", "SOIC-8", "Semiconductors", 4.9, 6.0, 1.6,
                 _soic(8, 3.9), ref_prefix="U"))
register(PartDef("soic16", "SOIC-16", "Semiconductors", 10.0, 6.0, 1.6,
                 _soic(16, 9.9), ref_prefix="U"))


def _qfn(pins: int, size: float):
    per_side = pins // 4

    def build(bm: bmesh.types.BMesh) -> None:
        verts = _box(bm, (0.0, 0.0, 0.45), (size, size, 0.9), SLOT_BODY)
        _bevel_verts(bm, verts, 0.1)
        # Exposed pad and perimeter terminals, visible as a thin metal fringe.
        _box(bm, (0.0, 0.0, 0.03), (size * 0.55, size * 0.55, 0.06), SLOT_METAL)
        pitch = size / (per_side + 1)
        span = (per_side - 1) * pitch
        for i in range(per_side):
            offset = -span * 0.5 + i * pitch
            for center, dims in (
                ((offset, size * 0.5 - 0.15, 0.03), (0.25, 0.4, 0.06)),
                ((offset, -size * 0.5 + 0.15, 0.03), (0.25, 0.4, 0.06)),
                ((size * 0.5 - 0.15, offset, 0.03), (0.4, 0.25, 0.06)),
                ((-size * 0.5 + 0.15, offset, 0.03), (0.4, 0.25, 0.06)),
            ):
                _box(bm, center, dims, SLOT_METAL)
        _cyl(bm, (-size * 0.32, size * 0.32, 0.9), 0.25, 0.1, SLOT_PLASTIC, segments=10)

    return build


register(PartDef("qfn16", "QFN-16", "Semiconductors", 4.0, 4.0, 1.0,
                 _qfn(16, 4.0), ref_prefix="U"))
register(PartDef("qfn32", "QFN-32", "Semiconductors", 5.0, 5.0, 1.0,
                 _qfn(32, 5.0), ref_prefix="U"))


def _qfp(pins: int, body: float):
    per_side = pins // 4
    pitch = 0.5

    def build(bm: bmesh.types.BMesh) -> None:
        verts = _box(bm, (0.0, 0.0, 0.8), (body, body, 1.4), SLOT_BODY)
        _bevel_verts(bm, verts, 0.15)
        span = (per_side - 1) * pitch
        for i in range(per_side):
            offset = -span * 0.5 + i * pitch
            _box(bm, (offset, body * 0.5 + 0.5, 0.12), (0.25, 1.0, 0.18), SLOT_METAL)
            _box(bm, (offset, -body * 0.5 - 0.5, 0.12), (0.25, 1.0, 0.18), SLOT_METAL)
            _box(bm, (body * 0.5 + 0.5, offset, 0.12), (1.0, 0.25, 0.18), SLOT_METAL)
            _box(bm, (-body * 0.5 - 0.5, offset, 0.12), (1.0, 0.25, 0.18), SLOT_METAL)
        _cyl(bm, (-body * 0.35, body * 0.35, 1.5), 0.3, 0.12, SLOT_PLASTIC, segments=10)

    return build


register(PartDef("qfp32", "QFP-32", "Semiconductors", 9.0, 9.0, 1.6,
                 _qfp(32, 7.0), ref_prefix="U"))


# --------------------------------------------------------------------------
# Connectors and ports — the notable parts
# --------------------------------------------------------------------------


def _usb_c(bm: bmesh.types.BMesh) -> None:
    width, depth, height = 8.94, 7.35, 3.26
    shell = _box(bm, (0.0, 0.0, height * 0.5), (width, depth, height), SLOT_METAL)
    # The rounded ends are the whole visual signature of a USB-C port.
    _bevel_verts(bm, shell, 1.5, SLOT_METAL)
    # Mouth and tongue, recessed into the front face (-Y is outward).
    _box(bm, (0.0, -depth * 0.5 + 0.6, height * 0.5), (6.9, 1.4, 1.9), SLOT_BODY)
    _box(bm, (0.0, -depth * 0.5 + 0.9, height * 0.5), (6.3, 1.0, 0.62), SLOT_PLASTIC)
    for sign in (-1.0, 1.0):
        _box(bm, (sign * (width * 0.5 - 0.3), depth * 0.4, 0.35),
             (0.9, 1.6, 0.7), SLOT_METAL)


register(PartDef("usb_c", "USB-C receptacle", "Connectors", 8.94, 7.35, 3.26,
                 _usb_c, ref_prefix="J", notable=True,
                 description="Front face points along -Y."))


def _usb_a(bm: bmesh.types.BMesh) -> None:
    width, depth, height = 13.1, 14.5, 6.5
    _box(bm, (0.0, 0.0, height * 0.5), (width, depth, height), SLOT_METAL)
    _box(bm, (0.0, -depth * 0.5 + 1.0, height * 0.5), (11.8, 2.2, 5.2), SLOT_BODY)
    _box(bm, (0.0, -depth * 0.5 + 1.4, height * 0.5 - 1.1), (10.8, 1.6, 1.9), SLOT_PLASTIC)


register(PartDef("usb_a", "USB-A receptacle", "Connectors", 13.1, 14.5, 6.5,
                 _usb_a, ref_prefix="J", notable=True))


def _micro_usb(bm: bmesh.types.BMesh) -> None:
    width, depth, height = 7.5, 5.0, 2.6
    shell = _box(bm, (0.0, 0.0, height * 0.5), (width, depth, height), SLOT_METAL)
    _bevel_verts(bm, shell, 0.4, SLOT_METAL)
    _box(bm, (0.0, -depth * 0.5 + 0.5, height * 0.5), (6.4, 1.2, 1.6), SLOT_BODY)


register(PartDef("micro_usb", "Micro-USB", "Connectors", 7.5, 5.0, 2.6,
                 _micro_usb, ref_prefix="J", notable=True))


def _jst(pins: int, pitch: float, height: float):
    width = pins * pitch + 1.8

    def build(bm: bmesh.types.BMesh) -> None:
        _box(bm, (0.0, 0.0, height * 0.5), (width, 4.5, height), SLOT_PLASTIC)
        _box(bm, (0.0, -1.6, height * 0.6), (width - 1.6, 1.6, height * 0.8), SLOT_BODY)
        span = (pins - 1) * pitch
        for i in range(pins):
            _box(bm, (-span * 0.5 + i * pitch, 1.9, 0.1),
                 (0.4, 1.2, 0.2), SLOT_METAL)

    return build


register(PartDef("jst_ph_2", "JST-PH 2-pin", "Connectors", 5.8, 4.5, 6.0,
                 _jst(2, 2.0, 6.0), ref_prefix="J", notable=True))
register(PartDef("jst_sh_4", "JST-SH 4-pin", "Connectors", 5.8, 4.5, 3.0,
                 _jst(4, 1.0, 3.0), ref_prefix="J", notable=True))


def _fpc_connector(pins: int, pitch: float):
    width = pins * pitch + 3.0

    def build(bm: bmesh.types.BMesh) -> None:
        _box(bm, (0.0, 0.0, 0.5), (width, 5.0, 1.0), SLOT_BODY)
        # Flip-up actuator, drawn closed.
        _box(bm, (0.0, 1.8, 1.15), (width, 1.4, 0.3), SLOT_PLASTIC)
        span = (pins - 1) * pitch
        for i in range(pins):
            _box(bm, (-span * 0.5 + i * pitch, -2.2, 0.08),
                 (pitch * 0.5, 1.0, 0.16), SLOT_METAL)

    return build


register(PartDef("fpc_10", "FPC/ZIF 10-pin", "Connectors", 8.0, 5.0, 1.5,
                 _fpc_connector(10, 0.5), ref_prefix="J", notable=True,
                 description="Ribbon enters along -Y."))
register(PartDef("fpc_24", "FPC/ZIF 24-pin", "Connectors", 15.0, 5.0, 1.5,
                 _fpc_connector(24, 0.5), ref_prefix="J", notable=True))


def _pin_header(rows: int, cols: int, pitch: float = 2.54):
    base_h = 2.5
    pin_h = 11.5

    def build(bm: bmesh.types.BMesh) -> None:
        span_x = (cols - 1) * pitch
        span_y = (rows - 1) * pitch
        _box(
            bm,
            (0.0, 0.0, base_h * 0.5),
            (span_x + pitch, span_y + pitch, base_h),
            SLOT_BODY,
        )
        for r in range(rows):
            for c in range(cols):
                x = -span_x * 0.5 + c * pitch
                y = -span_y * 0.5 + r * pitch
                _box(bm, (x, y, pin_h * 0.5 - 2.5), (0.64, 0.64, pin_h), SLOT_METAL)

    return build


register(PartDef("header_1x4", "Header 1x4 (2.54)", "Connectors",
                 4 * 2.54, 2.54, 9.0, _pin_header(1, 4), ref_prefix="J", notable=True))
register(PartDef("header_1x8", "Header 1x8 (2.54)", "Connectors",
                 8 * 2.54, 2.54, 9.0, _pin_header(1, 8), ref_prefix="J", notable=True))
register(PartDef("header_2x5", "Header 2x5 (2.54)", "Connectors",
                 5 * 2.54, 2 * 2.54, 9.0, _pin_header(2, 5), ref_prefix="J", notable=True))


# --------------------------------------------------------------------------
# Controls — the parts that have to line up with an enclosure
# --------------------------------------------------------------------------


def _tactile_switch(size: float, height: float, button_h: float):
    def build(bm: bmesh.types.BMesh) -> None:
        body_h = height - button_h
        _box(bm, (0.0, 0.0, body_h * 0.5), (size, size, body_h), SLOT_BODY)
        _box(bm, (0.0, 0.0, body_h - 0.12), (size * 0.85, size * 0.85, 0.24), SLOT_METAL)
        _cyl(bm, (0.0, 0.0, body_h + button_h * 0.5), size * 0.28, button_h,
             SLOT_PLASTIC, segments=16)
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                _box(bm, (sx * size * 0.55, sy * size * 0.3, 0.1),
                     (0.7, 0.6, 0.2), SLOT_METAL)

    return build


for _key, _label, _size, _height, _btn in (
    ("tact_6mm_43", "Tactile 6mm (4.3 tall)", 6.0, 4.3, 0.8),
    ("tact_6mm_50", "Tactile 6mm (5.0 tall)", 6.0, 5.0, 1.5),
    ("tact_6mm_70", "Tactile 6mm (7.0 tall)", 6.0, 7.0, 3.5),
    ("tact_smd_35", "Tactile SMD 3.5mm", 3.5, 2.0, 0.55),
):
    register(PartDef(_key, _label, "Controls", _size, _size, _height,
                     _tactile_switch(_size, _height, _btn), ref_prefix="SW",
                     notable=True,
                     description="Button axis is the part origin — bind this to the cap."))


def _slide_switch(bm: bmesh.types.BMesh) -> None:
    _box(bm, (0.0, 0.0, 1.2), (9.0, 3.5, 2.4), SLOT_PLASTIC)
    _box(bm, (-1.2, 0.0, 2.9), (2.0, 1.4, 1.4), SLOT_BODY)
    for x in (-3.5, 0.0, 3.5):
        _box(bm, (x, -2.4, 0.1), (0.8, 1.6, 0.2), SLOT_METAL)


register(PartDef("slide_switch", "Slide switch", "Controls", 9.0, 3.5, 3.6,
                 _slide_switch, ref_prefix="SW", notable=True))


def _rotary_encoder(bm: bmesh.types.BMesh) -> None:
    _box(bm, (0.0, 0.0, 3.5), (12.0, 12.0, 7.0), SLOT_METAL)
    _cyl(bm, (0.0, 0.0, 10.5), 3.0, 7.0, SLOT_METAL, segments=20)
    _cyl(bm, (0.0, 0.0, 14.2), 2.9, 0.4, SLOT_BODY, segments=20)


register(PartDef("rotary_encoder", "Rotary encoder", "Controls", 12.0, 12.0, 14.5,
                 _rotary_encoder, ref_prefix="SW", notable=True))


def _led_3mm(bm: bmesh.types.BMesh) -> None:
    _cyl(bm, (0.0, 0.0, 0.6), 1.6, 1.2, SLOT_LED, segments=20)
    _cyl(bm, (0.0, 0.0, 2.6), 1.5, 2.8, SLOT_LED, segments=20)
    for sign in (-1.0, 1.0):
        _box(bm, (sign * 0.6, 0.0, -0.5), (0.5, 0.5, 1.0), SLOT_METAL)


register(PartDef("led_3mm", "LED 3mm", "Controls", 3.2, 3.2, 4.2, _led_3mm,
                 ref_prefix="D", notable=True))


# --------------------------------------------------------------------------
# Mechanical
# --------------------------------------------------------------------------


def _mounting_pad(radius: float):
    def build(bm: bmesh.types.BMesh) -> None:
        # An annulus of exposed plating. The hole itself is a board cutout.
        _cyl(bm, (0.0, 0.0, 0.02), radius, 0.04, SLOT_METAL, segments=24)

    return build


register(PartDef("mount_m2", "M2 mounting pad", "Mechanical", 4.0, 4.0, 0.05,
                 _mounting_pad(2.0), ref_prefix="H", notable=True))
register(PartDef("mount_m25", "M2.5 mounting pad", "Mechanical", 5.0, 5.0, 0.05,
                 _mounting_pad(2.5), ref_prefix="H", notable=True))
register(PartDef("mount_m3", "M3 mounting pad", "Mechanical", 6.0, 6.0, 0.05,
                 _mounting_pad(3.0), ref_prefix="H", notable=True))


def _standoff(bm: bmesh.types.BMesh) -> None:
    _cyl(bm, (0.0, 0.0, 2.5), 2.0, 5.0, SLOT_METAL, segments=16)
    _cyl(bm, (0.0, 0.0, 2.5), 1.0, 5.2, SLOT_BODY, segments=12)


register(PartDef("standoff_5mm", "Standoff 5mm", "Mechanical", 4.0, 4.0, 5.0,
                 _standoff, ref_prefix="H", notable=True))


# --------------------------------------------------------------------------
# Mesh construction and caching
# --------------------------------------------------------------------------


def mesh_name(key: str) -> str:
    return MESH_PREFIX + key


def build_mesh(key: str, rebuild: bool = False) -> Optional[bpy.types.Mesh]:
    """Fetch the cached mesh for a part, building it on first use."""
    part = get(key)
    if part is None:
        return None

    name = mesh_name(key)
    existing = bpy.data.meshes.get(name)
    if existing is not None and not rebuild:
        return existing

    mesh = existing if existing is not None else bpy.data.meshes.new(name)
    if existing is not None:
        mesh.clear_geometry()

    # Slots must exist before the geometry arrives. Clearing material slots
    # resets every face's material_index to zero, so doing it afterwards throws
    # away everything the builders assigned and leaves the part single-material.
    mesh.materials.clear()
    for material_name in materials.PART_SLOTS:
        mesh.materials.append(bpy.data.materials.get(material_name))

    bm = bmesh.new()
    try:
        part.builder(bm)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
    finally:
        bm.free()

    # Left faceted deliberately: small hardware has crisp machined edges, and
    # smooth shading on a 1mm resistor just looks soft.
    return mesh


def clear_cached_meshes() -> int:
    """Drop cached part meshes that nothing is using."""
    removed = 0
    for mesh in list(bpy.data.meshes):
        if mesh.name.startswith(MESH_PREFIX) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


def scatter_specs() -> List:
    """Filler parts as scatter specs, with weights favouring small passives.

    Real boards are mostly 0402s and 0603s. Getting that ratio right matters
    more to the look than any individual part's detail.
    """
    from ..core.scatter import PartSpec

    weights = {
        "passive_0402": 6.0,
        "passive_0603": 5.0,
        "passive_0805": 2.5,
        "passive_1206": 1.0,
        "led_0603": 0.6,
        "sot23": 1.2,
        "soic8": 0.5,
        "soic16": 0.2,
        "qfn16": 0.35,
        "qfn32": 0.15,
        "qfp32": 0.1,
        "crystal_smd": 0.3,
        "inductor_smd": 0.4,
        "tantalum_cap": 0.4,
        "electrolytic_cap": 0.15,
        "test_point": 0.5,
    }

    specs = []
    for key, weight in weights.items():
        part = get(key)
        if part is None:
            continue
        specs.append(
            PartSpec(
                key=key,
                width=part.width,
                height=part.depth,
                weight=weight,
                pad_offsets=part.pad_offsets,
            )
        )
    return specs
