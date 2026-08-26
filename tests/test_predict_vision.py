"""Deterministic tests for the tiled-vision PDF path in bc_dev_permits.modeling.predict.

The Ollama OCR itself is non-deterministic and needs a local model, so it is not exercised
here. What IS tested are the pure pieces that make the vision path reliable: tiling
geometry, cross-tile field merging, and the split-count arithmetic that turns the verbatim
cells the model transcribes ('9+1 VISITOR', '7 SHORT TERM : 18 LONG TERM') into integers.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.modeling import predict  # noqa: E402


# --------------------------------------------------------------------------- #
# split-count arithmetic (the values the vision model copies into parking_notes)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("notes", "vehicle", "bike"),
    [
        # Shelbourne: both cells transcribed together
        ("9+1 VISITOR, 7 SHORT TERM : 18 LONG TERM", 10, 25),
        ("9+1 VISITOR", 10, None),
        ("7 SHORT TERM : 18 LONG TERM", None, 25),
        ("18 LONG TERM : 7 SHORT TERM", None, 25),  # order-insensitive
        ("12 + 3 visitor", 15, None),  # spacing-insensitive
        # words/punctuation between the two bike counts must still sum
        ("182 Long Term Bicycle Parking, 14 Short Term", None, 196),
        ("108 long-term and 8 short-term bicycle parking spaces", None, 116),
        ("surface parking at grade", None, None),  # no numeric split -> untouched
    ],
)
def test_finalize_parking_sums_split_counts(notes, vehicle, bike):
    fields = {"parking_notes": notes}
    predict._finalize_parking(fields)
    assert fields.get("parking_vehicle_stalls") == vehicle
    assert fields.get("parking_bike_stalls") == bike


def test_finalize_parking_leaves_fields_when_no_notes():
    fields = {}
    predict._finalize_parking(fields)
    assert "parking_vehicle_stalls" not in fields
    assert "parking_bike_stalls" not in fields


# --------------------------------------------------------------------------- #
# cross-tile merge (majority vote across tiles)
# --------------------------------------------------------------------------- #
def test_reduce_majority_vote_beats_one_off_misread():
    # floor_area 1321 read by two tiles must beat a lone 186.62 misread.
    tiles = [
        {"development_class": "unknown", "floor_area": 1321},
        {"development_class": "residential", "floor_area": 186.62},
        {"floor_area": 1321},
    ]
    out = predict._reduce_fields(tiles)
    assert out["floor_area"] == 1321
    assert out["development_class"] == "residential"  # 'unknown' sentinel never collected


def test_reduce_combines_unit_mix_and_occupancy():
    tiles = [
        {"unit_mix": {"3-bed": 5}, "occupancy_types": ["residential"]},
        {"unit_mix": {"4-bed": 4}, "occupancy_types": ["commercial"]},
    ]
    out = predict._reduce_fields(tiles)
    assert out["unit_mix"] == {"3-bed": 5, "4-bed": 4}
    assert out["occupancy_types"] == ["commercial", "residential"]


def test_reduce_tolerates_unhashable_scalar_value():
    # A model may return a dict/list for a normally-scalar field (e.g. floor_area split by
    # use); majority vote can't hash it, so we keep the first without crashing.
    tiles = [
        {"floor_area": {"residential": 54028, "commercial": 2078}},
        {"floor_area": 54028, "units_total": 9},
    ]
    out = predict._reduce_fields(tiles)  # must not raise
    assert out["units_total"] == 9
    assert "floor_area" in out


def test_reduce_keeps_most_complete_parking_notes():
    tiles = [
        {"parking_notes": "9+1 VISITOR"},
        {"parking_notes": "9+1 VISITOR, 7 SHORT TERM : 18 LONG TERM"},
        {"parking_notes": "parking"},
    ]
    out = predict._reduce_fields(tiles)
    assert "18 LONG TERM" in out["parking_notes"]


def test_shelbourne_reduce_then_finalize_end_to_end():
    # Simulates the per-tile outputs the vision model returned for Shelbourne page 1.
    tiles = [
        {"development_class": "residential", "floor_area": 1321},
        {"unit_mix": {"total": 9, "4-bedroom": 4, "3-bedroom": 5}, "occupancy_types": ["residential", "commercial"]},
        {"number_of_stories": 3},
        {"parking_notes": "9+1 VISITOR, 7 SHORT TERM : 18 LONG TERM"},
    ]
    out = predict._reduce_fields(tiles)
    predict._finalize_parking(out)
    assert out["floor_area"] == 1321
    assert out["number_of_stories"] == 3
    assert out["unit_mix"] == {"total": 9, "4-bedroom": 4, "3-bedroom": 5}
    assert out["parking_vehicle_stalls"] == 10
    assert out["parking_bike_stalls"] == 25


# --------------------------------------------------------------------------- #
# count coercion: reject dimensions/areas the model misreads as counts
# --------------------------------------------------------------------------- #
def test_coerce_counts_drops_non_integer_and_out_of_range():
    out = predict._coerce_counts({
        "parking_vehicle_stalls": 27.62,   # a parking AREA misread as a count -> drop
        "parking_bike_stalls": 12.21,      # a slab dimension misread -> drop
        "number_of_stories": 0,            # not a real storey count -> drop
        "units_total": 9,                  # valid -> keep
        "floor_area": 11.97,               # numeric field, not a count -> untouched
    })
    assert out["parking_vehicle_stalls"] is None
    assert out["parking_bike_stalls"] is None
    assert out["number_of_stories"] is None
    assert out["units_total"] == 9
    assert out["floor_area"] == 11.97


def test_coerce_counts_normalizes_whole_floats():
    out = predict._coerce_counts({"units_total": 61.0, "parking_vehicle_stalls": "10"})
    assert out["units_total"] == 61 and isinstance(out["units_total"], int)
    assert out["parking_vehicle_stalls"] == 10


# --------------------------------------------------------------------------- #
# focused full-sheet passes: each pass scopes a few fields, and together they cover
# the fields we care about (the reliable pattern for a strong VLM vs. spatial tiling)
# --------------------------------------------------------------------------- #
def test_vision_passes_cover_key_fields():
    passes = predict.VISION_PASSES
    assert isinstance(passes, tuple) and 1 <= len(passes) <= 5  # single comprehensive by default
    joined = " ".join(passes).lower()
    for field in ("units", "storeys", "parking", "bicycle", "provided"):
        assert field in joined, f"no pass mentions {field}"
    # must ask for PROVIDED parking, not required (the fix for the 59 vs 4 misread)
    assert any("provided" in p.lower() and "required" in p.lower() for p in passes)
