"""Detail and scatter generation operators."""

from __future__ import annotations

import random

import bpy
from bpy.types import Operator

from ..build import components as components_build
from ..build import detail as detail_build
from ..data import properties
from ..shading import materials


class PCB_OT_generate_detail(Operator):
    bl_idname = "pcb.generate_detail"
    bl_label = "Generate Detail"
    bl_description = (
        "Generate traces, vias, pads, silkscreen and pour for the active board"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        board = properties.find_board(context)
        return board is not None and properties.has_outline(board)

    def execute(self, context):
        board = properties.find_board(context)

        stats = detail_build.generate(board)
        if stats.traces == 0:
            self.report(
                {"WARNING"},
                "No traces routed. The board may be too small for the current "
                "edge margin and router pitch.",
            )
            return {"FINISHED"}

        self.report(
            {"INFO"},
            f"{stats.traces} trace run(s), {stats.vias} via(s), "
            f"{stats.pads} pad(s), {stats.silk_marks} silkscreen mark(s). "
            f"Routed {stats.routed_fraction * 100:.0f}% of attempts.",
        )
        return {"FINISHED"}


class PCB_OT_clear_detail(Operator):
    bl_idname = "pcb.clear_detail"
    bl_label = "Clear Detail"
    bl_description = "Remove generated traces, vias, pads, silkscreen and pour"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        detail_build.clear_detail(properties.find_board(context))
        self.report({"INFO"}, "Detail cleared.")
        return {"FINISHED"}


class PCB_OT_reroll_detail(Operator):
    """Pick a new seed and regenerate.

    Hunting for a layout you like by nudging one integer is the intended
    workflow, so it gets its own button rather than making people find the seed
    field each time.
    """

    bl_idname = "pcb.reroll_detail"
    bl_label = "Reroll"
    bl_description = "Pick a new random seed and regenerate the detail"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        board = properties.find_board(context)
        return board is not None and properties.has_outline(board)

    def execute(self, context):
        board = properties.find_board(context)
        board.pcb_board.detail_seed = random.randint(0, 99999)
        return bpy.ops.pcb.generate_detail()


class PCB_OT_scatter_parts(Operator):
    bl_idname = "pcb.scatter_parts"
    bl_label = "Scatter Parts"
    bl_description = (
        "Fill the board with filler components, working around anything "
        "already placed"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        board = properties.find_board(context)
        return board is not None and properties.has_outline(board)

    def execute(self, context):
        board = properties.find_board(context)
        count = components_build.generate_scatter(board)

        if count == 0:
            self.report(
                {"WARNING"},
                "Nothing scattered. Try lowering the scatter margin or "
                "raising density.",
            )
        else:
            self.report({"INFO"}, f"Scattered {count} part(s).")
        return {"FINISHED"}


class PCB_OT_clear_scatter(Operator):
    bl_idname = "pcb.clear_scatter"
    bl_label = "Clear Scatter"
    bl_description = "Remove scattered filler components"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        removed = components_build.clear_scatter(properties.find_board(context))
        self.report({"INFO"}, f"Removed {removed} scattered part(s).")
        return {"FINISHED"}


class PCB_OT_reroll_scatter(Operator):
    bl_idname = "pcb.reroll_scatter"
    bl_label = "Reroll Scatter"
    bl_description = "Pick a new random seed and scatter again"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        board = properties.find_board(context)
        return board is not None and properties.has_outline(board)

    def execute(self, context):
        board = properties.find_board(context)
        board.pcb_board.scatter_seed = random.randint(0, 99999)
        return bpy.ops.pcb.scatter_parts()


class PCB_OT_populate(Operator):
    """Scatter and generate detail in one go.

    The ordering matters and is easy to get wrong by hand: filler has to be
    placed before routing, so traces can work around it and terminate on its
    pads instead of running underneath.
    """

    bl_idname = "pcb.populate"
    bl_label = "Populate Board"
    bl_description = "Scatter filler parts, then generate all board detail"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        board = properties.find_board(context)
        return board is not None and properties.has_outline(board)

    def execute(self, context):
        board = properties.find_board(context)

        scattered = components_build.generate_scatter(board)
        stats = detail_build.generate(board)

        self.report(
            {"INFO"},
            f"{scattered} part(s) scattered, {stats.traces} trace run(s), "
            f"{stats.vias} via(s).",
        )
        return {"FINISHED"}


class PCB_OT_refresh_materials(Operator):
    bl_idname = "pcb.refresh_materials"
    bl_label = "Refresh Materials"
    bl_description = "Rebuild board materials from the current colour and finish"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def execute(self, context):
        board = properties.find_board(context)
        props = board.pcb_board

        materials.build_board_materials(
            mask_color=props.mask_color,
            finish=props.finish,
            gloss=props.gloss,
            replace=True,
        )
        materials.build_part_materials(replace=True)

        self.report({"INFO"}, "Materials refreshed.")
        return {"FINISHED"}


CLASSES = (
    PCB_OT_generate_detail,
    PCB_OT_clear_detail,
    PCB_OT_reroll_detail,
    PCB_OT_scatter_parts,
    PCB_OT_clear_scatter,
    PCB_OT_reroll_scatter,
    PCB_OT_populate,
    PCB_OT_refresh_materials,
)
