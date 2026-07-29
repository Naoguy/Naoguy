"""Wire operators."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty
from bpy.types import Operator
from mathutils import Vector

from ..build import wires as wires_build
from ..data import properties


class PCB_OT_add_wire(Operator):
    """Run a wire between two selected objects.

    Select the destination first and the source second, the way Blender's own
    two-object operators work. Endpoints become empties parented to each end, so
    the wire follows when either side moves.
    """

    bl_idname = "pcb.add_wire"
    bl_label = "Add Wire"
    bl_description = (
        "Run a wire between the two selected objects. Select the far end first, "
        "then the board-side end"
    )
    bl_options = {"REGISTER", "UNDO"}

    gauge: FloatProperty(
        name="Gauge", default=0.4, min=0.05, max=5.0,
        description="Wire diameter including insulation, millimetres",
    )
    slack: FloatProperty(
        name="Slack", default=0.25, min=0.0, max=2.0,
        description="How much the wire sags between its endpoints",
    )
    parent_ends: BoolProperty(
        name="Follow Endpoints",
        default=True,
        description="Parent each end to its object so the wire follows it",
    )

    @classmethod
    def poll(cls, context):
        return (
            len(context.selected_objects) >= 2
            and properties.find_board(context) is not None
        )

    def execute(self, context):
        board = properties.find_board(context)

        active = context.active_object
        others = [o for o in context.selected_objects if o is not active]
        if active is None or not others:
            self.report({"ERROR"}, "Select two objects.")
            return {"CANCELLED"}

        far_end = others[0]
        scene_props = context.scene.pcb

        obj = wires_build.create_wire(
            board,
            start_world=active.matrix_world.translation.copy(),
            end_world=far_end.matrix_world.translation.copy(),
            start_parent=active if self.parent_ends else None,
            end_parent=far_end if self.parent_ends else None,
            gauge=self.gauge,
            slack=self.slack,
            color=tuple(scene_props.wire_color),
        )

        self.report({"INFO"}, f"Ran {obj.name} from {active.name} to {far_end.name}.")
        return {"FINISHED"}


class PCB_OT_update_wires(Operator):
    bl_idname = "pcb.update_wires"
    bl_label = "Update Wires"
    bl_description = "Recompute wire shapes from their endpoints"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        count = wires_build.update_all_wires(properties.find_board(context))
        self.report({"INFO"}, f"Updated {count} wire(s).")
        return {"FINISHED"}


CLASSES = (
    PCB_OT_add_wire,
    PCB_OT_update_wires,
)
