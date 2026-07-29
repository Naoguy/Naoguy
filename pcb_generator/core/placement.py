"""Anchor resolution for precisely placed components.

Three anchor modes, covering everything the tool needs to position a part whose
location actually matters:

``FREE``
    Absolute board-space coordinates. What manual placement produces.

``EDGE``
    Bound to the board outline at a normalised perimeter position, oriented to
    the outward normal. Used for connectors that sit on the board's border.
    Addressing by arc length rather than edge index means the anchor survives
    outline edits: inserting a vertex renumbers every subsequent edge but barely
    moves the arc-length parameter of a point elsewhere on the ring.

``TARGET``
    Bound to an external scene object, inheriting XY from it. This is how a
    tactile switch tracks a plastic button cap modelled outside the generator —
    move the cap, the switch follows.

Nothing here imports ``bpy``: the caller reads the target object's position and
passes it in as a plain coordinate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import geometry, units
from .geometry import Vec2
from .routing import Keepout

MODE_FREE = "FREE"
MODE_EDGE = "EDGE"
MODE_TARGET = "TARGET"

MODES = (MODE_FREE, MODE_EDGE, MODE_TARGET)


@dataclass
class AnchorSpec:
    mode: str = MODE_FREE

    position: Vec2 = (0.0, 0.0)
    """Board-space position. Used by FREE, and as the fallback for the others."""

    rotation: float = 0.0
    """Radians. Absolute for FREE and TARGET; an additional offset for EDGE."""

    edge_t: float = 0.0
    """Normalised position along the outline perimeter, wraps at 1.0."""

    edge_offset: float = 0.0
    """Displacement along the outward edge normal. Negative pulls inboard."""

    align_to_edge: bool = True
    """Orient the part to the edge normal rather than using rotation alone."""

    target_position: Optional[Vec2] = None
    """World XY of the bound object, supplied by the caller."""

    target_offset: Vec2 = (0.0, 0.0)
    """Offset applied after inheriting the target's position."""

    snap_pitch: float = 0.0
    """Snap the resolved position to this pitch. 0 disables."""


@dataclass
class Placement:
    position: Vec2
    rotation: float
    resolved: bool = True
    """False when an anchor could not be satisfied and fell back to ``position``."""

    note: str = ""


def resolve(spec: AnchorSpec, outer: Sequence[Vec2] = ()) -> Placement:
    """Resolve an anchor to a concrete board-space position and rotation."""
    if spec.mode == MODE_EDGE:
        placement = _resolve_edge(spec, outer)
    elif spec.mode == MODE_TARGET:
        placement = _resolve_target(spec)
    else:
        placement = Placement(position=tuple(spec.position), rotation=spec.rotation)

    if spec.snap_pitch > 0.0:
        placement.position = units.snap_point(placement.position, spec.snap_pitch)

    return placement


def _resolve_edge(spec: AnchorSpec, outer: Sequence[Vec2]) -> Placement:
    if len(outer) < 3:
        return Placement(
            position=tuple(spec.position),
            rotation=spec.rotation,
            resolved=False,
            note="No board outline to anchor to.",
        )

    point, normal = geometry.point_at_perimeter(outer, spec.edge_t)
    position = (
        point[0] + normal[0] * spec.edge_offset,
        point[1] + normal[1] * spec.edge_offset,
    )

    if spec.align_to_edge:
        rotation = math.atan2(normal[1], normal[0]) + spec.rotation
    else:
        rotation = spec.rotation

    return Placement(position=position, rotation=rotation)


def _resolve_target(spec: AnchorSpec) -> Placement:
    if spec.target_position is None:
        return Placement(
            position=tuple(spec.position),
            rotation=spec.rotation,
            resolved=False,
            note="Target object missing — holding last known position.",
        )

    position = (
        spec.target_position[0] + spec.target_offset[0],
        spec.target_position[1] + spec.target_offset[1],
    )
    return Placement(position=position, rotation=spec.rotation)


def bind_to_edge(
    outer: Sequence[Vec2],
    position: Vec2,
    align: bool = True,
) -> AnchorSpec:
    """Build an EDGE anchor from a position near the outline.

    The offset is derived from how far the position already sits from the
    boundary, so binding an existing part does not move it.
    """
    t = geometry.perimeter_at_point(outer, position)
    point, normal = geometry.point_at_perimeter(outer, t)
    offset = (position[0] - point[0]) * normal[0] + (position[1] - point[1]) * normal[1]
    return AnchorSpec(
        mode=MODE_EDGE,
        position=tuple(position),
        edge_t=t,
        edge_offset=offset,
        align_to_edge=align,
    )


def target_drift(spec: AnchorSpec, current: Vec2) -> float:
    """How far a target-bound part has drifted from where its target wants it.

    Non-zero means the scene moved and placement has not been re-solved. The UI
    surfaces this rather than silently correcting, so a stale board is visible
    instead of quietly wrong.
    """
    if spec.mode != MODE_TARGET or spec.target_position is None:
        return 0.0
    wanted = _resolve_target(spec).position
    return math.dist(wanted, current)


def keepout_for(
    placement: Placement,
    width: float,
    height: float,
    margin: float = 0.0,
) -> Keepout:
    """Keep-out region claimed by a placed part.

    Precisely placed parts register these so scatter and routing work around
    them — the important components claim their space first.
    """
    return Keepout(
        center=placement.position,
        width=width + margin * 2.0,
        height=height + margin * 2.0,
        rotation=placement.rotation,
    )


def distribute_along_edge(
    outer: Sequence[Vec2],
    count: int,
    start_t: float,
    end_t: float,
    offset: float = 0.0,
) -> List[AnchorSpec]:
    """Evenly space ``count`` anchors along a stretch of the outline.

    Handy for connector banks and castellated edges.
    """
    if count <= 0:
        return []
    if count == 1:
        return [
            AnchorSpec(
                mode=MODE_EDGE,
                edge_t=start_t,
                edge_offset=offset,
            )
        ]

    span = end_t - start_t
    return [
        AnchorSpec(
            mode=MODE_EDGE,
            edge_t=start_t + span * (i / (count - 1)),
            edge_offset=offset,
        )
        for i in range(count)
    ]
