import pytest
from shapely.geometry import Point

from everylot import parcel_attr, image_coordinates, get_closest_images


def test_parcel_attr_returns_present_value():
    assert parcel_attr({"year_built": 1925}, "year_built") == 1925


def test_parcel_attr_defaults_for_none():
    assert parcel_attr({"year_built": None}, "year_built") == "Unknown"


def test_parcel_attr_defaults_for_blank():
    assert parcel_attr({"year_built": ""}, "year_built") == "Unknown"


def test_parcel_attr_defaults_for_missing_key():
    assert parcel_attr({}, "year_built") == "Unknown"


def test_parcel_attr_custom_default():
    assert parcel_attr({}, "address", default="Unknown address") == "Unknown address"


def test_image_coordinates_prefers_computed_geometry():
    image = {
        "computed_geometry": {"type": "Point", "coordinates": [1, 2]},
        "geometry": {"type": "Point", "coordinates": [3, 4]},
    }
    assert image_coordinates(image) == [1, 2]


def test_image_coordinates_falls_back_to_geometry():
    image = {"geometry": {"type": "Point", "coordinates": [3, 4]}}
    assert image_coordinates(image) == [3, 4]


def _img(seq, x, y):
    return {"sequence": seq, "geometry": {"type": "Point", "coordinates": [x, y]}}


def test_get_closest_images_keeps_closest_per_sequence():
    anchor = Point(0, 0)
    images = [
        _img("s1", 3, 0),  # far in s1
        _img("s1", 1, 0),  # closest in s1
        _img("s2", 2, 0),  # only one in s2
    ]
    sequences, closest = get_closest_images(images, anchor)

    assert set(sequences.keys()) == {"s1", "s2"}
    assert sequences["s1"]["geometry"]["coordinates"] == [1, 0]
    assert sequences["s1"]["distance"] == pytest.approx(1)
    assert sequences["s2"]["distance"] == pytest.approx(2)
    assert closest == pytest.approx(1)
