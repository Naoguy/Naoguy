"""Property groups.

Board state lives on the board object itself rather than on the scene, so a
.blend can hold several boards and each remembers how it was generated. The
outline is stored as a flat coordinate list in a custom property, which keeps
regeneration self-contained: nothing has to re-derive the shape from whatever
object it originally came from, and that source object can be deleted.
"""

from __future__ import annotations

import json
from typing import List, Optional, Sequence, Tuple

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Object, PropertyGroup

from ..core import units
from ..shading import materials

Vec2 = Tuple[float, float]

OUTLINE_KEY = "pcb_outline"
HOLES_KEY = "pcb_holes"


MASK_ITEMS = [
    ("GREEN", "Green", "Standard green soldermask"),
    ("BLACK", "Black", "Gloss black soldermask"),
    ("MATTE_BLACK", "Matte Black", "Matte black soldermask"),
    ("BLUE", "Blue", "Blue soldermask"),
    ("RED", "Red", "Red soldermask"),
    ("WHITE", "White", "White soldermask"),
    ("PURPLE", "Purple", "Purple soldermask"),
    ("YELLOW", "Yellow", "Yellow soldermask"),
]

FINISH_ITEMS = [
    ("ENIG", "ENIG (gold)", "Electroless nickel immersion gold"),
    ("HASL", "HASL (tin)", "Hot air solder levelled"),
    ("OSP", "OSP (copper)", "Organic solderability preservative"),
]

SOURCE_ITEMS = [
    ("CURVE", "Curve", "Outline drawn as a curve object"),
    ("EXTRACTED", "Extracted", "Outline pulled from another object's geometry"),
    ("OBJECT", "Object", "An existing object used directly as the board"),
]

ANCHOR_ITEMS = [
    ("FREE", "Free", "Absolute position on the board"),
    ("EDGE", "Edge", "Bound to a position along the board outline"),
    ("TARGET", "Target", "Bound to another object's position"),
]

SIDE_ITEMS = [
    ("TOP", "Top", "Mounted on the top of the board"),
    ("BOTTOM", "Bottom", "Mounted on the underside of the board"),
]


def _tag_redraw(self, context) -> None:
    for area in getattr(context.screen, "areas", ()):
        if area.type == "VIEW_3D":
            area.tag_redraw()


class PCBBoardProperties(PropertyGroup):
    """Everything needed to regenerate a board."""

    is_board: BoolProperty(
        name="Is PCB Board",
        default=False,
        description="Marks this object as a generated board",
    )

    source_type: EnumProperty(
        name="Source", items=SOURCE_ITEMS, default="CURVE"
    )

    source_object: PointerProperty(
        name="Source",
        type=Object,
        description="Object the outline came from. Safe to delete after building",
    )

    thickness: FloatProperty(
        name="Thickness",
        default=units.DEFAULT_THICKNESS,
        min=0.1,
        max=10.0,
        step=10,
        unit="LENGTH",
        description="Board thickness in millimetres",
    )

    surface_z: FloatProperty(
        name="Surface Height",
        default=0.0,
        description=(
            "Local Z of the board's top surface. Zero for generated boards; "
            "adopted objects keep their own height"
        ),
    )

    shrinkwrap_detail: BoolProperty(
        name="Project Detail",
        default=False,
        description=(
            "Project generated detail onto the board surface. Needed when an "
            "adopted board is not flat"
        ),
    )

    # --- appearance ---

    mask_color: EnumProperty(name="Soldermask", items=MASK_ITEMS, default="GREEN")
    finish: EnumProperty(name="Finish", items=FINISH_ITEMS, default="ENIG")
    gloss: FloatProperty(
        name="Gloss", default=0.35, min=0.0, max=1.0,
        description="Matte through to wet-looking soldermask",
    )

    # --- detail generation ---

    detail_seed: IntProperty(
        name="Seed", default=0,
        description="Change for a different layout with the same settings",
    )
    trace_density: FloatProperty(
        name="Trace Density", default=1.0, min=0.0, max=4.0
    )
    trace_width: FloatProperty(
        name="Trace Width", default=0.2, min=0.05, max=2.0, step=1
    )
    grid_pitch: FloatProperty(
        name="Router Pitch", default=0.5, min=0.15, max=3.0, step=1,
        description="Routing grid spacing. Must exceed trace width plus clearance",
    )
    edge_margin: FloatProperty(
        name="Edge Margin", default=1.0, min=0.0, max=10.0, step=10,
        description="Keep traces and parts this far from the board edge",
    )
    via_cost: FloatProperty(
        name="Via Cost", default=7.0, min=0.5, max=100.0,
        description="Higher keeps more routing on the top layer",
    )
    generate_pour: BoolProperty(name="Ground Pour", default=True)
    generate_silkscreen: BoolProperty(name="Silkscreen", default=True)
    generate_vias: BoolProperty(name="Vias", default=True)
    bottom_traces: BoolProperty(
        name="Show Bottom Traces", default=True,
        description="Build geometry for routing that runs on the underside",
    )

    # --- scatter ---

    scatter_seed: IntProperty(name="Scatter Seed", default=0)
    scatter_density: FloatProperty(
        name="Part Density", default=1.0, min=0.0, max=5.0
    )
    scatter_spacing: FloatProperty(
        name="Part Spacing", default=0.6, min=0.0, max=5.0, step=1
    )
    scatter_clustering: FloatProperty(
        name="Clustering", default=0.55, min=0.0, max=1.0,
        description="0 spreads parts evenly, 1 packs them into tight groups",
    )
    scatter_margin: FloatProperty(
        name="Scatter Margin", default=1.5, min=0.0, max=10.0, step=10
    )

    # --- cached state ---

    outline_hash: StringProperty(name="Outline Hash", default="")


class PCBComponentProperties(PropertyGroup):
    """State for a precisely placed component."""

    is_component: BoolProperty(name="Is PCB Component", default=False)

    part_key: StringProperty(name="Part", default="")
    ref: StringProperty(name="Reference", default="")

    board: PointerProperty(
        name="Board", type=Object,
        description="Board this component belongs to",
    )

    side: EnumProperty(name="Side", items=SIDE_ITEMS, default="TOP")

    anchor: EnumProperty(
        name="Anchor", items=ANCHOR_ITEMS, default="FREE", update=_tag_redraw
    )

    edge_t: FloatProperty(
        name="Along Edge", default=0.0, min=0.0, max=1.0, subtype="FACTOR",
        description="Position along the board outline, 0 to 1",
    )
    edge_offset: FloatProperty(
        name="Edge Offset", default=0.0, step=10,
        description="Distance out from the edge. Negative pulls inboard",
    )
    align_to_edge: BoolProperty(name="Align to Edge", default=True)

    target: PointerProperty(
        name="Target", type=Object,
        description="Object this component follows, e.g. a button cap",
    )
    target_offset: FloatVectorProperty(
        name="Target Offset", size=2, default=(0.0, 0.0), subtype="XYZ",
    )

    rotation: FloatProperty(
        name="Rotation", default=0.0, subtype="ANGLE",
    )
    snap_pitch: FloatProperty(
        name="Snap", default=0.0, min=0.0, max=10.0, step=10,
        description="Snap position to this pitch. 0 disables",
    )
    keepout_margin: FloatProperty(
        name="Keep-out", default=0.5, min=0.0, max=10.0, step=10,
        description="Clearance claimed around this part from scatter and routing",
    )


class PCBSceneProperties(PropertyGroup):
    """Scene-level tool state, not part of any board."""

    active_board: PointerProperty(name="Active Board", type=Object)

    add_part_key: StringProperty(name="Part", default="usb_c")

    add_anchor: EnumProperty(name="Anchor", items=ANCHOR_ITEMS, default="FREE")

    add_target: PointerProperty(
        name="Target", type=Object,
        description="Bind the new component to this object's position",
    )

    wire_gauge: FloatProperty(
        name="Gauge", default=0.4, min=0.05, max=5.0, step=1,
        description="Wire diameter including insulation",
    )
    wire_slack: FloatProperty(
        name="Slack", default=0.25, min=0.0, max=2.0,
        description="How much the wire sags between its endpoints",
    )
    wire_color: FloatVectorProperty(
        name="Colour", size=4, subtype="COLOR",
        default=(0.02, 0.02, 0.022, 1.0), min=0.0, max=1.0,
    )


# --------------------------------------------------------------------------
# Outline storage
#
# Stored as JSON in a custom property. Blender's ID property arrays are awkward
# for ragged nested data, and an outline is small enough that the encode cost is
# irrelevant next to the geometry rebuild it feeds.
# --------------------------------------------------------------------------


def store_outline(
    obj: bpy.types.Object,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
) -> None:
    obj[OUTLINE_KEY] = json.dumps([[float(p[0]), float(p[1])] for p in outer])
    obj[HOLES_KEY] = json.dumps(
        [[[float(p[0]), float(p[1])] for p in hole] for hole in holes]
    )


def load_outline(
    obj: Optional[bpy.types.Object],
) -> Tuple[List[Vec2], List[List[Vec2]]]:
    if obj is None:
        return ([], [])

    raw_outer = obj.get(OUTLINE_KEY)
    raw_holes = obj.get(HOLES_KEY)

    try:
        outer = [tuple(p) for p in json.loads(raw_outer)] if raw_outer else []
    except (TypeError, ValueError):
        outer = []

    try:
        holes = (
            [[tuple(p) for p in hole] for hole in json.loads(raw_holes)]
            if raw_holes
            else []
        )
    except (TypeError, ValueError):
        holes = []

    return (outer, holes)


def has_outline(obj: Optional[bpy.types.Object]) -> bool:
    outer, _ = load_outline(obj)
    return len(outer) >= 3


# --------------------------------------------------------------------------
# Lookup helpers
# --------------------------------------------------------------------------


def is_board(obj: Optional[bpy.types.Object]) -> bool:
    return bool(obj and getattr(obj, "pcb_board", None) and obj.pcb_board.is_board)


def is_component(obj: Optional[bpy.types.Object]) -> bool:
    return bool(
        obj and getattr(obj, "pcb_component", None) and obj.pcb_component.is_component
    )


def find_board(context: bpy.types.Context) -> Optional[bpy.types.Object]:
    """The board to act on: the active object, its board, or the scene default."""
    obj = context.active_object

    if is_board(obj):
        return obj
    if is_component(obj) and obj.pcb_component.board:
        return obj.pcb_component.board

    scene_props = getattr(context.scene, "pcb", None)
    if scene_props and is_board(scene_props.active_board):
        return scene_props.active_board

    for candidate in context.scene.objects:
        if is_board(candidate):
            return candidate
    return None


def board_components(
    board: bpy.types.Object,
) -> List[bpy.types.Object]:
    if board is None:
        return []
    return [
        obj
        for obj in bpy.data.objects
        if is_component(obj) and obj.pcb_component.board == board
    ]


def next_ref(board: bpy.types.Object, prefix: str) -> str:
    """Next unused reference designator for a prefix, e.g. J1, J2, J3."""
    used = set()
    for obj in board_components(board):
        ref = obj.pcb_component.ref
        if ref.startswith(prefix):
            suffix = ref[len(prefix):]
            if suffix.isdigit():
                used.add(int(suffix))

    index = 1
    while index in used:
        index += 1
    return f"{prefix}{index}"


CLASSES = (
    PCBBoardProperties,
    PCBComponentProperties,
    PCBSceneProperties,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Object.pcb_board = PointerProperty(type=PCBBoardProperties)
    bpy.types.Object.pcb_component = PointerProperty(type=PCBComponentProperties)
    bpy.types.Scene.pcb = PointerProperty(type=PCBSceneProperties)


def unregister() -> None:
    del bpy.types.Scene.pcb
    del bpy.types.Object.pcb_component
    del bpy.types.Object.pcb_board

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
