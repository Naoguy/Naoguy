"""Component placement operators — the precise-control half of the tool."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector

from ..build import board as board_build
from ..build import components as components_build
from ..core import placement as core_placement
from ..data import properties
from ..parts import library

# Enum item strings must be kept alive by Python or Blender will read freed
# memory. Caching the built list is the standard guard.
_PART_ITEMS_CACHE: List[Tuple[str, str, str]] = []


def _part_items(self, context):
    _PART_ITEMS_CACHE.clear()

    first = True
    for category in library.categories():
        parts = [p for p in library.notable_parts() if p.category == category]
        if not parts:
            continue

        # Separators go *between* categories only. A leading separator becomes
        # item zero, so the enum's default resolves to the empty identifier and
        # the operator fails the moment anyone runs it without opening the
        # dropdown first.
        if not first:
            _PART_ITEMS_CACHE.append(("", category, ""))
        first = False

        for part in parts:
            _PART_ITEMS_CACHE.append(
                (
                    part.key,
                    part.label,
                    part.description
                    or f"{part.width:.1f} x {part.depth:.1f} x {part.height:.1f} mm",
                )
            )

    if not _PART_ITEMS_CACHE:
        _PART_ITEMS_CACHE.append(("NONE", "No parts", ""))

    return _PART_ITEMS_CACHE


def _resolve_part_key(key: str, scene_props) -> str:
    """Fall back to something real if the enum handed us a separator."""
    if key and library.get(key) is not None:
        return key
    if library.get(scene_props.add_part_key) is not None:
        return scene_props.add_part_key
    notable = library.notable_parts()
    return notable[0].key if notable else ""


def _board_plane_point(
    context, board: bpy.types.Object, mouse: Tuple[int, int]
) -> Optional[Vector]:
    """Where the mouse ray meets the board's top surface plane, in board space."""
    region = context.region
    region_3d = context.space_data.region_3d
    if region is None or region_3d is None:
        return None

    origin = view3d_utils.region_2d_to_origin_3d(region, region_3d, mouse)
    direction = view3d_utils.region_2d_to_vector_3d(region, region_3d, mouse)

    to_local = board.matrix_world.inverted()
    local_origin = to_local @ origin
    local_direction = (to_local.to_3x3() @ direction).normalized()

    plane_z = board_build.board_surface_z(board)
    if abs(local_direction.z) < 1e-6:
        return None

    t = (plane_z - local_origin.z) / local_direction.z
    if t < 0.0:
        return None

    return local_origin + local_direction * t


class PCB_OT_add_component(Operator):
    """Add a component to the board."""

    bl_idname = "pcb.add_component"
    bl_label = "Add Component"
    bl_description = "Add a component to the active board"
    bl_options = {"REGISTER", "UNDO"}

    part_key: EnumProperty(name="Part", items=_part_items)
    anchor: EnumProperty(
        name="Anchor",
        items=[
            ("FREE", "Free", "Place at the 3D cursor and leave it there"),
            ("EDGE", "Edge", "Bind to the nearest point on the board outline"),
            ("TARGET", "Target", "Bind to the selected target object's position"),
        ],
        default="FREE",
    )
    side: EnumProperty(
        name="Side",
        items=[("TOP", "Top", ""), ("BOTTOM", "Bottom", "")],
        default="TOP",
    )
    rotation: FloatProperty(name="Rotation", default=0.0, subtype="ANGLE")

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        board = properties.find_board(context)
        scene_props = context.scene.pcb
        part_key = _resolve_part_key(self.part_key, scene_props)

        target = scene_props.add_target if self.anchor == "TARGET" else None
        if self.anchor == "TARGET" and target is None:
            self.report(
                {"ERROR"},
                "Pick a target object first — that is what the component follows.",
            )
            return {"CANCELLED"}

        # Start from the 3D cursor, expressed in board space.
        cursor_local = board.matrix_world.inverted() @ context.scene.cursor.location
        position = (cursor_local.x, cursor_local.y)

        obj = components_build.create_component(
            board,
            part_key,
            position=position,
            rotation=self.rotation,
            anchor=self.anchor,
            target=target,
            side=self.side,
        )
        if obj is None:
            self.report({"ERROR"}, f"Unknown part '{part_key}'.")
            return {"CANCELLED"}

        scene_props.add_part_key = part_key
        _make_active(context, obj)

        self.report({"INFO"}, f"Added {obj.pcb_component.ref} ({part_key}).")
        return {"FINISHED"}


class PCB_OT_place_component(Operator):
    """Place a component interactively.

    Move the mouse to position, click to confirm. The part follows the board
    surface rather than the view plane, so it lands where it looks like it will
    from any angle.
    """

    bl_idname = "pcb.place_component"
    bl_label = "Place Component"
    bl_description = (
        "Place a component by hand. Mouse to move, R to rotate, E to snap to "
        "the board edge, F to flip side, click to confirm"
    )
    bl_options = {"REGISTER", "UNDO"}

    part_key: EnumProperty(name="Part", items=_part_items)

    _obj: Optional[bpy.types.Object] = None
    _board: Optional[bpy.types.Object] = None
    _rotation: float = 0.0
    _edge_snap: bool = False
    _side: str = "TOP"
    _key: str = ""

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and properties.find_board(context) is not None
        )

    def invoke(self, context, event):
        self._board = properties.find_board(context)
        if not properties.has_outline(self._board):
            self.report({"ERROR"}, "The board has no outline to place onto.")
            return {"CANCELLED"}

        self._rotation = 0.0
        self._edge_snap = False
        self._side = "TOP"
        self._key = _resolve_part_key(self.part_key, context.scene.pcb)

        self._obj = components_build.create_component(
            self._board, self._key, position=(0.0, 0.0), anchor="FREE"
        )
        if self._obj is None:
            self.report({"ERROR"}, f"Unknown part '{self._key}'.")
            return {"CANCELLED"}

        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Move: mouse   Confirm: click   Rotate: R   Edge snap: E   "
            "Flip side: F   Cancel: Esc"
        )
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "MOUSEMOVE":
            self._update(context, (event.mouse_region_x, event.mouse_region_y))
            return {"RUNNING_MODAL"}

        if event.type == "R" and event.value == "PRESS":
            step = math.radians(15.0 if event.shift else 90.0)
            self._rotation += step
            self._update(context, (event.mouse_region_x, event.mouse_region_y))
            return {"RUNNING_MODAL"}

        if event.type == "E" and event.value == "PRESS":
            self._edge_snap = not self._edge_snap
            self._update(context, (event.mouse_region_x, event.mouse_region_y))
            return {"RUNNING_MODAL"}

        if event.type == "F" and event.value == "PRESS":
            self._side = "BOTTOM" if self._side == "TOP" else "TOP"
            self._obj.pcb_component.side = self._side
            self._update(context, (event.mouse_region_x, event.mouse_region_y))
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            return self._finish(context, confirmed=True)

        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            return self._finish(context, confirmed=False)

        return {"RUNNING_MODAL"}

    def _update(self, context, mouse) -> None:
        point = _board_plane_point(context, self._board, mouse)
        if point is None:
            return

        comp = self._obj.pcb_component
        comp.rotation = self._rotation
        outer, _ = properties.load_outline(self._board)

        if self._edge_snap and len(outer) >= 3:
            part = library.get(self._key)
            spec = core_placement.bind_to_edge(outer, (point.x, point.y))
            spec.edge_offset = -(part.depth * 0.5 if part else 0.0)
            spec.rotation = self._rotation
            comp.anchor = "EDGE"
            comp.edge_t = spec.edge_t
            comp.edge_offset = spec.edge_offset
            result = core_placement.resolve(spec, outer)
        else:
            comp.anchor = "FREE"
            result = core_placement.Placement(
                position=(point.x, point.y), rotation=self._rotation
            )

        components_build.apply_transform(
            self._obj, self._board, result.position, result.rotation, self._side
        )

    def _finish(self, context, confirmed: bool):
        context.workspace.status_text_set(None)

        if not confirmed:
            components_build.delete_component(self._obj)
            self._obj = None
            return {"CANCELLED"}

        _make_active(context, self._obj)
        self.report({"INFO"}, f"Placed {self._obj.pcb_component.ref}.")
        self._obj = None
        return {"FINISHED"}


class PCB_OT_resolve_anchors(Operator):
    bl_idname = "pcb.resolve_anchors"
    bl_label = "Re-solve Placement"
    bl_description = (
        "Re-solve every anchored component so edge and target bindings catch up "
        "with the current scene"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        board = properties.find_board(context)
        count, warnings = components_build.resolve_all(board)

        if warnings:
            self.report(
                {"WARNING"},
                f"Re-solved {count} component(s). " + "; ".join(warnings),
            )
        else:
            self.report({"INFO"}, f"Re-solved {count} component(s).")
        return {"FINISHED"}


class PCB_OT_bind_to_target(Operator):
    """Bind the active component to another object's position.

    This is the headset case: a tactile switch that has to sit under a plastic
    button cap modelled somewhere else. Select the cap, then the switch, and the
    switch follows it from now on.
    """

    bl_idname = "pcb.bind_to_target"
    bl_label = "Bind To Selected"
    bl_description = (
        "Bind the active component to the other selected object, so it follows "
        "that object's position"
    )
    bl_options = {"REGISTER", "UNDO"}

    keep_offset: BoolProperty(
        name="Keep Offset",
        default=False,
        description="Preserve the current gap instead of snapping onto the target",
    )

    @classmethod
    def poll(cls, context):
        return (
            properties.is_component(context.active_object)
            and len(context.selected_objects) >= 2
        )

    def execute(self, context):
        obj = context.active_object
        others = [o for o in context.selected_objects if o is not obj]
        if not others:
            self.report({"ERROR"}, "Select the target object as well.")
            return {"CANCELLED"}

        target = others[0]
        board = obj.pcb_component.board or properties.find_board(context)
        if board is None:
            self.report({"ERROR"}, "This component is not attached to a board.")
            return {"CANCELLED"}

        comp = obj.pcb_component
        comp.anchor = "TARGET"
        comp.target = target

        if self.keep_offset:
            local = board.matrix_world.inverted() @ target.matrix_world.translation
            comp.target_offset = (
                obj.location.x - local.x,
                obj.location.y - local.y,
            )
        else:
            comp.target_offset = (0.0, 0.0)

        outer, _ = properties.load_outline(board)
        components_build.resolve_component(obj, board, outer)

        self.report({"INFO"}, f"{comp.ref} now follows {target.name}.")
        return {"FINISHED"}


class PCB_OT_bind_to_edge(Operator):
    bl_idname = "pcb.bind_to_edge"
    bl_label = "Bind To Edge"
    bl_description = (
        "Bind the active component to the nearest point on the board outline, "
        "without moving it"
    )
    bl_options = {"REGISTER", "UNDO"}

    align: BoolProperty(name="Align To Edge", default=True)

    @classmethod
    def poll(cls, context):
        return properties.is_component(context.active_object)

    def execute(self, context):
        obj = context.active_object
        board = obj.pcb_component.board or properties.find_board(context)
        outer, _ = properties.load_outline(board) if board else ([], [])

        if len(outer) < 3:
            self.report({"ERROR"}, "The board has no outline to bind to.")
            return {"CANCELLED"}

        spec = core_placement.bind_to_edge(
            outer, (obj.location.x, obj.location.y), align=self.align
        )

        comp = obj.pcb_component
        comp.anchor = "EDGE"
        comp.edge_t = spec.edge_t
        comp.edge_offset = spec.edge_offset
        comp.align_to_edge = self.align

        components_build.resolve_component(obj, board, outer)
        self.report({"INFO"}, f"{comp.ref} bound to the outline.")
        return {"FINISHED"}


class PCB_OT_unbind(Operator):
    bl_idname = "pcb.unbind"
    bl_label = "Unbind"
    bl_description = "Drop the anchor and leave the component where it is"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.is_component(context.active_object)

    def execute(self, context):
        comp = context.active_object.pcb_component
        comp.anchor = "FREE"
        comp.target = None
        self.report({"INFO"}, f"{comp.ref} unbound.")
        return {"FINISHED"}


class PCB_OT_select_board_parts(Operator):
    bl_idname = "pcb.select_board_parts"
    bl_label = "Select Components"
    bl_description = "Select every placed component on the active board"
    bl_options = {"REGISTER", "UNDO"}

    include_scatter: BoolProperty(name="Include Scatter", default=False)

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        board = properties.find_board(context)

        for obj in context.selected_objects:
            obj.select_set(False)

        targets = properties.board_components(board)
        if self.include_scatter:
            targets = targets + components_build.scatter_objects(board)

        for obj in targets:
            obj.select_set(True)

        self.report({"INFO"}, f"Selected {len(targets)} object(s).")
        return {"FINISHED"}


def _make_active(context, obj) -> None:
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


CLASSES = (
    PCB_OT_add_component,
    PCB_OT_place_component,
    PCB_OT_resolve_anchors,
    PCB_OT_bind_to_target,
    PCB_OT_bind_to_edge,
    PCB_OT_unbind,
    PCB_OT_select_board_parts,
)
