"""Component scatter.

Fills the board with plausible small hardware. This is decoration and treats
itself as such — fast to regenerate, easy to reroll, no bookkeeping.

The difference between "scattered parts" and "confetti" is entirely in the
rules: rotations snap to 90 degrees, placements cluster into functional-looking
groups rather than spreading uniformly, and everything respects margins and
keep-outs. Uniform random placement looks obviously wrong on a PCB, so it is
never used.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import geometry
from .geometry import Vec2
from .routing import Keepout


@dataclass
class PartSpec:
    """A part the scatter may place, with its footprint and relative frequency."""

    key: str
    width: float
    height: float
    weight: float = 1.0
    """Relative likelihood of being chosen."""

    pad_offsets: Tuple[Vec2, ...] = ()
    """Pad positions relative to the part origin, unrotated."""


@dataclass
class ScatterItem:
    key: str
    position: Vec2
    rotation: float
    width: float
    height: float

    def pads(self, spec: PartSpec) -> List[Vec2]:
        """World-space pad positions for this placement."""
        cos_r = math.cos(self.rotation)
        sin_r = math.sin(self.rotation)
        out = []
        for ox, oy in spec.pad_offsets:
            out.append(
                (
                    self.position[0] + ox * cos_r - oy * sin_r,
                    self.position[1] + ox * sin_r + oy * cos_r,
                )
            )
        return out

    def radius(self) -> float:
        return math.hypot(self.width, self.height) * 0.5


@dataclass
class ScatterConfig:
    density: float = 1.0
    """Scales the target count, relative to board area."""

    edge_margin: float = 1.5
    """Keep parts this far in from the board edge and any cutout."""

    spacing: float = 0.6
    """Minimum gap between part footprints."""

    cluster_count: int = 0
    """Functional-looking groups. 0 derives a count from board area."""

    cluster_tightness: float = 0.55
    """0 spreads parts across the whole board, 1 packs them onto cluster centres."""

    align_jitter: float = 0.0
    """Degrees of rotation wobble added after snapping to 90. Keep small."""

    max_attempts_per_item: int = 24
    seed: int = 0


@dataclass
class ScatterResult:
    items: List[ScatterItem] = field(default_factory=list)
    pads: List[Vec2] = field(default_factory=list)

    def keepouts(self, margin: float = 0.0) -> List[Keepout]:
        return [
            Keepout(
                center=item.position,
                width=item.width + margin * 2.0,
                height=item.height + margin * 2.0,
                rotation=item.rotation,
            )
            for item in self.items
        ]


def _weighted_choice(rng: random.Random, specs: Sequence[PartSpec]) -> PartSpec:
    total = sum(max(s.weight, 0.0) for s in specs)
    if total <= 0.0:
        return rng.choice(list(specs))
    target = rng.random() * total
    upto = 0.0
    for spec in specs:
        upto += max(spec.weight, 0.0)
        if upto >= target:
            return spec
    return specs[-1]


def _footprint_clear(
    candidate: ScatterItem,
    placed: Sequence[ScatterItem],
    spacing: float,
) -> bool:
    """Bounding-circle rejection.

    Deliberately conservative: circles overestimate rectangular footprints, so
    parts end up slightly further apart than strictly necessary. On a render
    that reads as tidy rather than sparse, and it keeps the test cheap enough to
    run thousands of times.
    """
    r_cand = candidate.radius()
    for other in placed:
        min_dist = r_cand + other.radius() + spacing
        if math.dist(candidate.position, other.position) < min_dist:
            return False
    return True


def _corner_clear(
    candidate: ScatterItem,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]],
    margin: float,
) -> bool:
    """Check the footprint corners, not just the centre, sit inside the board."""
    cos_r = math.cos(candidate.rotation)
    sin_r = math.sin(candidate.rotation)
    hw = candidate.width * 0.5
    hh = candidate.height * 0.5
    for ox, oy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        corner = (
            candidate.position[0] + ox * cos_r - oy * sin_r,
            candidate.position[1] + ox * sin_r + oy * cos_r,
        )
        if not geometry.clearance_ok(corner, outer, holes, margin):
            return False
    return True


def scatter_parts(
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    keepouts: Sequence[Keepout] = (),
    specs: Sequence[PartSpec] = (),
    config: Optional[ScatterConfig] = None,
) -> ScatterResult:
    """Distribute parts across the board.

    ``keepouts`` should include every precisely placed component, so the
    important parts claim their space first and the filler works around them.
    """
    config = config or ScatterConfig()
    result = ScatterResult()

    if not specs or len(outer) < 3:
        return result

    rng = random.Random(config.seed)
    board_area = geometry.shape_area(outer, holes)
    if board_area <= 0.0:
        return result

    # Roughly one filler part per 45 mm^2 at density 1.0.
    target = int(max(1, board_area / 45.0 * config.density))
    target = min(target, 1200)

    min_x, min_y, max_x, max_y = geometry.bounds(outer)

    cluster_count = config.cluster_count or max(2, int(math.sqrt(board_area) / 7.0))
    clusters: List[Vec2] = []
    guard = 0
    while len(clusters) < cluster_count and guard < cluster_count * 60:
        guard += 1
        point = (rng.uniform(min_x, max_x), rng.uniform(min_y, max_y))
        if geometry.clearance_ok(point, outer, holes, config.edge_margin):
            clusters.append(point)
    if not clusters:
        clusters.append(geometry.centroid(outer))

    span = max(max_x - min_x, max_y - min_y)
    cluster_sigma = span * (1.0 - config.cluster_tightness) * 0.5 + span * 0.06

    for _ in range(target):
        spec = _weighted_choice(rng, specs)

        for _ in range(config.max_attempts_per_item):
            cluster = rng.choice(clusters)
            position = (
                rng.gauss(cluster[0], cluster_sigma),
                rng.gauss(cluster[1], cluster_sigma),
            )

            rotation = math.radians(rng.choice((0.0, 90.0, 180.0, 270.0)))
            if config.align_jitter:
                rotation += math.radians(
                    rng.uniform(-config.align_jitter, config.align_jitter)
                )

            candidate = ScatterItem(
                key=spec.key,
                position=position,
                rotation=rotation,
                width=spec.width,
                height=spec.height,
            )

            if not _corner_clear(candidate, outer, holes, config.edge_margin):
                continue
            if any(
                k.contains(position, max(spec.width, spec.height) * 0.5)
                for k in keepouts
            ):
                continue
            if not _footprint_clear(candidate, result.items, config.spacing):
                continue

            result.items.append(candidate)
            result.pads.extend(candidate.pads(spec))
            break

    return result


def default_part_specs() -> List[PartSpec]:
    """A filler mix that reads as a generic consumer board.

    Weights are deliberately lopsided toward small passives — real boards are
    mostly 0402s and 0603s, and getting that ratio right matters more to the
    look than any individual part's detail.
    """
    return [
        PartSpec("passive_0402", 1.0, 0.5, weight=6.0,
                 pad_offsets=((-0.45, 0.0), (0.45, 0.0))),
        PartSpec("passive_0603", 1.6, 0.8, weight=5.0,
                 pad_offsets=((-0.75, 0.0), (0.75, 0.0))),
        PartSpec("passive_0805", 2.0, 1.25, weight=2.5,
                 pad_offsets=((-0.95, 0.0), (0.95, 0.0))),
        PartSpec("passive_1206", 3.2, 1.6, weight=1.0,
                 pad_offsets=((-1.5, 0.0), (1.5, 0.0))),
        PartSpec("led_0603", 1.6, 0.8, weight=0.6,
                 pad_offsets=((-0.75, 0.0), (0.75, 0.0))),
        PartSpec("sot23", 2.9, 1.3, weight=1.2,
                 pad_offsets=((-0.95, -0.5), (0.95, -0.5), (0.0, 0.5))),
        PartSpec("soic8", 4.9, 3.9, weight=0.5),
        PartSpec("qfn16", 4.0, 4.0, weight=0.35),
        PartSpec("crystal_smd", 3.2, 2.5, weight=0.3),
        PartSpec("inductor_smd", 2.5, 2.0, weight=0.4),
        PartSpec("tantalum_cap", 3.2, 1.6, weight=0.4,
                 pad_offsets=((-1.5, 0.0), (1.5, 0.0))),
        PartSpec("electrolytic_cap", 5.0, 5.0, weight=0.15),
        PartSpec("test_point", 1.2, 1.2, weight=0.5),
    ]
