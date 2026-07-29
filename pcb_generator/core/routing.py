"""Procedural trace generation.

This is a deliberately fake router. It produces traces that *look* like a
routed board without any notion of a netlist, connectivity or electrical
correctness. What the eye actually reads on a PCB is statistical — trace
density, how runs bundle together and travel in parallel, consistent widths,
45-degree corners, via scatter. Those are the things this reproduces.

Everything is driven by an explicit seed, so a given configuration always
produces the same board and you can hunt for a layout you like by changing one
integer.
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from . import geometry
from .geometry import Ring, Vec2


@dataclass
class Keepout:
    """A rectangular region the router must avoid, in board space."""

    center: Vec2
    width: float
    height: float
    rotation: float = 0.0

    def contains(self, point: Vec2, margin: float = 0.0) -> bool:
        dx = point[0] - self.center[0]
        dy = point[1] - self.center[1]
        if self.rotation:
            cos_r = math.cos(-self.rotation)
            sin_r = math.sin(-self.rotation)
            dx, dy = dx * cos_r - dy * sin_r, dx * sin_r + dy * cos_r
        return (
            abs(dx) <= self.width * 0.5 + margin
            and abs(dy) <= self.height * 0.5 + margin
        )


LAYER_TOP = 0
LAYER_BOTTOM = 1


@dataclass
class Trace:
    points: List[Vec2]
    width: float
    layer: int = LAYER_TOP


@dataclass
class RouteConfig:
    grid_pitch: float = 0.5
    """Router grid spacing. Must exceed trace_width + clearance."""

    trace_width: float = 0.2
    power_trace_width: float = 0.45
    power_fraction: float = 0.15
    """Share of traces drawn at the wider power width."""

    clearance: float = 0.2
    edge_margin: float = 1.0
    """Keep traces this far in from the board edge and any cutout."""

    density: float = 1.0
    """Scales the number of nets attempted, relative to board area."""

    hub_count: int = 0
    """Routing hubs. 0 derives a count from board area."""

    turn_cost: float = 1.8
    bundle_bonus: float = 0.35
    """Cost discount for running alongside an existing trace. Drives bundling."""

    via_cost: float = 7.0
    """Cost of switching layers, in grid steps.

    A single-layer grid router deadlocks quickly: every completed trace is an
    unbroken wall that partitions the remaining free space, so after a handful
    of routes most endpoint pairs are in disconnected pockets and nothing else
    can be routed. Real boards solve this with layers and vias, and so does
    this. The cost is set high enough that routes prefer to stay on top and
    only dive to cross something, which produces a top-heavy board sprinkled
    with vias exactly where traces pass over each other.
    """

    via_chance: float = 0.2
    """Extra probability a route endpoint gets a via, beyond layer changes."""

    max_route_cells: int = 40000
    """A* node budget per net. Prevents pathological searches on big boards."""

    endpoint_retries: int = 3
    """Endpoint pairs to try before giving up on a net."""

    seed: int = 0


@dataclass
class RouteResult:
    traces: List[Trace] = field(default_factory=list)
    vias: List[Vec2] = field(default_factory=list)
    attempted: int = 0
    routed: int = 0

    @property
    def success_rate(self) -> float:
        return self.routed / self.attempted if self.attempted else 0.0


# Eight-way movement. Diagonals give the 45-degree runs that read as PCB-like.
_NEIGHBOURS = (
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (1, -1), (-1, 1), (-1, -1),
)


class _Grid:
    """Routing grid over the board's bounding box."""

    def __init__(
        self,
        outer: Sequence[Vec2],
        holes: Sequence[Sequence[Vec2]],
        keepouts: Sequence[Keepout],
        config: RouteConfig,
    ) -> None:
        self.pitch = max(config.grid_pitch, 1e-3)
        min_x, min_y, max_x, max_y = geometry.bounds(outer)
        self.origin = (min_x, min_y)
        self.cols = max(2, int((max_x - min_x) / self.pitch) + 1)
        self.rows = max(2, int((max_y - min_y) / self.pitch) + 1)

        margin = config.edge_margin + config.trace_width * 0.5
        keepout_margin = config.clearance

        self.passable: List[List[bool]] = []
        for j in range(self.rows):
            row: List[bool] = []
            for i in range(self.cols):
                point = self.to_world((i, j))
                ok = geometry.clearance_ok(point, outer, holes, margin)
                if ok:
                    ok = not any(k.contains(point, keepout_margin) for k in keepouts)
                row.append(ok)
            self.passable.append(row)

        # Occupancy is tracked per layer. Vias pierce the board, so a cell
        # carrying one is unusable on both.
        self.used: Dict[int, Set[Tuple[int, int]]] = {
            LAYER_TOP: set(),
            LAYER_BOTTOM: set(),
        }
        self.vias: Set[Tuple[int, int]] = set()

    def to_world(self, cell: Tuple[int, int]) -> Vec2:
        return (
            self.origin[0] + cell[0] * self.pitch,
            self.origin[1] + cell[1] * self.pitch,
        )

    def to_cell(self, point: Vec2) -> Tuple[int, int]:
        return (
            int(round((point[0] - self.origin[0]) / self.pitch)),
            int(round((point[1] - self.origin[1]) / self.pitch)),
        )

    def in_bounds(self, cell: Tuple[int, int]) -> bool:
        return 0 <= cell[0] < self.cols and 0 <= cell[1] < self.rows

    def is_free(self, cell: Tuple[int, int], layer: int = LAYER_TOP) -> bool:
        if not self.in_bounds(cell):
            return False
        if not self.passable[cell[1]][cell[0]]:
            return False
        if cell in self.vias:
            return False
        return cell not in self.used[layer]

    def free_cells(self, layer: int = LAYER_TOP) -> List[Tuple[int, int]]:
        return [
            (i, j)
            for j in range(self.rows)
            for i in range(self.cols)
            if self.is_free((i, j), layer)
        ]

    def nearest_free(
        self, cell: Tuple[int, int], layer: int = LAYER_TOP, radius: int = 6
    ) -> Optional[Tuple[int, int]]:
        """Walk outward in rings looking for a usable cell near ``cell``."""
        if self.is_free(cell, layer):
            return cell
        for r in range(1, radius + 1):
            for di in range(-r, r + 1):
                for dj in (-r, r):
                    candidate = (cell[0] + di, cell[1] + dj)
                    if self.is_free(candidate, layer):
                        return candidate
            for dj in range(-r + 1, r):
                for di in (-r, r):
                    candidate = (cell[0] + di, cell[1] + dj)
                    if self.is_free(candidate, layer):
                        return candidate
        return None

    def open_neighbour_count(self, cell: Tuple[int, int], layer: int) -> int:
        """How many ways out a cell has. Endpoints walled in on all sides are
        unroutable, and rejecting them up front is far cheaper than letting A*
        exhaust the pocket before failing."""
        return sum(
            1
            for di, dj in _NEIGHBOURS
            if self.is_free((cell[0] + di, cell[1] + dj), layer)
        )

    def has_used_neighbour(self, cell: Tuple[int, int], layer: int) -> bool:
        i, j = cell
        used = self.used[layer]
        for di, dj in _NEIGHBOURS:
            if (i + di, j + dj) in used:
                return True
        return False


def _octile(a: Tuple[int, int], b: Tuple[int, int]) -> float:
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return (dx + dy) + (math.sqrt(2.0) - 2.0) * min(dx, dy)


Node = Tuple[int, int, int]  # (i, j, layer)


def _route_one(
    grid: _Grid,
    start: Tuple[int, int],
    goal: Tuple[int, int],
    config: RouteConfig,
) -> Optional[List[Node]]:
    """A* between two cells, across two layers.

    Cost model beyond plain distance:

    * a turn penalty, so runs prefer to stay straight and change direction in
      clean 45-degree steps rather than staircasing;
    * a discount for cells adjacent to an already-routed cell on the same layer,
      which is what makes separate nets collapse into parallel bundles the way
      real boards do;
    * a via cost for changing layer, high enough that routes only dive to get
      past something in the way.

    Returns a path of ``(i, j, layer)`` nodes, or None if no route exists.
    """
    start_node: Node = (start[0], start[1], LAYER_TOP)
    goal_cell = goal

    open_heap: List[Tuple[float, int, Node, Optional[Node]]] = []
    counter = 0
    heapq.heappush(open_heap, (0.0, counter, start_node, None))

    came_from: Dict[Node, Optional[Node]] = {}
    best_cost: Dict[Node, float] = {start_node: 0.0}
    expanded = 0

    while open_heap:
        _, _, current, parent = heapq.heappop(open_heap)
        if current in came_from:
            continue
        came_from[current] = parent
        expanded += 1

        if (current[0], current[1]) == goal_cell:
            path = [current]
            node = parent
            while node is not None:
                path.append(node)
                node = came_from[node]
            path.reverse()
            return path

        if expanded > config.max_route_cells:
            return None

        ci, cj, layer = current

        if parent is not None and parent[2] == layer:
            in_dir = (ci - parent[0], cj - parent[1])
        else:
            in_dir = None

        # In-plane moves.
        for di, dj in _NEIGHBOURS:
            cell = (ci + di, cj + dj)
            nxt: Node = (cell[0], cell[1], layer)
            if nxt in came_from or not grid.is_free(cell, layer):
                continue

            # Do not cut diagonally past a blocked orthogonal neighbour.
            if di and dj:
                if not grid.is_free((ci + di, cj), layer) and not grid.is_free(
                    (ci, cj + dj), layer
                ):
                    continue

            cost = math.sqrt(2.0) if (di and dj) else 1.0

            if in_dir is not None and (di, dj) != in_dir:
                cost += config.turn_cost

            if grid.has_used_neighbour(cell, layer):
                cost -= config.bundle_bonus
            cost = max(cost, 0.05)

            tentative = best_cost[current] + cost
            if tentative < best_cost.get(nxt, math.inf):
                best_cost[nxt] = tentative
                counter += 1
                heapq.heappush(
                    open_heap,
                    (tentative + _octile(cell, goal_cell), counter, nxt, current),
                )

        # Layer change, in place.
        other = LAYER_BOTTOM if layer == LAYER_TOP else LAYER_TOP
        via_node: Node = (ci, cj, other)
        if via_node not in came_from and grid.is_free((ci, cj), other):
            tentative = best_cost[current] + config.via_cost
            if tentative < best_cost.get(via_node, math.inf):
                best_cost[via_node] = tentative
                counter += 1
                heapq.heappush(
                    open_heap,
                    (
                        tentative + _octile((ci, cj), goal_cell),
                        counter,
                        via_node,
                        current,
                    ),
                )

    return None


def _split_path_by_layer(
    grid: _Grid, path: Sequence[Node]
) -> Tuple[List[Tuple[int, List[Tuple[int, int]]]], List[Tuple[int, int]]]:
    """Break a multi-layer path into per-layer runs, and collect its vias."""
    runs: List[Tuple[int, List[Tuple[int, int]]]] = []
    vias: List[Tuple[int, int]] = []

    current_layer = path[0][2]
    current: List[Tuple[int, int]] = [(path[0][0], path[0][1])]

    for node in path[1:]:
        cell = (node[0], node[1])
        if node[2] != current_layer:
            vias.append(cell)
            runs.append((current_layer, current))
            current_layer = node[2]
            current = [cell]
        else:
            current.append(cell)

    runs.append((current_layer, current))
    return (runs, vias)


def _pick_hubs(
    grid: _Grid, rng: random.Random, count: int
) -> List[Tuple[int, int]]:
    free = grid.free_cells()
    if not free:
        return []
    count = max(2, min(count, len(free)))
    return rng.sample(free, count)


def _jitter_near(
    grid: _Grid,
    rng: random.Random,
    hub: Tuple[int, int],
    spread: int,
    min_openings: int = 3,
) -> Optional[Tuple[int, int]]:
    """A usable endpoint near ``hub`` that is not walled in."""
    for _ in range(16):
        candidate = (
            hub[0] + rng.randint(-spread, spread),
            hub[1] + rng.randint(-spread, spread),
        )
        if (
            grid.is_free(candidate, LAYER_TOP)
            and grid.open_neighbour_count(candidate, LAYER_TOP) >= min_openings
        ):
            return candidate

    fallback = grid.nearest_free(hub, LAYER_TOP)
    if fallback and grid.open_neighbour_count(fallback, LAYER_TOP) >= 1:
        return fallback
    return None


def generate_traces(
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    keepouts: Sequence[Keepout] = (),
    pads: Sequence[Vec2] = (),
    config: Optional[RouteConfig] = None,
) -> RouteResult:
    """Route a plausible-looking set of traces across the board.

    ``pads`` are anchor points from placed components. When supplied, routes
    preferentially start and end on them, which is what makes traces look like
    they belong to the parts rather than being wallpaper underneath them.
    """
    config = config or RouteConfig()
    result = RouteResult()

    if len(outer) < 3:
        return result

    rng = random.Random(config.seed)
    grid = _Grid(outer, holes, keepouts, config)

    free = grid.free_cells()
    if len(free) < 8:
        return result

    board_area = geometry.shape_area(outer, holes)
    # Roughly one net per 6 mm^2 at density 1.0. Tuned by rendering: sparser
    # than this and the board reads as a blank slab with a few lines on it,
    # because only the top-layer share of these nets is ever visible.
    net_count = int(max(4, board_area / 6.0 * config.density))
    net_count = min(net_count, 1600)

    hub_count = config.hub_count or max(3, int(math.sqrt(board_area) / 4.0))
    hubs = _pick_hubs(grid, rng, hub_count)
    if len(hubs) < 2:
        return result

    pad_cells = [grid.to_cell(p) for p in pads]
    pad_cells = [c for c in pad_cells if grid.in_bounds(c)]
    rng.shuffle(pad_cells)
    pad_queue = list(pad_cells)

    spread = max(2, int(math.sqrt(len(free)) / 6))

    for _ in range(net_count):
        result.attempted += 1
        path = None

        for attempt in range(max(1, config.endpoint_retries)):
            start = goal = None

            # Prefer routing between real pads while any remain unconsumed.
            if attempt == 0 and len(pad_queue) >= 2:
                raw_start = pad_queue.pop()
                raw_goal = min(pad_queue, key=lambda c: _octile(raw_start, c))
                pad_queue.remove(raw_goal)
                start = grid.nearest_free(raw_start, LAYER_TOP)
                goal = grid.nearest_free(raw_goal, LAYER_TOP)

            if start is None or goal is None:
                hub_a, hub_b = rng.sample(hubs, 2)
                start = _jitter_near(grid, rng, hub_a, spread)
                goal = _jitter_near(grid, rng, hub_b, spread)

            if start is None or goal is None or start == goal:
                continue

            path = _route_one(grid, start, goal, config)
            if path:
                break

        if not path:
            continue

        runs, via_cells = _split_path_by_layer(grid, path)

        for node in path:
            grid.used[node[2]].add((node[0], node[1]))
        for cell in via_cells:
            grid.vias.add(cell)

        width = (
            config.power_trace_width
            if rng.random() < config.power_fraction
            else config.trace_width
        )

        emitted = False
        for layer, cells in runs:
            points = geometry.simplify_collinear([grid.to_world(c) for c in cells])
            if len(points) < 2:
                continue
            result.traces.append(Trace(points=points, width=width, layer=layer))
            emitted = True

        if not emitted:
            continue

        result.routed += 1
        result.vias.extend(grid.to_world(c) for c in via_cells)

        # Endpoints of a net often terminate in a via on a real board even when
        # no layer change was needed.
        for endpoint_cell in (path[0], path[-1]):
            if rng.random() < config.via_chance:
                result.vias.append(grid.to_world((endpoint_cell[0], endpoint_cell[1])))

    return result


def pour_outline(
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    inset: float = 0.8,
) -> Tuple[Ring, List[Ring]]:
    """Inset shape for a copper pour.

    Approximate by design — a pour edge is a soft visual detail and nothing
    downstream measures it.
    """
    # offset_ring grows the enclosed region for positive distances regardless of
    # winding, so the outline shrinks and the cutouts grow — both pull the pour
    # inward, away from every board edge.
    poured_outer = geometry.offset_ring(geometry.as_ccw(outer), -inset)
    poured_holes = [
        geometry.offset_ring(geometry.as_cw(h), inset) for h in holes
    ]
    return (poured_outer, poured_holes)
