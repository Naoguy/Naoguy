import math

import pytest

from pcb_generator.core import geometry, scatter
from pcb_generator.core.routing import Keepout
from pcb_generator.core.scatter import PartSpec, ScatterConfig


BOARD = [(0.0, 0.0), (50.0, 0.0), (50.0, 40.0), (0.0, 40.0)]
SPECS = scatter.default_part_specs()


def test_scatter_places_parts():
    result = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=1))
    assert len(result.items) > 5


def test_scatter_is_deterministic_for_a_given_seed():
    a = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=8))
    b = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=8))
    assert [(i.key, i.position, i.rotation) for i in a.items] == [
        (i.key, i.position, i.rotation) for i in b.items
    ]


def test_parts_stay_inside_the_board_with_margin():
    config = ScatterConfig(seed=2, edge_margin=1.5)
    result = scatter.scatter_parts(BOARD, specs=SPECS, config=config)
    assert result.items

    for item in result.items:
        assert geometry.clearance_ok(item.position, BOARD, margin=config.edge_margin)


def test_part_footprints_do_not_overlap():
    config = ScatterConfig(seed=4, spacing=0.6)
    result = scatter.scatter_parts(BOARD, specs=SPECS, config=config)
    items = result.items
    assert len(items) > 5

    for i, a in enumerate(items):
        for b in items[i + 1:]:
            gap = math.dist(a.position, b.position) - a.radius() - b.radius()
            assert gap >= config.spacing - 1e-9


def test_scatter_respects_keepouts():
    keepout = Keepout(center=(25.0, 20.0), width=20.0, height=14.0)
    result = scatter.scatter_parts(
        BOARD, keepouts=[keepout], specs=SPECS, config=ScatterConfig(seed=3)
    )
    assert result.items
    for item in result.items:
        assert not keepout.contains(item.position)


def test_scatter_avoids_holes():
    hole = geometry.as_cw([(20.0, 15.0), (30.0, 15.0), (30.0, 25.0), (20.0, 25.0)])
    result = scatter.scatter_parts(
        BOARD, holes=[hole], specs=SPECS, config=ScatterConfig(seed=5)
    )
    assert result.items
    for item in result.items:
        assert not geometry.point_in_ring(item.position, hole)


def test_density_scales_part_count():
    sparse = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=6, density=0.3))
    dense = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=6, density=3.0))
    assert len(dense.items) > len(sparse.items)


def test_rotations_snap_to_ninety_degrees():
    result = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=7))
    assert result.items
    for item in result.items:
        degrees = math.degrees(item.rotation) % 360.0
        assert min(abs(degrees - a) for a in (0.0, 90.0, 180.0, 270.0, 360.0)) < 1e-6


def test_align_jitter_breaks_exact_alignment():
    result = scatter.scatter_parts(
        BOARD, specs=SPECS, config=ScatterConfig(seed=7, align_jitter=3.0)
    )
    assert result.items
    off_axis = [
        item
        for item in result.items
        if min(
            abs(math.degrees(item.rotation) % 360.0 - a)
            for a in (0.0, 90.0, 180.0, 270.0, 360.0)
        )
        > 1e-6
    ]
    assert off_axis


def test_pads_are_collected_from_placed_parts():
    spec = PartSpec("two_pad", 2.0, 1.0, pad_offsets=((-0.8, 0.0), (0.8, 0.0)))
    result = scatter.scatter_parts(BOARD, specs=[spec], config=ScatterConfig(seed=9))
    assert result.items
    assert len(result.pads) == len(result.items) * 2


def test_scatter_item_pads_follow_rotation():
    spec = PartSpec("two_pad", 2.0, 1.0, pad_offsets=((1.0, 0.0),))
    item = scatter.ScatterItem(
        key="two_pad", position=(5.0, 5.0), rotation=math.pi / 2, width=2.0, height=1.0
    )
    pad = item.pads(spec)[0]
    assert pad == pytest.approx((5.0, 6.0), abs=1e-9)


def test_result_keepouts_cover_placed_items():
    result = scatter.scatter_parts(BOARD, specs=SPECS, config=ScatterConfig(seed=10))
    keepouts = result.keepouts(margin=0.5)
    assert len(keepouts) == len(result.items)
    for item, k in zip(result.items, keepouts):
        assert k.contains(item.position)


def test_no_specs_yields_nothing():
    assert scatter.scatter_parts(BOARD, specs=[]).items == []


def test_degenerate_board_yields_nothing():
    assert scatter.scatter_parts([(0.0, 0.0), (1.0, 0.0)], specs=SPECS).items == []


def test_default_specs_are_weighted_toward_small_passives():
    specs = {s.key: s for s in scatter.default_part_specs()}
    assert specs["passive_0402"].weight > specs["soic8"].weight
    assert specs["passive_0603"].weight > specs["electrolytic_cap"].weight
