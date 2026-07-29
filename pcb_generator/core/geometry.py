"""2D polygon utilities.

A board shape is represented as ``(outer, holes)`` where ``outer`` is a ring and
``holes`` is a list of rings. A ring is a list of ``(x, y)`` tuples in
millimetres, implicitly closed — the last point is *not* repeated.

Clearance tests are distance-based rather than offset-based: asking "is this
point at least d inside the board" is exact and cheap, whereas polygon
offsetting is neither. Offsetting is only used where an actual inset *shape* is
needed (the ground pour), and there a bisector offset is good enough because
nothing downstream depends on it being exact.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

Vec2 = Tuple[float, float]
Ring = List[Vec2]

EPS = 1e-9


# --------------------------------------------------------------------------
# Basic measures
# --------------------------------------------------------------------------


def signed_area(ring: Sequence[Vec2]) -> float:
    """Twice-signed area / 2. Positive when the ring winds counter-clockwise."""
    n = len(ring)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return total * 0.5


def area(ring: Sequence[Vec2]) -> float:
    return abs(signed_area(ring))


def shape_area(outer: Sequence[Vec2], holes: Sequence[Sequence[Vec2]] = ()) -> float:
    """Area of the outer ring minus its holes."""
    return area(outer) - sum(area(h) for h in holes)


def is_ccw(ring: Sequence[Vec2]) -> bool:
    return signed_area(ring) > 0.0


def as_ccw(ring: Sequence[Vec2]) -> Ring:
    return list(ring) if is_ccw(ring) else list(reversed(ring))


def as_cw(ring: Sequence[Vec2]) -> Ring:
    return list(reversed(ring)) if is_ccw(ring) else list(ring)


def centroid(ring: Sequence[Vec2]) -> Vec2:
    """Area centroid. Falls back to the vertex mean for degenerate rings."""
    n = len(ring)
    a = signed_area(ring)
    if n < 3 or abs(a) < EPS:
        if not ring:
            return (0.0, 0.0)
        return (
            sum(p[0] for p in ring) / n,
            sum(p[1] for p in ring) / n,
        )
    cx = cy = 0.0
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    factor = 1.0 / (6.0 * a)
    return (cx * factor, cy * factor)


def perimeter(ring: Sequence[Vec2]) -> float:
    n = len(ring)
    if n < 2:
        return 0.0
    return sum(
        math.dist(ring[i], ring[(i + 1) % n])
        for i in range(n)
    )


def bounds(points: Iterable[Vec2]) -> Tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)``."""
    pts = list(points)
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# --------------------------------------------------------------------------
# Containment and distance
# --------------------------------------------------------------------------


def point_in_ring(point: Vec2, ring: Sequence[Vec2]) -> bool:
    """Crossing-number test. Points exactly on the boundary are unspecified."""
    x, y = point
    inside = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            t = (y - y0) / (y1 - y0)
            if x < x0 + t * (x1 - x0):
                inside = not inside
    return inside


def point_in_shape(
    point: Vec2,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
) -> bool:
    if not point_in_ring(point, outer):
        return False
    return not any(point_in_ring(point, hole) for hole in holes)


def distance_point_to_segment(point: Vec2, a: Vec2, b: Vec2) -> float:
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < EPS:
        return math.dist(point, a)
    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.dist(point, (ax + t * dx, ay + t * dy))


def distance_to_ring(point: Vec2, ring: Sequence[Vec2]) -> float:
    """Unsigned distance from ``point`` to the ring's boundary."""
    n = len(ring)
    if n < 2:
        return math.inf
    return min(
        distance_point_to_segment(point, ring[i], ring[(i + 1) % n])
        for i in range(n)
    )


def distance_to_boundary(
    point: Vec2,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
) -> float:
    """Distance to the nearest edge of the shape, holes included."""
    best = distance_to_ring(point, outer)
    for hole in holes:
        best = min(best, distance_to_ring(point, hole))
    return best


def clearance_ok(
    point: Vec2,
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    margin: float = 0.0,
) -> bool:
    """True when ``point`` is inside the shape and at least ``margin`` from any edge.

    This is the workhorse test for scatter and routing. It is exact, unlike
    testing against an offset polygon.
    """
    if not point_in_shape(point, outer, holes):
        return False
    if margin <= 0.0:
        return True
    return distance_to_boundary(point, outer, holes) >= margin


# --------------------------------------------------------------------------
# Ring manipulation
# --------------------------------------------------------------------------


def resample_ring(ring: Sequence[Vec2], max_segment: float) -> Ring:
    """Insert points so no edge is longer than ``max_segment``."""
    if max_segment <= 0.0 or len(ring) < 2:
        return list(ring)
    out: Ring = []
    n = len(ring)
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        out.append(a)
        seg = math.dist(a, b)
        if seg > max_segment:
            steps = int(math.ceil(seg / max_segment))
            for s in range(1, steps):
                t = s / steps
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def simplify_collinear(points: Sequence[Vec2], tolerance: float = 1e-6) -> List[Vec2]:
    """Drop interior points that lie on the line between their neighbours.

    Operates on an open polyline. Used to clean up router output, where the
    grid produces long runs of collinear cells.
    """
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[i]
        cx, cy = points[i + 1]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(cross) > tolerance:
            out.append(points[i])
    out.append(points[-1])
    return out


def offset_ring(ring: Sequence[Vec2], distance: float) -> Ring:
    """Offset a ring by ``distance`` along vertex angle bisectors.

    Positive distance moves outward for a CCW ring. Miter length is clamped so
    sharp corners degrade gracefully rather than shooting off to infinity. This
    is approximate — it can self-intersect on tight concave corners — and is
    only used for the ground pour, where that does not matter.
    """
    n = len(ring)
    if n < 3 or abs(distance) < EPS:
        return list(ring)

    ccw = is_ccw(ring)
    sign = 1.0 if ccw else -1.0
    max_miter = abs(distance) * 4.0
    out: Ring = []

    for i in range(n):
        prev_pt = ring[(i - 1) % n]
        cur = ring[i]
        next_pt = ring[(i + 1) % n]

        d0 = _normalize((cur[0] - prev_pt[0], cur[1] - prev_pt[1]))
        d1 = _normalize((next_pt[0] - cur[0], next_pt[1] - cur[1]))
        if d0 is None or d1 is None:
            out.append(cur)
            continue

        # Outward normals for a CCW ring are the right-hand perpendiculars.
        n0 = (d0[1] * sign, -d0[0] * sign)
        n1 = (d1[1] * sign, -d1[0] * sign)

        bis = _normalize((n0[0] + n1[0], n0[1] + n1[1]))
        if bis is None:
            out.append((cur[0] + n0[0] * distance, cur[1] + n0[1] * distance))
            continue

        cos_half = bis[0] * n0[0] + bis[1] * n0[1]
        if abs(cos_half) < 1e-3:
            miter = max_miter
        else:
            miter = min(abs(distance / cos_half), max_miter)
        miter = math.copysign(miter, distance)
        out.append((cur[0] + bis[0] * miter, cur[1] + bis[1] * miter))

    return out


def _normalize(v: Vec2):
    length = math.hypot(v[0], v[1])
    if length < EPS:
        return None
    return (v[0] / length, v[1] / length)


# --------------------------------------------------------------------------
# Perimeter parameterisation
#
# Edge anchors address a position as a normalised distance along the perimeter
# rather than an edge index. That survives outline edits far better: inserting a
# vertex renumbers every subsequent edge index but barely moves the arc-length
# parameter of a point elsewhere on the ring.
# --------------------------------------------------------------------------


def point_at_perimeter(ring: Sequence[Vec2], t: float) -> Tuple[Vec2, Vec2]:
    """Point and outward unit normal at normalised perimeter position ``t``.

    ``t`` wraps, so 1.1 and 0.1 give the same result.
    """
    n = len(ring)
    if n < 2:
        return ((0.0, 0.0), (1.0, 0.0))

    total = perimeter(ring)
    if total < EPS:
        return (tuple(ring[0]), (1.0, 0.0))

    target = (t % 1.0) * total
    ccw = is_ccw(ring)
    sign = 1.0 if ccw else -1.0

    travelled = 0.0
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        seg = math.dist(a, b)
        if seg < EPS:
            continue
        if travelled + seg >= target or i == n - 1:
            local = (target - travelled) / seg
            local = max(0.0, min(1.0, local))
            point = (a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local)
            direction = ((b[0] - a[0]) / seg, (b[1] - a[1]) / seg)
            normal = (direction[1] * sign, -direction[0] * sign)
            return (point, normal)
        travelled += seg

    return (tuple(ring[0]), (1.0, 0.0))


def perimeter_at_point(ring: Sequence[Vec2], point: Vec2) -> float:
    """Inverse of :func:`point_at_perimeter` — nearest normalised position."""
    n = len(ring)
    total = perimeter(ring)
    if n < 2 or total < EPS:
        return 0.0

    best_t = 0.0
    best_d = math.inf
    travelled = 0.0
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        seg = math.dist(a, b)
        if seg < EPS:
            continue
        d = distance_point_to_segment(point, a, b)
        if d < best_d:
            best_d = d
            ax, ay = a
            dx, dy = b[0] - ax, b[1] - ay
            local = ((point[0] - ax) * dx + (point[1] - ay) * dy) / (seg * seg)
            local = max(0.0, min(1.0, local))
            best_t = (travelled + local * seg) / total
        travelled += seg
    return best_t


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def segments_intersect(a0: Vec2, a1: Vec2, b0: Vec2, b1: Vec2) -> bool:
    """Proper intersection test — shared endpoints do not count."""

    def orient(p, q, r):
        val = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        if abs(val) < EPS:
            return 0
        return 1 if val > 0 else -1

    o1 = orient(a0, a1, b0)
    o2 = orient(a0, a1, b1)
    o3 = orient(b0, b1, a0)
    o4 = orient(b0, b1, a1)

    # A proper crossing requires each segment to strictly straddle the other's
    # line. Any zero orientation means a collinear or touching configuration —
    # shared endpoints between consecutive ring edges being the common case —
    # and those are not crossings.
    if 0 in (o1, o2, o3, o4):
        return False

    return o1 != o2 and o3 != o4


def ring_self_intersects(ring: Sequence[Vec2]) -> bool:
    n = len(ring)
    if n < 4:
        return False
    for i in range(n):
        a0, a1 = ring[i], ring[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or j == (i + 1) % n:
                continue
            b0, b1 = ring[j], ring[(j + 1) % n]
            if segments_intersect(a0, a1, b0, b1):
                return True
    return False


def validate_shape(
    outer: Sequence[Vec2],
    holes: Sequence[Sequence[Vec2]] = (),
    min_area: float = 1.0,
) -> List[str]:
    """Return a list of human-readable problems. Empty means usable."""
    problems: List[str] = []

    if len(outer) < 3:
        problems.append("Outline needs at least 3 points.")
        return problems

    if area(outer) < min_area:
        problems.append(
            f"Outline area is {area(outer):.2f} mm^2, below the {min_area:.2f} mm^2 minimum. "
            "Check the scene is in millimetres."
        )

    if ring_self_intersects(outer):
        problems.append("Outline self-intersects.")

    for index, hole in enumerate(holes):
        if len(hole) < 3:
            problems.append(f"Cutout {index + 1} needs at least 3 points.")
            continue
        if ring_self_intersects(hole):
            problems.append(f"Cutout {index + 1} self-intersects.")
        if not point_in_ring(centroid(hole), outer):
            problems.append(f"Cutout {index + 1} lies outside the outline.")

    return problems


# --------------------------------------------------------------------------
# Boundary extraction
# --------------------------------------------------------------------------


def loops_from_edges(edges: Sequence[Tuple[int, int]]) -> List[List[int]]:
    """Chain undirected edges into closed loops of vertex indices.

    Used to pull board outlines out of existing meshes. Edges that do not form
    closed loops are returned as open chains, which the caller can reject.
    """
    adjacency: dict[int, List[int]] = {}
    for a, b in edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    unused = {tuple(sorted(e)) for e in edges}
    loops: List[List[int]] = []

    while unused:
        start_edge = next(iter(unused))
        unused.discard(start_edge)
        loop = [start_edge[0], start_edge[1]]

        while True:
            tail = loop[-1]
            nxt = None
            for candidate in adjacency.get(tail, ()):
                key = tuple(sorted((tail, candidate)))
                if key in unused:
                    nxt = candidate
                    unused.discard(key)
                    break
            if nxt is None:
                break
            if nxt == loop[0]:
                break
            loop.append(nxt)

        loops.append(loop)

    return loops


def classify_rings(rings: Sequence[Sequence[Vec2]]) -> Tuple[Ring, List[Ring]]:
    """Pick the largest ring as the outline and treat the rest as cutouts.

    Winding is normalised: outline counter-clockwise, cutouts clockwise, which
    is what :func:`~mathutils.geometry.tessellate_polygon` wants.
    """
    usable = [list(r) for r in rings if len(r) >= 3]
    if not usable:
        return ([], [])
    usable.sort(key=area, reverse=True)
    outer = as_ccw(usable[0])
    holes = [as_cw(r) for r in usable[1:]]
    return (outer, holes)
