"""Board creation operators — the three shape inputs."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator

from ..core import geometry, units
from ..data import properties
from ..build import board as board_build
from ..build import detail as detail_build


def _setup_scene_units(scene: bpy.types.Scene) -> bool:
    """Put the scene in millimetres. Returns True if anything changed.

    Board features are sub-millimetre. A scene left in metres makes every
    default in this addon wrong by a factor of a thousand, so this is fixed up
    rather than merely warned about.
    """
    settings = scene.unit_settings
    changed = False

    if settings.system != "METRIC":
        settings.system = "METRIC"
        changed = True
    if abs(settings.scale_length - units.SCENE_SCALE_LENGTH) > 1e-9:
        settings.scale_length = units.SCENE_SCALE_LENGTH
        changed = True
    if settings.length_unit != "MILLIMETERS":
        try:
            settings.length_unit = "MILLIMETERS"
            changed = True
        except TypeError:
            pass

    return changed


class PCB_OT_setup_scene(Operator):
    bl_idname = "pcb.setup_scene"
    bl_label = "Set Scene to Millimetres"
    bl_description = "Configure scene units so one Blender unit is one millimetre"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        changed = _setup_scene_units(context.scene)
        self.report(
            {"INFO"},
            "Scene set to millimetres." if changed else "Scene already in millimetres.",
        )
        return {"FINISHED"}


class PCB_OT_add_board(Operator):
    """Create a starter board to work from."""

    bl_idname = "pcb.add_board"
    bl_label = "Add Board"
    bl_description = "Create a rectangular board to start from"
    bl_options = {"REGISTER", "UNDO"}

    width: FloatProperty(name="Width", default=40.0, min=1.0, max=1000.0)
    height: FloatProperty(name="Height", default=30.0, min=1.0, max=1000.0)
    corner_radius: FloatProperty(name="Corner Radius", default=2.0, min=0.0, max=100.0)
    thickness: FloatProperty(
        name="Thickness", default=units.DEFAULT_THICKNESS, min=0.1, max=10.0
    )

    def execute(self, context):
        _setup_scene_units(context.scene)

        outline = board_build.make_rounded_rect(
            self.width, self.height, self.corner_radius
        )
        board = board_build.build_board(
            "PCB", outline, [], thickness=self.thickness
        )
        if board is None:
            self.report({"ERROR"}, "Could not build the board mesh.")
            return {"CANCELLED"}

        board.location = context.scene.cursor.location
        board.pcb_board.source_type = "CURVE"
        context.scene.pcb.active_board = board

        _make_active(context, board)
        self.report({"INFO"}, f"Created {board.name}.")
        return {"FINISHED"}


class PCB_OT_board_from_object(Operator):
    """Build a board from another object's shape, or adopt it directly."""

    bl_idname = "pcb.board_from_object"
    bl_label = "Board From Object"
    bl_description = (
        "Extract an outline from the selected object, or turn that object "
        "into the board itself"
    )
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="Mode",
        items=[
            (
                "EXTRACT",
                "Extract Outline",
                "Read the shape and build a new board from it",
            ),
            (
                "ADOPT",
                "Use Object As Board",
                "Keep this object's own geometry and make it a PCB in place",
            ),
        ],
        default="EXTRACT",
    )

    thickness: FloatProperty(
        name="Thickness", default=units.DEFAULT_THICKNESS, min=0.1, max=10.0
    )

    use_section: BoolProperty(
        name="Slice Solid",
        default=False,
        description="For closed solids, cut a cross-section at a given height",
    )
    section_z: FloatProperty(name="Slice Height", default=0.0)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        _setup_scene_units(context.scene)

        source = context.active_object
        depsgraph = context.evaluated_depsgraph_get()
        section_z = self.section_z if self.use_section else None

        if self.mode == "ADOPT":
            ok, message = board_build.adopt_object_as_board(
                source, depsgraph, section_z
            )
            if not ok:
                self.report({"ERROR"}, message)
                return {"CANCELLED"}

            if not board_build.is_planar(source):
                source.pcb_board.shrinkwrap_detail = True

            context.scene.pcb.active_board = source
            self.report({"INFO"}, message)
            return {"FINISHED"}

        outer, holes, method = board_build.extract_shape(source, depsgraph, section_z)
        if len(outer) < 3:
            self.report(
                {"ERROR"},
                "No usable outline found. For a closed solid, enable Slice Solid.",
            )
            return {"CANCELLED"}

        problems = geometry.validate_shape(outer, holes)
        blocking = [p for p in problems if "self-intersect" in p]
        if blocking:
            self.report({"ERROR"}, " ".join(blocking))
            return {"CANCELLED"}

        board = board_build.build_board(
            f"PCB_{source.name}",
            outer,
            holes,
            thickness=self.thickness,
            matrix=source.matrix_world.copy(),
        )
        if board is None:
            self.report({"ERROR"}, "Could not build the board mesh.")
            return {"CANCELLED"}

        board.pcb_board.source_type = "EXTRACTED"
        board.pcb_board.source_object = source
        context.scene.pcb.active_board = board

        _make_active(context, board)

        note = f"Built {board.name} from {method}."
        if problems:
            note += " " + " ".join(problems)
        self.report({"WARNING"} if problems else {"INFO"}, note)
        return {"FINISHED"}


class PCB_OT_rebuild_board(Operator):
    """Rebuild board geometry from its stored outline."""

    bl_idname = "pcb.rebuild_board"
    bl_label = "Rebuild Board"
    bl_description = "Rebuild the board mesh and materials from its stored outline"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        board = properties.find_board(context)
        outer, holes = properties.load_outline(board)

        if len(outer) < 3:
            self.report({"ERROR"}, "This board has no stored outline.")
            return {"CANCELLED"}

        if board.pcb_board.source_type == "OBJECT":
            # Adopted boards keep their own geometry; only materials refresh.
            board_build.apply_board_materials(board, board.pcb_board)
            self.report({"INFO"}, "Refreshed materials on adopted board.")
            return {"FINISHED"}

        rebuilt = board_build.build_board(
            board.name,
            outer,
            holes,
            thickness=board.pcb_board.thickness,
            existing=board,
        )
        if rebuilt is None:
            self.report({"ERROR"}, "Could not rebuild the board mesh.")
            return {"CANCELLED"}

        self.report({"INFO"}, "Board rebuilt.")
        return {"FINISHED"}


class PCB_OT_update_outline(Operator):
    """Re-read the outline from the source object and re-solve everything."""

    bl_idname = "pcb.update_outline"
    bl_label = "Update Outline From Source"
    bl_description = (
        "Re-read the shape from the source object, rebuild the board and "
        "re-solve component anchors"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        board = properties.find_board(context)
        return board is not None and board.pcb_board.source_object is not None

    def execute(self, context):
        from ..build import components as components_build

        board = properties.find_board(context)
        source = board.pcb_board.source_object

        outer, holes, method = board_build.extract_shape(
            source, context.evaluated_depsgraph_get()
        )
        if len(outer) < 3:
            self.report({"ERROR"}, "Could not re-read an outline from the source.")
            return {"CANCELLED"}

        properties.store_outline(board, outer, holes)

        if board.pcb_board.source_type != "OBJECT":
            board_build.build_board(
                board.name,
                outer,
                holes,
                thickness=board.pcb_board.thickness,
                existing=board,
            )

        count, warnings = components_build.resolve_all(board)

        message = f"Outline updated from {method}; {count} component(s) re-solved."
        if warnings:
            self.report({"WARNING"}, message + " " + "; ".join(warnings))
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class PCB_OT_delete_board(Operator):
    """Delete a board and everything generated for it."""

    bl_idname = "pcb.delete_board"
    bl_label = "Delete Board"
    bl_description = "Delete the board along with its detail, parts and wires"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from ..build import components as components_build
        from ..build import meshkit, wires

        board = properties.find_board(context)
        name = board.name

        detail_build.clear_detail(board)
        components_build.clear_scatter(board)

        for obj in properties.board_components(board):
            bpy.data.objects.remove(obj, do_unlink=True)

        wire_collection = bpy.data.collections.get(
            f"{name}{wires.WIRE_COLLECTION_SUFFIX}"
        )
        if wire_collection is not None:
            for obj in list(wire_collection.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.collections.remove(wire_collection)

        if board.pcb_board.source_type == "OBJECT":
            # Adopted boards were the user's geometry; hand it back rather than
            # deleting something they made themselves.
            board.pcb_board.is_board = False
        else:
            meshkit.remove_object(board)

        self.report({"INFO"}, f"Removed {name}.")
        return {"FINISHED"}


def _make_active(context, obj) -> None:
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


CLASSES = (
    PCB_OT_setup_scene,
    PCB_OT_add_board,
    PCB_OT_board_from_object,
    PCB_OT_rebuild_board,
    PCB_OT_update_outline,
    PCB_OT_delete_board,
)
