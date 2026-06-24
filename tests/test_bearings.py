import math

import pytest

from bearings import (
    wrap_value,
    calculate_bearing,
    bearing_to_basic,
    compute_viewer_center,
)


def test_wrap_value_within_range():
    assert wrap_value(0.5, 0, 1) == pytest.approx(0.5)


def test_wrap_value_above_range_wraps():
    assert wrap_value(1.2, 0, 1) == pytest.approx(0.2)


def test_wrap_value_below_range_wraps():
    assert wrap_value(-0.3, 0, 1) == pytest.approx(0.7)


def test_calculate_bearing_due_north():
    assert calculate_bearing([0, 0], [0, 1]) == pytest.approx(0, abs=1e-6)


def test_calculate_bearing_due_east():
    assert calculate_bearing([0, 0], [1, 0]) == pytest.approx(90, abs=1e-6)


def test_bearing_to_basic_centers_on_node_bearing():
    # Looking exactly along the node's bearing maps to the image center (0.5).
    assert bearing_to_basic(42, 42) == pytest.approx(0.5)


def test_bearing_to_basic_quarter_turn():
    assert bearing_to_basic(90, 0) == pytest.approx(0.75)


def test_bearing_to_basic_opposite_wraps_to_zero():
    assert bearing_to_basic(180, 0) == pytest.approx(0.0)


def test_compute_viewer_center_uses_computed_compass_angle():
    image = {"computed_compass_angle": 0}
    x, y = compute_viewer_center(image, [0, 0], [0, 1])
    assert x == pytest.approx(0.5)
    assert y == pytest.approx(0.45)


def test_compute_viewer_center_without_angle_raises():
    with pytest.raises(ValueError):
        compute_viewer_center({}, [0, 0], [0, 1])
