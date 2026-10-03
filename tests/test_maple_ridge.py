"""
Tests for the Maple Ridge VertiGIS/ArcGIS harvester.

Runs the deterministic mapper against a saved MapServer response (layer 0 applications +
table 2 ApplicationDetails), so the test is hermetic. The three features exercise the
mapping branches:

  * 2019-255-RZ - Rezoning, multi-family: units + rental + zoning change  (happy path)
  * 2026-094-SD - Subdivision: the "Lots/Units" value is LOTS, not units  (no units_total)
  * 2026-092-DP - Development Permit, multi-family, no unit count          (class from type)

Run:  python -m pytest -q            (or:  python -m tests.test_maple_ridge)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import maple_ridge  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _rows():
    data = json.loads((FIX / "maple_ridge_query.json").read_text(encoding="utf-8"))
    details = maple_ridge.group_details(data["details"])
    rows = maple_ridge.parse_applications(data["applications"], details)
    return {r["permit_id"]: r for r in rows}


def show(row):
    print(json.dumps({k: v for k, v in row.items() if k != "raw_text"}, indent=2, ensure_ascii=False))


def test_rezoning_multifamily():
    """Rezoning with a proposed unit count, rental units, and a zoning change."""
    row = _rows()["2019-255-RZ"]
    print("\n=== 2019-255-RZ ===")
    show(row)

    assert row["municipality"] == "maple_ridge"
    assert row["address"] == "12297 222 ST"            # id prefix stripped, whitespace collapsed
    assert row["permit_type"] == "Rezoning"
    assert row["status"] == "Final Reading"
    assert row["development_class"] == "residential"
    assert row["units_total"] == 106                   # RZ -> "Lots/Units" counts as units
    assert row["rental_or_strata"] == "rental"
    assert row["zoning_density"] == "RS-1 (One Family Urban Residential) -> RM-4 (Multiple Family Residential District)"
    assert row["extraction_method"] == "arcgis"
    assert row["latitude"] == 49.215 and row["longitude"] == -122.595
    assert row["milestones"][0] == {"milestone": "Application Received", "milestone_date": "2019-07-19"}
    print("PASSED ✓")


def test_subdivision_lot_count_is_units():
    """A Subdivision's proposed lot count populates units_total (one dwelling per lot here)."""
    row = _rows()["2026-094-SD"]
    print("\n=== 2026-094-SD ===")
    show(row)

    assert row["permit_type"] == "Subdivision"
    assert row["units_total"] == 2                      # "Approx. Number of Proposed Lots/Units"
    assert row["development_class"] == "residential"    # RS-2 zoning
    assert row["address"] == "23733 FERN CRES"
    assert row["latitude"] is None                      # empty geometry
    # HTML entities in detail values are decoded into raw_text.
    assert "&amp;" not in row["raw_text"] and "Conservation & Open Space" in row["raw_text"]
    print("PASSED ✓")


def test_storeys_and_units_from_prose():
    """Storeys live only in the ApplicationDescription prose; units come from the detail count."""
    row = _rows()["2017-462-RZ"]
    print("\n=== 2017-462-RZ ===")
    show(row)

    assert row["number_of_stories"] == 35              # "35-storey apartment building" (prose)
    assert row["units_total"] == 252                   # structured "Lots/Units"
    assert row["rental_or_strata"] == "strata"         # "252 market strata dwelling units" (prose)
    assert row["development_class"] == "mixed"         # C-3 commercial + apartment residential
    print("PASSED ✓")


def test_development_permit_class_from_type():
    """Class comes from 'Development Permit Type' when prose and zoning are ambiguous."""
    row = _rows()["2026-092-DP"]
    print("\n=== 2026-092-DP ===")
    show(row)

    assert row["permit_type"] == "Development Permit"
    assert row["development_class"] == "residential"   # "Multi Family Residential"
    assert row["units_total"] is None
    # Reading-date strings (YYYYMMDDHHMMSS) become milestones.
    names = {m["milestone"]: m["milestone_date"] for m in row["milestones"]}
    assert names.get("First Reading") == "2026-09-08"
    print("PASSED ✓")


if __name__ == "__main__":
    test_rezoning_multifamily()
    test_subdivision_lot_count_is_units()
    test_storeys_and_units_from_prose()
    test_development_permit_class_from_type()
    print("\nAll Maple Ridge harvester tests passed.")
