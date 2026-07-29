import math

import pytest

from pcb_generator.core import geometry, routing
from pcb_generator.core.routing import Keepout, RouteConfig


BOARD = [(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)]

# Routing is the expensive part of the suite. Tests that only need to compare
# two runs against each other use a smaller board.
SMALL = [(0.0, 0.0), (20.0, 0.0), (20.0, 15.0), (0.0, 15.0)]


def test_keepout_contains_respects_rotation():
    k = Keepout(center=(0.0, 0.0), width=10.0, height=2.0)
    assert k.contains((4.0, 0.0))
    assert not k.contains((0.0, 4.0))

    rotated = Keepout(center=(0.0, 0.0), width=10.0, height=2.0, rotation=math.pi / 2)
    assert not rotated.contains((4.0, 0.0))
    assert rotated.contains((0.0, 4.0))


def test_keepout_margin_expands_the_region():
    k = Keepout(center=(0.0, 0.0), width=2.0, height=2.0)
    assert not k.contains((2.0, 0.0))
    assert k.contains((2.0, 0.0), margin=1.5)


def test_generate_traces_produces_routes():
    result = routing.generate_traces(BOARD, config=RouteConfig(seed=1))
    assert result.routed > 0
    assert result.success_rate > 0.2
    for trace in result.traces:
        assert len(trace.points) >= 2
        assert trace.width > 0.0


def test_traces_stay_inside_the_board_with_margin():
    config = RouteConfig(seed=3, edge_margin=1.0)
    result = routing.generate_traces(BOARD, config=config)
    assert result.routed > 0

    limit = config.edge_margin + config.trace_width * 0.5
    for trace in result.traces:
        for point in trace.points:
            assert geometry.point_in_shape(point, BOARD)
            # Allow a small tolerance: vertices land on grid centres, and
            # simplification keeps only corners.
            assert geometry.distance_to_boundary(point, BOARD) >= limit - 1e-6


def test_traces_avoid_keepouts():
    keepout = Keepout(center=(20.0, 15.0), width=12.0, height=8.0)
    result = routing.generate_traces(
        BOARD, keepouts=[keepout], config=RouteConfig(seed=7)
    )
    assert result.routed > 0

    for trace in result.traces:
        for point in trace.points:
            assert not keepout.contains(point)


def test_routing_is_deterministic_for_a_given_seed():
    a = routing.generate_traces(SMALL, config=RouteConfig(seed=42))
    b = routing.generate_traces(SMALL, config=RouteConfig(seed=42))
    assert [t.points for t in a.traces] == [t.points for t in b.traces]
    assert a.vias == b.vias


def test_different_seeds_give_different_boards():
    a = routing.generate_traces(SMALL, config=RouteConfig(seed=1))
    b = routing.generate_traces(SMALL, config=RouteConfig(seed=2))
    assert [t.points for t in a.traces] != [t.points for t in b.traces]


def test_density_scales_trace_count():
    sparse = routing.generate_traces(SMALL, config=RouteConfig(seed=5, density=0.3))
    dense = routing.generate_traces(SMALL, config=RouteConfig(seed=5, density=2.0))
    assert dense.attempted > sparse.attempted
    assert dense.routed > sparse.routed


def test_traces_do_not_overlap_on_the_same_layer():
    """Routed cells are consumed per layer, so no two traces share a position."""
    config = RouteConfig(seed=11)
    result = routing.generate_traces(BOARD, config=config)
    assert result.routed > 0

    seen: dict[int, set] = {}
    for trace in result.traces:
        layer_seen = seen.setdefault(trace.layer, set())
        for point in trace.points:
            key = (round(point[0], 6), round(point[1], 6))
            assert key not in layer_seen, "two traces share a cell on one layer"
            layer_seen.add(key)


def test_router_uses_both_layers_to_get_past_congestion():
    """Single-layer routing deadlocks; layers and vias are what prevent it."""
    result = routing.generate_traces(BOARD, config=RouteConfig(seed=11))
    layers = {t.layer for t in result.traces}
    assert layers == {routing.LAYER_TOP, routing.LAYER_BOTTOM}
    assert result.vias


def test_layering_lifts_the_success_rate():
    cheap_vias = routing.generate_traces(SMALL, config=RouteConfig(seed=11, via_cost=7.0))
    no_vias = routing.generate_traces(SMALL, config=RouteConfig(seed=11, via_cost=1e6))
    assert cheap_vias.routed > no_vias.routed


def test_traces_start_on_the_top_layer():
    result = routing.generate_traces(BOARD, config=RouteConfig(seed=13))
    assert result.traces
    assert result.traces[0].layer == routing.LAYER_TOP


def test_pads_are_used_as_route_endpoints():
    pads = [(6.0, 6.0), (34.0, 6.0), (6.0, 24.0), (34.0, 24.0)]
    result = routing.generate_traces(
        BOARD, pads=pads, config=RouteConfig(seed=9, density=0.2)
    )
    assert result.routed > 0

    endpoints = [t.points[0] for t in result.traces] + [t.points[-1] for t in result.traces]
    near_pad = sum(
        1
        for e in endpoints
        if min(math.dist(e, p) for p in pads) < 2.0
    )
    assert near_pad >= 2


def test_power_traces_are_wider():
    result = routing.generate_traces(
        SMALL, config=RouteConfig(seed=4, power_fraction=1.0)
    )
    assert result.routed > 0
    assert all(t.width == pytest.approx(0.5) for t in result.traces)


def test_holes_block_routing():
    hole = geometry.as_cw([(15.0, 10.0), (25.0, 10.0), (25.0, 20.0), (15.0, 20.0)])
    result = routing.generate_traces(
        BOARD, holes=[hole], config=RouteConfig(seed=6)
    )
    assert result.routed > 0
    for trace in result.traces:
        for point in trace.points:
            assert not geometry.point_in_ring(point, hole)


def test_degenerate_board_returns_empty_result():
    result = routing.generate_traces([(0.0, 0.0), (1.0, 0.0)])
    assert result.traces == []
    assert result.routed == 0


def test_tiny_board_does_not_crash():
    tiny = [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)]
    result = routing.generate_traces(tiny, config=RouteConfig(edge_margin=1.0))
    assert result.routed == 0


def test_pour_outline_pulls_away_from_every_edge():
    hole = geometry.as_cw([(15.0, 10.0), (25.0, 10.0), (25.0, 20.0), (15.0, 20.0)])
    poured_outer, poured_holes = routing.pour_outline(BOARD, [hole], inset=1.0)

    assert geometry.area(poured_outer) < geometry.area(BOARD)
    assert geometry.area(poured_holes[0]) > geometry.area(hole)


def test_success_rate_is_zero_when_nothing_attempted():
    result = routing.RouteResult()
    assert result.success_rate == 0.0
