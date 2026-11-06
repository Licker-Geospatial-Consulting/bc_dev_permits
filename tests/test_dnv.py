"""District of North Vancouver (JSON API) harvester tests.

The fixture (dnv_harold.json) is a real full-record response from the DNV public API for one
application (1120 Harold Rd). These tests lock in the API-field -> schema mapping: the floor-area-
by-use -> development_class derivation, the per-building storeys table, the FSR computation, the
milestone -> status mapping, and the lat/long passthrough.

Do NOT edit the fixture to make a test pass - fix the parser instead.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import dnv

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HAROLD = json.loads((FIXTURES / "dnv_harold.json").read_text(encoding="utf-8"))
ROW = dnv.parse_record(HAROLD)


def test_core_fields_mapped_from_api():
    assert ROW["municipality"] == "dnv"
    assert ROW["permit_id"] == "PLN2023-00038"
    assert ROW["permit_type"] == "Rezoning with DP (±OCP)"
    assert ROW["development_name"] == "Heritage Cross House"
    assert ROW["address"] == "1120 Harold Rd"            # cleaned from " 1120  HAROLD RD"
    assert ROW["source_url"].endswith("/business-development/1120-harold-road-cross-residence")


def test_program_fields():
    assert ROW["units_total"] == 6
    assert ROW["number_of_stories"] == 2                 # tallest building in the per-building table
    assert ROW["floor_area"] == 859.5
    assert ROW["lot_area"] == 1330.0
    assert ROW["parking_vehicle_stalls"] == 7
    assert ROW["zoning_density"] == "FSR 0.65"           # 859.5 / 1330
    assert ROW["rental_or_strata"] == "strata"           # "all market housing - ownership"
    # unit_mix comes from the "Pl Unit Info Proposed" tenure table (explains totals > "N homes":
    # e.g. elsewhere 46 ownership + 10 lock-off suites = a 56-unit total).
    assert ROW["unit_mix"] == {"Market Housing - Ownership": 6}


def test_class_and_occupancy_from_floor_by_use():
    # Only the townhouse floor-area field is non-zero -> residential.
    assert ROW["development_class"] == "residential"
    assert ROW["occupancy_types"] == ["residential"]
    assert ROW["occupancies"] == [
        {"occupancy": "residential", "floor_area": 859.5, "floor_area_unit": "m2"}
    ]


def test_latlong_passthrough_no_geocoding():
    assert round(ROW["latitude"], 3) == 49.338
    assert round(ROW["longitude"], 3) == -123.043


def test_milestones_and_status():
    ms = {m["milestone"]: m["milestone_date"] for m in ROW["milestones"]}
    assert ms["Application Received"] == "2023-08-04"
    assert ms["Bylaw Adoption"] == "2025-12-08"
    # Status is the furthest-along milestone that has a date.
    assert ROW["status"] == "Bylaw Adoption"


def test_title_address_and_num_helpers():
    assert dnv._title_address(" 1120  HAROLD RD") == "1120 Harold Rd"
    assert dnv._num("0") is None       # a "0" sum means "no area", not zero
    assert dnv._num("859.5") == 859.5
    assert dnv._int(None) is None
