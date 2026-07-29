"""Sidebar panels.

Laid out in the order the tool is actually used: get a board, give it a look,
place the parts that matter, fill in the rest, wire it up.
"""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..build import components as components_build
from ..core import units
from ..data import properties
from ..parts import library

CATEGORY = "PCB"


class _PCBPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY


def _scene_needs_units(context) -> bool:
    settings = context.scene.unit_settings
    return abs(settings.scale_length - units.SCENE_SCALE_LENGTH) > 1e-9


class PCB_PT_board(_PCBPanel, Panel):
    bl_label = "Board"
    bl_idname = "PCB_PT_board"

    def draw(self, context):
        layout = self.layout
        board = properties.find_board(context)

        if _scene_needs_units(context):
            box = layout.box()
            box.label(text="Scene is not in millimetres", icon="ERROR")
            box.operator("pcb.setup_scene", icon="SETTINGS")

        column = layout.column(align=True)
        column.operator("pcb.add_board", icon="MESH_PLANE")

        row = column.row(align=True)
        op = row.operator("pcb.board_from_object", text="Extract Outline")
        op.mode = "EXTRACT"
        op = row.operator("pcb.board_from_object", text="Use As Board")
        op.mode = "ADOPT"

        if board is None:
            layout.label(text="No board in the scene yet.", icon="INFO")
            return

        layout.separator()
        layout.label(text=board.name, icon="MESH_GRID")

        props = board.pcb_board
        outer, holes = properties.load_outline(board)

        info = layout.box().column(align=True)
        info.label(text=f"Outline: {len(outer)} points, {len(holes)} cutout(s)")
        info.label(text=f"Source: {props.source_type.title()}")

        column = layout.column()
        column.prop(props, "thickness")
        if props.source_type == "OBJECT":
            column.prop(props, "shrinkwrap_detail")

        row = layout.row(align=True)
        row.operator("pcb.rebuild_board", icon="FILE_REFRESH")
        row.operator("pcb.update_outline", text="From Source")

        layout.operator("pcb.delete_board", icon="TRASH")


class PCB_PT_appearance(_PCBPanel, Panel):
    bl_label = "Appearance"
    bl_idname = "PCB_PT_appearance"
    bl_parent_id = "PCB_PT_board"

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def draw(self, context):
        layout = self.layout
        props = properties.find_board(context).pcb_board

        column = layout.column()
        column.prop(props, "mask_color")
        column.prop(props, "finish")
        column.prop(props, "gloss", slider=True)

        layout.operator("pcb.refresh_materials", icon="MATERIAL")


class PCB_PT_detail(_PCBPanel, Panel):
    bl_label = "Detail"
    bl_idname = "PCB_PT_detail"

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def draw(self, context):
        layout = self.layout
        board = properties.find_board(context)
        props = board.pcb_board

        column = layout.column(align=True)
        column.scale_y = 1.3
        column.operator("pcb.populate", icon="SHADERFX")

        row = layout.row(align=True)
        row.operator("pcb.generate_detail", icon="MOD_WIREFRAME")
        row.operator("pcb.reroll_detail", text="", icon="FILE_REFRESH")
        row.operator("pcb.clear_detail", text="", icon="X")

        column = layout.column(align=True)
        column.prop(props, "detail_seed")
        column.prop(props, "trace_density", slider=True)
        column.prop(props, "trace_width")
        column.prop(props, "grid_pitch")
        column.prop(props, "edge_margin")
        column.prop(props, "via_cost")

        if props.grid_pitch <= props.trace_width * 2.0:
            layout.label(
                text="Router pitch should exceed twice the trace width",
                icon="ERROR",
            )

        grid = layout.grid_flow(columns=2, even_columns=True)
        grid.prop(props, "generate_pour")
        grid.prop(props, "generate_silkscreen")
        grid.prop(props, "generate_vias")
        grid.prop(props, "bottom_traces")


class PCB_PT_scatter(_PCBPanel, Panel):
    bl_label = "Scatter"
    bl_idname = "PCB_PT_scatter"

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def draw(self, context):
        layout = self.layout
        board = properties.find_board(context)
        props = board.pcb_board

        row = layout.row(align=True)
        row.operator("pcb.scatter_parts", icon="OUTLINER_OB_POINTCLOUD")
        row.operator("pcb.reroll_scatter", text="", icon="FILE_REFRESH")
        row.operator("pcb.clear_scatter", text="", icon="X")

        column = layout.column(align=True)
        column.prop(props, "scatter_seed")
        column.prop(props, "scatter_density", slider=True)
        column.prop(props, "scatter_clustering", slider=True)
        column.prop(props, "scatter_spacing")
        column.prop(props, "scatter_margin")

        count = len(components_build.scatter_objects(board))
        layout.label(text=f"{count} scattered part(s)")


class PCB_PT_components(_PCBPanel, Panel):
    bl_label = "Components"
    bl_idname = "PCB_PT_components"

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def draw(self, context):
        layout = self.layout
        board = properties.find_board(context)
        scene_props = context.scene.pcb

        layout.prop(scene_props, "add_part_key", text="")

        column = layout.column(align=True)
        column.scale_y = 1.2
        column.operator("pcb.place_component", icon="MOUSE_MOVE")
        column.operator("pcb.add_component", icon="ADD")

        layout.separator()
        layout.prop(scene_props, "add_target", text="Target")
        layout.label(
            text="Bind a part to a button cap or shell opening",
            icon="CON_LOCLIKE",
        )

        layout.separator()
        layout.operator("pcb.resolve_anchors", icon="CON_TRACKTO")
        layout.operator("pcb.select_board_parts", icon="RESTRICT_SELECT_OFF")

        placed = properties.board_components(board)
        layout.label(text=f"{len(placed)} placed component(s)")


class PCB_PT_active_component(_PCBPanel, Panel):
    bl_label = "Active Component"
    bl_idname = "PCB_PT_active_component"
    bl_parent_id = "PCB_PT_components"

    @classmethod
    def poll(cls, context):
        return properties.is_component(context.active_object)

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        comp = obj.pcb_component

        part = library.get(comp.part_key)
        header = layout.box().column(align=True)
        header.label(text=f"{comp.ref} — {part.label if part else comp.part_key}")
        if part:
            header.label(
                text=f"{part.width:.2f} x {part.depth:.2f} x {part.height:.2f} mm"
            )

        column = layout.column()
        column.prop(comp, "ref")
        column.prop(comp, "side")
        column.prop(comp, "anchor")

        if comp.anchor == "EDGE":
            box = layout.box().column(align=True)
            box.prop(comp, "edge_t", slider=True)
            box.prop(comp, "edge_offset")
            box.prop(comp, "align_to_edge")
        elif comp.anchor == "TARGET":
            box = layout.box().column(align=True)
            box.prop(comp, "target")
            box.prop(comp, "target_offset")
            if comp.target is None:
                box.label(text="No target set", icon="ERROR")

        column = layout.column()
        column.prop(comp, "rotation")
        column.prop(comp, "snap_pitch")
        column.prop(comp, "keepout_margin")

        row = layout.row(align=True)
        row.operator("pcb.bind_to_target", icon="CON_LOCLIKE")
        row.operator("pcb.bind_to_edge", icon="SNAP_MIDPOINT")
        layout.operator("pcb.unbind", icon="UNLINKED")


class PCB_PT_wires(_PCBPanel, Panel):
    bl_label = "Wires"
    bl_idname = "PCB_PT_wires"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return properties.find_board(context) is not None

    def draw(self, context):
        layout = self.layout
        scene_props = context.scene.pcb

        layout.operator("pcb.add_wire", icon="IPO_EASE_IN_OUT")
        layout.label(text="Select the far end, then the board end", icon="INFO")

        column = layout.column(align=True)
        column.prop(scene_props, "wire_gauge")
        column.prop(scene_props, "wire_slack", slider=True)
        column.prop(scene_props, "wire_color")

        layout.operator("pcb.update_wires", icon="FILE_REFRESH")


CLASSES = (
    PCB_PT_board,
    PCB_PT_appearance,
    PCB_PT_detail,
    PCB_PT_scatter,
    PCB_PT_components,
    PCB_PT_active_component,
    PCB_PT_wires,
)
