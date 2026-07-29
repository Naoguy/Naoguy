import math

import pytest

from pcb_generator.core import geometry, placement
from pcb_generator.core.placement import AnchorSpec


SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_free_anchor_passes_position_through():
    spec = AnchorSpec(mode=placement.MODE_FREE, position=(3.0, 4.0), rotation=1.0)
    result = placement.resolve(spec)
    assert result.position == (3.0, 4.0)
    assert result.rotation == 1.0
    assert result.resolved


def test_edge_anchor_sits_on_the_outline():
    spec = AnchorSpec(mode=placement.MODE_EDGE, edge_t=0.125)
    result = placement.resolve(spec, SQUARE)
    assert result.position == pytest.approx((5.0, 0.0))


def test_edge_anchor_offset_follows_the_outward_normal():
    spec = AnchorSpec(mode=placement.MODE_EDGE, edge_t=0.125, edge_offset=2.0)
    result = placement.resolve(spec, SQUARE)
    # Bottom edge of a CCW ring faces -Y, so a positive offset moves outward.
    assert result.position == pytest.approx((5.0, -2.0))

    inboard = placement.resolve(
        AnchorSpec(mode=placement.MODE_EDGE, edge_t=0.125, edge_offset=-2.0), SQUARE
    )
    assert inboard.position == pytest.approx((5.0, 2.0))


def test_edge_anchor_aligns_to_the_normal():
    spec = AnchorSpec(mode=placement.MODE_EDGE, edge_t=0.375, align_to_edge=True)
    result = placement.resolve(spec, SQUARE)
    # Right-hand edge faces +X.
    assert math.cos(result.rotation) == pytest.approx(1.0, abs=1e-9)


def test_edge_anchor_can_ignore_alignment():
    spec = AnchorSpec(
        mode=placement.MODE_EDGE, edge_t=0.375, align_to_edge=False, rotation=0.25
    )
    assert placement.resolve(spec, SQUARE).rotation == pytest.approx(0.25)


def test_edge_anchor_without_an_outline_falls_back():
    spec = AnchorSpec(mode=placement.MODE_EDGE, position=(1.0, 2.0), edge_t=0.5)
    result = placement.resolve(spec, [])
    assert result.position == (1.0, 2.0)
    assert not result.resolved
    assert "outline" in result.note


def test_target_anchor_inherits_position():
    spec = AnchorSpec(
        mode=placement.MODE_TARGET,
        target_position=(12.0, -3.0),
        target_offset=(0.5, 0.25),
    )
    result = placement.resolve(spec)
    assert result.position == pytest.approx((12.5, -2.75))
    assert result.resolved


def test_missing_target_holds_last_position_and_reports():
    spec = AnchorSpec(mode=placement.MODE_TARGET, position=(7.0, 7.0))
    result = placement.resolve(spec)
    assert result.position == (7.0, 7.0)
    assert not result.resolved
    assert "Target object missing" in result.note


def test_snap_pitch_quantises_the_result():
    spec = AnchorSpec(
        mode=placement.MODE_FREE, position=(3.1, 4.9), snap_pitch=2.54
    )
    result = placement.resolve(spec)
    assert result.position == pytest.approx((2.54, 5.08))


def test_bind_to_edge_does_not_move_an_existing_part():
    for point in ((5.0, -1.0), (10.5, 5.0), (5.0, 11.0), (2.0, 0.0)):
        spec = placement.bind_to_edge(SQUARE, point)
        assert spec.mode == placement.MODE_EDGE
        assert placement.resolve(spec, SQUARE).position == pytest.approx(point, abs=1e-6)


def test_edge_binding_survives_outline_densification():
    """A bound connector must not jump when the outline gains vertices."""
    point = (10.5, 5.0)
    spec = placement.bind_to_edge(SQUARE, point)

    dense = geometry.resample_ring(SQUARE, 0.9)
    moved = placement.resolve(spec, dense)
    assert moved.position == pytest.approx(point, abs=1e-6)


def test_target_drift_measures_staleness():
    spec = AnchorSpec(mode=placement.MODE_TARGET, target_position=(10.0, 0.0))
    assert placement.target_drift(spec, (10.0, 0.0)) == pytest.approx(0.0)
    assert placement.target_drift(spec, (13.0, 4.0)) == pytest.approx(5.0)


def test_target_drift_is_zero_for_other_modes():
    spec = AnchorSpec(mode=placement.MODE_FREE, position=(0.0, 0.0))
    assert placement.target_drift(spec, (99.0, 99.0)) == 0.0


def test_keepout_for_expands_by_margin():
    result = placement.Placement(position=(1.0, 2.0), rotation=0.0)
    keepout = placement.keepout_for(result, width=4.0, height=2.0, margin=0.5)
    assert keepout.width == pytest.approx(5.0)
    assert keepout.height == pytest.approx(3.0)
    assert keepout.contains((1.0, 2.0))


def test_distribute_along_edge_spaces_anchors_evenly():
    anchors = placement.distribute_along_edge(SQUARE, count=3, start_t=0.0, end_t=0.25)
    assert [a.edge_t for a in anchors] == pytest.approx([0.0, 0.125, 0.25])

    points = [placement.resolve(a, SQUARE).position for a in anchors]
    assert points[0] == pytest.approx((0.0, 0.0))
    assert points[1] == pytest.approx((5.0, 0.0))
    assert points[2] == pytest.approx((10.0, 0.0))


def test_distribute_along_edge_edge_cases():
    assert placement.distribute_along_edge(SQUARE, 0, 0.0, 1.0) == []
    single = placement.distribute_along_edge(SQUARE, 1, 0.4, 0.9)
    assert len(single) == 1
    assert single[0].edge_t == pytest.approx(0.4)
