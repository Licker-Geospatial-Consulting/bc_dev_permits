"""Tests for the City of Abbotsford (SSRS 'Instream Development Applications') harvester.

Runs the deterministic parse against a synthetic SSRS XML export fixture (matching the real namespace
+ attribute set), so the test is hermetic. It locks in the parts that are easy to get wrong: the
quantitative columns -> occupancies / floor_area / units / class, residential lots staying out of
units_total, the multi-type permit_type, KEY_DATES -> milestones, the NULL CIVIC placeholder address,
and that EMAIL / PHONE1 never reach the row.

Run:  python -m pytest -q            (or:  python -m tests.test_abbotsford)
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import abbotsford as abb  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "abbotsford_export.xml"
ROWS = {r["permit_id"]: r for r in abb.parse_export(FIX.read_text(encoding="utf-8"))}


def test_all_rows_parsed():
    assert set(ROWS) == {"PRJ20-005", "PRJ17-001", "PRJ20-060", "PRJ18-002"}
    for r in ROWS.values():
        assert r["municipality"] == "abbotsford"
        assert r["source_url"].endswith("InstreamDevelopmentApplications.aspx")


def test_mixed_use_row():
    """Commercial floor area + apartment units -> mixed, with both an occupancy and a unit count."""
    r = ROWS["PRJ20-005"]
    assert r["address"] == "33816 South Fraser Way"
    assert r["development_class"] == "mixed"
    assert r["occupancy_types"] == ["commercial", "residential"]
    assert r["floor_area"] == 469.0 and r["floor_area_unit"] == "m2"
    assert r["units_total"] == 78 and r["unit_mix"] == {"apartment": 78}
    assert {"occupancy": "commercial", "floor_area": 469.0, "floor_area_unit": "m2"} in r["occupancies"]
    assert "Rezoning" in r["permit_type"] and "Development Permit" in r["permit_type"]


def test_subdivision_lots_not_counted_as_units():
    """New SF lots land in unit_mix but NOT units_total (a lot is not a dwelling)."""
    r = ROWS["PRJ17-001"]
    assert r["development_class"] == "residential"
    assert r["unit_mix"] == {"single-family-lot": 12}
    assert r["units_total"] is None
    assert r["permit_type"] == "Subdivision"


def test_industrial_area_and_milestones():
    r = ROWS["PRJ20-060"]
    assert r["development_class"] == "industrial"
    assert r["floor_area"] == 25288.5
    # Application Received (MM/DD/YY) + a KEY_DATES line (Mon DD, YYYY) both parse to ISO dates.
    r2 = ROWS["PRJ20-005"]
    dates = {m["milestone"]: m["milestone_date"] for m in r2["milestones"]}
    assert dates["Application Received"] == "2020-01-13"
    assert dates["PLA Issuance Date"] == "2022-05-21"


def test_placeholder_address_and_no_pii():
    """NULL CIVIC -> no address; a metric-less rezoning falls back to a description class; no PII."""
    r = ROWS["PRJ18-002"]
    assert r["address"] is None
    assert r["permit_type"] == "Rezoning"
    # EMAIL / PHONE1 are never carried onto any row.
    blob = str(ROWS)
    assert "@b.com" not in blob and "555-1234" not in blob


if __name__ == "__main__":
    test_all_rows_parsed()
    test_mixed_use_row()
    test_subdivision_lots_not_counted_as_units()
    test_industrial_area_and_milestones()
    test_placeholder_address_and_no_pii()
    print("\nAll Abbotsford harvester tests passed.")
