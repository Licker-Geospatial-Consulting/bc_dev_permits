"""
Tests for the Halton Hills (Ontario) ArcGIS MapServer harvester.

Runs the deterministic mapper against a saved layer-24 /query response, so the test is
hermetic (no network). The four features exercise the mapping branches:

  * D12/24T-86022/H  13423 Highway 7      - Subdivision: lot count -> units_total
  * D12SUB12.001     125 McDonald Blvd.   - Subdivision: "108 townhouse unit" -> unit_mix
  * D09OPA26.001     16768 22 Side Road   - OPA, LAND_USE "Other" -> unknown class, needs_review
  * D09OPA24.001...  159 Confederation St - "81 Townhouses & 1 Single Detached" -> 81 + mix

The _extract_sections and _civic_key helpers (the HTML major-app cross-reference) are tested
directly, since the subpages are fetched over the network and cannot be exercised hermetically.

Run:  python -m pytest -q tests/test_halton_hills.py   (or:  python -m tests.test_halton_hills)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import halton_hills  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _rows():
    data = json.loads((FIX / "halton_hills_query.json").read_text(encoding="utf-8"))
    rows = halton_hills.parse_features(data["features"])
    return {r["permit_id"]: r for r in rows}


def show(row):
    print(json.dumps({k: v for k, v in row.items() if k != "raw_text"}, indent=2, ensure_ascii=False))


def test_subdivision_lot_count_is_units():
    """A subdivision's stated lot count becomes units_total; LAND_USE drives the class."""
    row = _rows()["D12/24T-86022/H"]
    print("\n=== 13423 Highway 7 ===")
    show(row)

    assert row["municipality"] == "halton_hills"
    assert row["address"] == "13423 Highway 7"
    assert row["permit_type"] == "Subdivision"
    assert row["status"] == "Approved (Clearing Conditions)"
    assert row["development_class"] == "residential"   # LAND_USE = Residential
    assert row["units_total"] == 8                     # "8 single detached lot subdivision"
    assert row["extraction_method"] == "arcgis"
    assert row["latitude"] == 43.659346 and row["longitude"] == -79.960796
    assert row["milestones"][0] == {"milestone": "Application Received", "milestone_date": "1986-07-01"}
    assert not row["needs_review"]
    print("PASSED ✓")


def test_townhouse_subdivision_unit_mix():
    """'108 townhouse unit subdivision' yields both a total and a townhouse unit mix."""
    row = _rows()["D12SUB12.001"]
    print("\n=== 125 McDonald Blvd. ===")
    show(row)

    assert row["units_total"] == 108
    assert row["unit_mix"] == {"townhouse": 108}
    assert row["development_class"] == "residential"
    print("PASSED ✓")


def test_opa_other_use_is_unknown_and_flagged():
    """An OPA with LAND_USE 'Other' and prose that states no dwellings stays unknown + needs_review."""
    row = _rows()["D09OPA26.001"]
    print("\n=== 16768 22 Side Road ===")
    show(row)

    assert row["permit_type"] == "OPA"
    assert row["development_class"] == "unknown"       # LAND_USE "Other", no residential prose
    assert row["occupancy_types"] == []
    assert row["units_total"] is None
    assert row["needs_review"] is True
    print("PASSED ✓")


def test_combined_file_numbers_and_split_counts():
    """A combined OPA/ZBA/SUB file keeps its full FILE_NO; '81 Townhouses & 1 Single Detached' splits."""
    row = _rows()["D09OPA24.001, D14ZBA24.008 & D12SUB24.001"]
    print("\n=== 159 Confederation Street ===")
    show(row)

    assert row["permit_type"] == "OPA, ZBA"
    assert row["units_total"] == 81                    # 81 townhouses (map's terse APP_DESC)
    assert row["unit_mix"] == {"townhouse": 81}
    assert row["development_class"] == "residential"
    print("PASSED ✓")


def test_civic_key_matching():
    """_civic_key pulls (civic number, distinctive street token), dropping type/direction suffixes."""
    assert halton_hills._civic_key("13423 Highway 7") == (13423, "highway")
    assert halton_hills._civic_key("125 McDonald Blvd.") == (125, "mcdonald")
    assert halton_hills._civic_key("159 Confederation Street ") == (159, "confederation")
    assert halton_hills._civic_key("16768 22 Side Road ") == (16768, "side")
    assert halton_hills._civic_key(None) == (None, None)
    print("\n_civic_key PASSED ✓")


def test_extract_sections_max_combines_counts():
    """Sibling subpage sections restate totals, so counts combine by MAX, not by sum."""
    sections = [
        "The proposal is for a total of 82 dwelling units.",
        "The 82 units comprise 81 townhouses and 1 single detached dwelling, "
        "with 326 resident parking spaces.",
    ]
    combined = halton_hills._extract_sections(sections)
    print("\n=== _extract_sections ===")
    print(json.dumps(combined, indent=2, ensure_ascii=False))

    assert combined["units_total"] == 82               # max(82, 82), not 82 + 82 + 81 + 1
    assert combined["unit_mix"] == {"townhouse": 81, "detached": 1}
    assert combined["parking_vehicle_stalls"] == 326
    assert combined["development_class"] == "residential"
    assert combined["description"] == sections[0]       # first section is the kept description
    print("PASSED ✓")


if __name__ == "__main__":
    test_subdivision_lot_count_is_units()
    test_townhouse_subdivision_unit_mix()
    test_opa_other_use_is_unknown_and_flagged()
    test_combined_file_numbers_and_split_counts()
    test_civic_key_matching()
    test_extract_sections_max_combines_counts()
    print("\nAll Halton Hills harvester tests passed.")
