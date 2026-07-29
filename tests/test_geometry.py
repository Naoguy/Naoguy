import math

import pytest

from pcb_generator.core import geometry


SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
HOLE = [(4.0, 4.0), (4.0, 6.0), (6.0, 6.0), (6.0, 4.0)]  # clockwise


def test_signed_area_sign_follows_winding():
    assert geometry.signed_area(SQUARE) == pytest.approx(100.0)
    assert geometry.signed_area(list(reversed(SQUARE))) == pytest.approx(-100.0)
    assert geometry.is_ccw(SQUARE)
    assert not geometry.is_ccw(list(reversed(SQUARE)))


def test_shape_area_subtracts_holes():
    assert geometry.shape_area(SQUARE, [HOLE]) == pytest.approx(96.0)


def test_as_ccw_and_as_cw_normalise_winding():
    assert geometry.is_ccw(geometry.as_ccw(list(reversed(SQUARE))))
    assert not geometry.is_ccw(geometry.as_cw(SQUARE))


def test_centroid_of_square():
    cx, cy = geometry.centroid(SQUARE)
    assert cx == pytest.approx(5.0)
    assert cy == pytest.approx(5.0)


def test_perimeter_and_bounds():
    assert geometry.perimeter(SQUARE) == pytest.approx(40.0)
    assert geometry.bounds(SQUARE) == (0.0, 0.0, 10.0, 10.0)


def test_point_in_shape_respects_holes():
    assert geometry.point_in_shape((1.0, 1.0), SQUARE, [HOLE])
    assert not geometry.point_in_shape((5.0, 5.0), SQUARE, [HOLE])
    assert not geometry.point_in_shape((-1.0, 5.0), SQUARE, [HOLE])


def test_distance_to_boundary_includes_holes():
    # (5, 2) is 2mm from the bottom edge and 2mm from the hole's lower edge.
    assert geometry.distance_to_boundary((5.0, 2.0), SQUARE, [HOLE]) == pytest.approx(2.0)


def test_clearance_ok_is_exact_not_offset_based():
    assert geometry.clearance_ok((5.0, 5.0), SQUARE, margin=4.0)
    assert not geometry.clearance_ok((5.0, 5.0), SQUARE, margin=6.0)
    # Inside the outline but too close to a cutout.
    assert not geometry.clearance_ok((5.0, 3.5), SQUARE, [HOLE], margin=1.0)


def test_resample_ring_bounds_segment_length():
    resampled = geometry.resample_ring(SQUARE, 2.0)
    n = len(resampled)
    longest = max(
        math.dist(resampled[i], resampled[(i + 1) % n]) for i in range(n)
    )
    assert longest <= 2.0 + 1e-9
    assert geometry.area(resampled) == pytest.approx(100.0)


def test_simplify_collinear_removes_interior_points():
    line = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (2.0, 2.0)]
    assert geometry.simplify_collinear(line) == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]


def test_simplify_collinear_keeps_short_polylines():
    assert geometry.simplify_collinear([(0.0, 0.0), (1.0, 1.0)]) == [
        (0.0, 0.0),
        (1.0, 1.0),
    ]


def test_offset_ring_expands_enclosed_region_regardless_of_winding():
    grown_ccw = geometry.offset_ring(SQUARE, 1.0)
    grown_cw = geometry.offset_ring(list(reversed(SQUARE)), 1.0)
    assert geometry.area(grown_ccw) == pytest.approx(144.0)
    assert geometry.area(grown_cw) == pytest.approx(144.0)

    shrunk = geometry.offset_ring(SQUARE, -1.0)
    assert geometry.area(shrunk) == pytest.approx(64.0)


def test_point_at_perimeter_walks_the_outline():
    point, normal = geometry.point_at_perimeter(SQUARE, 0.0)
    assert point == pytest.approx((0.0, 0.0))
    # Bottom edge of a CCW ring faces -Y.
    assert normal == pytest.approx((0.0, -1.0))

    point, normal = geometry.point_at_perimeter(SQUARE, 0.125)
    assert point == pytest.approx((5.0, 0.0))

    point, _ = geometry.point_at_perimeter(SQUARE, 0.375)
    assert point == pytest.approx((10.0, 5.0))


def test_point_at_perimeter_wraps():
    a, _ = geometry.point_at_perimeter(SQUARE, 0.2)
    b, _ = geometry.point_at_perimeter(SQUARE, 1.2)
    assert a == pytest.approx(b)


def test_perimeter_at_point_inverts_point_at_perimeter():
    for t in (0.05, 0.2, 0.4, 0.61, 0.87):
        point, _ = geometry.point_at_perimeter(SQUARE, t)
        assert geometry.perimeter_at_point(SQUARE, point) == pytest.approx(t, abs=1e-6)


def test_perimeter_parameter_is_stable_under_vertex_insertion():
    """The point of arc-length addressing: adding vertices must not move anchors."""
    point, _ = geometry.point_at_perimeter(SQUARE, 0.375)
    dense = geometry.resample_ring(SQUARE, 0.7)
    t_dense = geometry.perimeter_at_point(dense, point)
    moved, _ = geometry.point_at_perimeter(dense, t_dense)
    assert moved == pytest.approx(point, abs=1e-6)


def test_segments_intersect():
    assert geometry.segments_intersect((0, 0), (2, 2), (0, 2), (2, 0))
    assert not geometry.segments_intersect((0, 0), (1, 0), (2, 0), (3, 0))
    # Shared endpoints are not proper intersections.
    assert not geometry.segments_intersect((0, 0), (1, 1), (1, 1), (2, 0))


def test_ring_self_intersects_detects_bowtie():
    bowtie = [(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)]
    assert geometry.ring_self_intersects(bowtie)
    assert not geometry.ring_self_intersects(SQUARE)


def test_validate_shape_accepts_a_good_board():
    assert geometry.validate_shape(SQUARE, [HOLE]) == []


def test_validate_shape_reports_problems():
    problems = geometry.validate_shape([(0.0, 0.0), (1.0, 0.0)])
    assert any("3 points" in p for p in problems)

    tiny = [(0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)]
    assert any("millimetres" in p for p in geometry.validate_shape(tiny))

    outside_hole = [(20.0, 20.0), (21.0, 20.0), (21.0, 21.0)]
    assert any(
        "outside the outline" in p
        for p in geometry.validate_shape(SQUARE, [outside_hole])
    )


def test_loops_from_edges_chains_a_closed_loop():
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    loops = geometry.loops_from_edges(edges)
    assert len(loops) == 1
    assert sorted(loops[0]) == [0, 1, 2, 3]


def test_loops_from_edges_separates_disjoint_loops():
    edges = [(0, 1), (1, 2), (2, 0), (5, 6), (6, 7), (7, 5)]
    loops = geometry.loops_from_edges(edges)
    assert len(loops) == 2
    assert {len(loop) for loop in loops} == {3}


def test_classify_rings_picks_largest_as_outline():
    outer, holes = geometry.classify_rings([HOLE, SQUARE])
    assert geometry.area(outer) == pytest.approx(100.0)
    assert len(holes) == 1
    assert geometry.is_ccw(outer)
    assert not geometry.is_ccw(holes[0])


def test_classify_rings_handles_empty_input():
    assert geometry.classify_rings([]) == ([], [])
