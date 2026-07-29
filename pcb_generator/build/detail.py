"""Procedural board detail: traces, vias, pads, silkscreen and pour.

This is where believability actually comes from. A correctly shaped board with
good materials but no surface detail still reads as a placeholder; the same
board with plausible routing reads as hardware.

Detail is built as separate flat objects sitting just above the board surface
rather than as textures. That means the copper catches light at a slightly
different angle from the mask around it — the subtle relief that makes traces
legible on a real board — and it survives close-ups without a resolution
ceiling. Each layer is a single mesh, so a board with a thousand traces is still
six objects.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import bpy

from ..core import geometry, routing
from ..core.routing import Keepout, RouteConfig
from ..data import properties
from ..shading import materials
from . import board as board_build
from . import meshkit

Vec2 = Tuple[float, float]

# Layer heights above the board surface, in millimetres. Small enough to read as
# a laminate rather than as floating geometry, large enough to avoid z-fighting.
Z_POUR = 0.008
Z_TRACE = 0.016
Z_PAD = 0.024
Z_VIA = 0.030
Z_SILK = 0.038

SUFFIX_TRACES_TOP = "_Traces_Top"
SUFFIX_TRACES_BOTTOM = "_Traces_Bottom"
SUFFIX_VIAS = "_Vias"
SUFFIX_PADS = "_Pads"
SUFFIX_SILK = "_Silkscreen"
SUFFIX_POUR = "_Pour"

ALL_SUFFIXES = (
    SUFFIX_TRACES_TOP,
    SUFFIX_TRACES_BOTTOM,
    SUFFIX_VIAS,
    SUFFIX_PADS,
    SUFFIX_SILK,
    SUFFIX_POUR,
)


@dataclass
class DetailStats:
    traces: int = 0
    vias: int = 0
    pads: int = 0
    silk_marks: int = 0
    routed_fraction: float = 0.0


def detail_object_name(board: bpy.types.Object, suffix: str) -> str:
    return f"{board.name}{suffix}"


def clear_detail(board: bpy.types.Object) -> None:
    for suffix in ALL_SUFFIXES:
        meshkit.remove_object(bpy.data.objects.get(detail_object_name(board, suffix)))


def _apply_projection(obj: Optional[bpy.types.Object], board: bpy.types.Object) -> None:
    """Shrinkwrap flat detail onto a non-flat board."""
    if obj is None:
        return

    wants = board.pcb_board.shrinkwrap_detail
    existing = obj.modifiers.get("PCB Project")

    if not wants:
        if existing is not None:
            obj.modifiers.remove(existing)
        return

    modifier = existing or obj.modifiers.new("PCB Project", "SHRINKWRAP")
    modifier.target = board
    modifier.wrap_method = "PROJECT"
    modifier.use_project_z = True
    modifier.use_negative_direction = True
    modifier.use_positive_direction = True


def generate(board: bpy.types.Object) -> DetailStats:
    """Build every detail layer for a board.

    Keep-outs and pads are read back from the scene rather than passed in, so
    routing always works against what is actually on the board — placed parts
    and scattered filler alike — however they got there.
    """
    from . import components

    props = board.pcb_board
    outer, holes = properties.load_outline(board)
    stats = DetailStats()

    if len(outer) < 3:
        return stats

    surface_z = board_build.board_surface_z(board)
    collection = board_build.board_collection()

    keepouts, pads = components.collect_occupancy(board)

    config = RouteConfig(
        grid_pitch=props.grid_pitch,
        trace_width=props.trace_width,
        clearance=props.trace_width,
        edge_margin=props.edge_margin,
        density=props.trace_density,
        via_cost=props.via_cost,
        seed=props.detail_seed,
    )

    result = routing.generate_traces(outer, holes, keepouts, pads, config)
    stats.traces = len(result.traces)
    stats.routed_fraction = result.success_rate

    materials.build_board_materials(
        mask_color=props.mask_color, finish=props.finish, gloss=props.gloss
    )

    # --- copper pour, underneath everything else ---
    pour_obj = None
    if props.generate_pour:
        pour_outer, pour_holes = routing.pour_outline(
            outer, holes, inset=max(0.3, props.edge_margin * 0.6)
        )
        pour_mesh = meshkit.polygon_mesh(
            detail_object_name(board, SUFFIX_POUR) + "_mesh",
            pour_outer,
            pour_holes,
            z=surface_z + Z_POUR,
        )
        pour_obj = meshkit.replace_object(
            detail_object_name(board, SUFFIX_POUR),
            pour_mesh,
            board,
            collection,
            (materials.MAT_POUR,),
        )
    else:
        meshkit.remove_object(
            bpy.data.objects.get(detail_object_name(board, SUFFIX_POUR))
        )

    # --- traces, split by layer ---
    top_traces = [
        (t.points, t.width) for t in result.traces if t.layer == routing.LAYER_TOP
    ]
    bottom_traces = [
        (t.points, t.width) for t in result.traces if t.layer == routing.LAYER_BOTTOM
    ]

    top_obj = meshkit.replace_object(
        detail_object_name(board, SUFFIX_TRACES_TOP),
        meshkit.ribbon_mesh(
            detail_object_name(board, SUFFIX_TRACES_TOP) + "_mesh",
            top_traces,
            z=surface_z + Z_TRACE,
        ),
        board,
        collection,
        (materials.MAT_TRACE,),
    )

    bottom_obj = None
    if props.bottom_traces and bottom_traces:
        # Underside routing sits below the board, mirrored in Z.
        bottom_obj = meshkit.replace_object(
            detail_object_name(board, SUFFIX_TRACES_BOTTOM),
            meshkit.ribbon_mesh(
                detail_object_name(board, SUFFIX_TRACES_BOTTOM) + "_mesh",
                bottom_traces,
                z=surface_z - props.thickness - Z_TRACE,
            ),
            board,
            collection,
            (materials.MAT_TRACE,),
        )
    else:
        meshkit.remove_object(
            bpy.data.objects.get(detail_object_name(board, SUFFIX_TRACES_BOTTOM))
        )

    # --- vias ---
    via_obj = None
    if props.generate_vias and result.vias:
        via_radius = max(0.14, props.trace_width * 0.85)
        via_obj = meshkit.replace_object(
            detail_object_name(board, SUFFIX_VIAS),
            meshkit.disc_mesh(
                detail_object_name(board, SUFFIX_VIAS) + "_mesh",
                result.vias,
                radius=via_radius,
                z=surface_z + Z_VIA,
                segments=8,
                inner_radius=via_radius * 0.45,
            ),
            board,
            collection,
            (materials.MAT_PAD,),
        )
        stats.vias = len(result.vias)
    else:
        meshkit.remove_object(
            bpy.data.objects.get(detail_object_name(board, SUFFIX_VIAS))
        )

    # --- pads for scattered and placed parts ---
    pad_obj = None
    if pads:
        pad_obj = meshkit.replace_object(
            detail_object_name(board, SUFFIX_PADS),
            meshkit.disc_mesh(
                detail_object_name(board, SUFFIX_PADS) + "_mesh",
                pads,
                radius=max(0.2, props.trace_width * 1.1),
                z=surface_z + Z_PAD,
                segments=8,
            ),
            board,
            collection,
            (materials.MAT_PAD,),
        )
        stats.pads = len(pads)
    else:
        meshkit.remove_object(
            bpy.data.objects.get(detail_object_name(board, SUFFIX_PADS))
        )

    # --- silkscreen ---
    silk_obj = None
    if props.generate_silkscreen:
        silk_lines = _silkscreen_polylines(
            board, outer, holes, keepouts, props
        )
        stats.silk_marks = len(silk_lines)
        silk_obj = meshkit.replace_object(
            detail_object_name(board, SUFFIX_SILK),
            meshkit.ribbon_mesh(
                detail_object_name(board, SUFFIX_SILK) + "_mesh",
                silk_lines,
                z=surface_z + Z_SILK,
            ),
            board,
            collection,
            (materials.MAT_SILKSCREEN,),
        )
    else:
        meshkit.remove_object(
            bpy.data.objects.get(detail_object_name(board, SUFFIX_SILK))
        )

    for obj in (pour_obj, top_obj, via_obj, pad_obj, silk_obj):
        _apply_projection(obj, board)

    props.outline_hash = board_build.outline_signature(outer, holes)
    return stats


def _silkscreen_polylines(
    board: bpy.types.Object,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]],
    keepouts: Sequence[Keepout],
    props,
) -> List[Tuple[Sequence[Vec2], float]]:
    """Component outlines, a board border and a few fiducial marks.

    Silkscreen is mostly component outlines on a real board, so that is most of
    what gets drawn. The board border and corner ticks are cheap and do a lot to
    stop the edge looking bare.
    """
    line_width = max(0.10, props.trace_width * 0.45)
    lines: List[Tuple[Sequence[Vec2], float]] = []

    # Component outlines, broken at the sides the way real silkscreen is.
    for keepout in keepouts:
        # Undo the keep-out margin so the outline hugs the part, not its clearance.
        width = max(0.4, keepout.width - 1.0)
        height = max(0.4, keepout.height - 1.0)
        if width < 1.2 and height < 1.2:
            # Too small to be worth outlining; real boards skip these too.
            continue
        for polyline in meshkit.rect_outline_polylines(
            keepout.center, width, height, keepout.rotation, gap=min(width, height) * 0.22
        ):
            lines.append((polyline, line_width))

    # Board border, pulled in from the edge.
    border = geometry.offset_ring(geometry.as_ccw(outer), -max(0.4, props.edge_margin * 0.4))
    if len(border) >= 3:
        lines.append((list(border) + [border[0]], line_width))

    # Fiducial-style corner ticks.
    min_x, min_y, max_x, max_y = geometry.bounds(outer)
    inset = max(1.5, props.edge_margin * 1.5)
    tick = max(0.8, props.edge_margin)
    for cx, cy, sx, sy in (
        (min_x + inset, min_y + inset, 1.0, 1.0),
        (max_x - inset, min_y + inset, -1.0, 1.0),
        (max_x - inset, max_y - inset, -1.0, -1.0),
        (min_x + inset, max_y - inset, 1.0, -1.0),
    ):
        if not geometry.clearance_ok((cx, cy), outer, holes, margin=0.5):
            continue
        lines.append((((cx, cy), (cx + sx * tick, cy)), line_width))
        lines.append((((cx, cy), (cx, cy + sy * tick)), line_width))

    return lines
