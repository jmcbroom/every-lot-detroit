import pytest
from shapely.geometry import Point

import everylot

from everylot import parcel_attr, image_coordinates, get_closest_images, filter_nearby_sequences, format_capture_time, format_sale, load_auction_parcels


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


def test_format_capture_time_uses_detroit_time():
    # 2023-07-02 01:30 UTC is still the evening of July 1 in Detroit (EDT).
    captured_at = 1688261400000
    assert format_capture_time(captured_at, "%Y-%m-%d %H:%M") == "2023-07-01 21:30"


def _sale(**overrides):
    sale = {
        "parcel_id": "01002263.",
        "address": "90 CHANDLER",
        "sale_amount": "14500.00",
        "bidder_name": "JANE DOE",
        "bidder_city": "NOVI",
        "bidder_state": "MI",
    }
    sale.update(overrides)
    return sale


def test_format_sale_whole_dollars():
    assert format_sale(_sale()) == "Sold at auction for $14,500 to JANE DOE of NOVI, MI"


def test_format_sale_keeps_cents():
    assert "$12,309.86" in format_sale(_sale(sale_amount="12309.86"))


def test_format_sale_foreign_bidder_omits_state():
    text = format_sale(_sale(bidder_city="STOCKHOLM", bidder_state="OT"))
    assert text.endswith("of STOCKHOLM")


def test_auction_parcels_csv_loads():
    sales = load_auction_parcels()
    assert len(sales) > 1000
    assert len({s["parcel_id"] for s in sales}) == len(sales)
    for sale in sales:
        float(sale["sale_amount"])
        assert sale["bidder_name"] and sale["bidder_city"] and sale["bidder_state"]


def test_parcel_picker_auction_mode_yields_distinct_sales(monkeypatch):
    monkeypatch.setattr(everylot, "get_parcel", lambda parcel_id: {"parcel_id": parcel_id})
    monkeypatch.setattr(everylot, "get_posted_auction_parcel_ids", lambda: set())
    pick = everylot.parcel_picker("auction")
    picks = [pick() for _ in range(everylot.MAX_PARCEL_ATTEMPTS)]
    ids = [sale["parcel_id"] for _, sale in picks]
    assert len(set(ids)) == len(ids)
    assert all(parcel["parcel_id"] == sale["parcel_id"] for parcel, sale in picks)


def test_parcel_picker_random_mode_has_no_sale(monkeypatch):
    def fail():
        raise AssertionError("random mode shouldn't check posted parcels")

    monkeypatch.setattr(everylot, "get_posted_auction_parcel_ids", fail)
    monkeypatch.setattr(everylot, "get_parcel_count_with_retry", lambda: 10)
    monkeypatch.setattr(everylot, "get_random_parcel", lambda count: {"count": count})
    parcel, sale = everylot.parcel_picker("random")()
    assert parcel == {"count": 10}
    assert sale is None


def test_parcel_picker_auction_mode_skips_posted(monkeypatch):
    sales = [_sale(parcel_id=f"0000000{i}.") for i in range(5)]
    monkeypatch.setattr(everylot, "load_auction_parcels", lambda: sales)
    monkeypatch.setattr(everylot, "get_parcel", lambda parcel_id: {"parcel_id": parcel_id})
    monkeypatch.setattr(
        everylot, "get_posted_auction_parcel_ids", lambda: {"00000000.", "00000002.", "00000004."}
    )
    pick = everylot.parcel_picker("auction")
    picked = set()
    while (candidate := pick()) is not None:
        picked.add(candidate[1]["parcel_id"])
    assert picked == {"00000001.", "00000003."}


def test_parcel_ids_in_posts():
    texts = [
        "90 CHANDLER\nSold at auction for $14,500 to JANE DOE of NOVI, MI\nParcel ID: 01002263.\nYear built: 1925",
        "5943 BEAUBIEN\nParcel ID: 01003671.003\nZoned R2",
        "Parcel info: https://baseunits.detroitmi.gov/map?id=01002263.&layer=parcel",
    ]
    assert everylot.parcel_ids_in_posts(texts) == {"01002263.", "01003671.003"}


def _seq(distance, captured_at):
    return {"distance": distance, "captured_at": captured_at}


def test_filter_nearby_sequences_floor_keeps_close_sequences():
    # Frontage anchor: closest image is ~0.4m away. A purely relative 2x cutoff
    # would keep only "a"; the floor keeps the other sequences a few meters off.
    sequences = {
        "a": _seq(3.9e-6, 300),
        "b": _seq(3e-5, 200),
        "c": _seq(5e-5, 100),
        "alley": _seq(4e-4, 50),
    }
    result = filter_nearby_sequences(sequences, 3.9e-6)
    assert "alley" not in result
    assert len(result) >= 2


def test_filter_nearby_sequences_relative_cutoff_still_applies_when_far():
    sequences = {"a": _seq(0.0002, 1), "b": _seq(0.00039, 2), "c": _seq(0.0005, 3)}
    result = filter_nearby_sequences(sequences, 0.0002)
    assert set(result) == {"a", "b"}


def test_filter_nearby_sequences_keeps_both_of_two():
    sequences = {"a": _seq(1e-5, 1), "b": _seq(2e-5, 2)}
    assert set(filter_nearby_sequences(sequences, 1e-5)) == {"a", "b"}


def test_filter_nearby_sequences_newest_first():
    sequences = {"old": _seq(1e-5, 1), "new": _seq(2e-5, 3), "mid": _seq(3e-5, 2)}
    # 66% of 3 keeps the two closest (old, new), then orders them newest first.
    assert list(filter_nearby_sequences(sequences, 1e-5)) == ["new", "old"]
