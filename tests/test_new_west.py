"""
Tests for the City of New Westminster ArcGIS "Projects on the Go" harvester.

Runs the deterministic mapper against a saved POG FeatureServer /query response, so the test is
hermetic (no network). The three features exercise the mapping branches:

  * Item 24  102-128 E Eighth Ave / Cumberland  - Residential, units from the description, Be Heard link
  * Item 65  616-640 Sixth St                   - Mixed Use, storeys + rental from the description
  * Item 29  404 Second St                       - Commercial, Heritage files, no Be Heard link

The Be Heard enrichment itself renders a JavaScript page, so it cannot run hermetically; its pure
helpers (_file_numbers, _permit_type, _beheard_description) are tested directly.

Run:  python -m pytest -q tests/test_new_west.py   (or:  python -m tests.test_new_west)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import new_west  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _rows():
    data = json.loads((FIX / "new_west_query.json").read_text(encoding="utf-8"))
    return {r["permit_id"]: r for r in new_west.parse_features(data["features"])}


def show(row):
    print(json.dumps({k: v for k, v in row.items() if k != "raw_text"}, indent=2, ensure_ascii=False))


def test_residential_files_and_units():
    """Files become the permit id + type; the description's unit count is parsed; link is kept."""
    row = _rows()["PAR01366, RZ000204, DP000860"]
    print("\n=== Cumberland ===")
    show(row)

    assert row["municipality"] == "new_west"
    assert row["address"] == "102-128 E Eighth Ave & 721 Cumberland St"
    assert row["permit_type"] == "Pre-Application Review, Rezoning, Development Permit"
    assert row["development_class"] == "residential"       # Project_Type "Residential - above 6 units"
    assert row["units_total"] == 55                         # "55 unit ... development"
    assert row["status"] == "D1 Rezoning/HRA and/or OCP Amendment Approved"
    assert row["extraction_method"] == "arcgis"            # before Be Heard enrichment
    assert row["latitude"] is not None and row["longitude"] is not None
    assert row["milestones"][0]["milestone"] == "Last Updated"
    assert row["_beheard_url"] == "https://www.beheardnewwest.ca/cumberland-st"
    print("PASSED ✓")


def test_mixed_use_rental_from_description():
    """A mixed-use project's storeys, unit count and rental tenure come from the prose."""
    row = _rows()["RZ000147, RZ000223, DPU00065"]
    print("\n=== 616-640 Sixth St ===")
    show(row)

    assert row["development_class"] == "mixed"             # Project_Type "Mixed Use"
    assert row["units_total"] == 338
    assert row["number_of_stories"] == 29
    assert row["rental_or_strata"] == "rental"
    assert row["rental_subtype"] == "below-market rental"
    print("PASSED ✓")


def test_commercial_heritage_no_link():
    """A Heritage-only commercial project has no program and no Be Heard link to enrich from."""
    row = _rows()["HER00752, HER00753"]
    print("\n=== 404 Second St ===")
    show(row)

    assert row["permit_type"] == "Heritage"
    assert row["development_class"] == "commercial"
    assert row["units_total"] is None
    assert row["_beheard_url"] is None                    # no link -> enrichment skips it
    print("PASSED ✓")


def test_file_numbers_and_permit_type():
    """Files split into ordered file numbers; their prefixes map to the combined permit type."""
    files = new_west._file_numbers("PAR01366\nRZ000204\nDP000860")
    assert files == ["PAR01366", "RZ000204", "DP000860"]
    assert new_west._permit_type(files) == "Pre-Application Review, Rezoning, Development Permit"
    # Duplicate prefixes collapse: two DPs are one "Development Permit".
    assert new_west._permit_type(["DP000860", "DPU00065"]) == "Development Permit"
    print("\n_file_numbers PASSED ✓")


def test_beheard_description_extraction():
    """The narrative is pulled from 'What's being proposed?', dropping nav and the engage footer."""
    body = ("Skip to main content Accept Back to folder Follow (3) 102 Cumberland "
            "Project Update: What's being proposed? 55 townhouse units with an FSR of 1.0 and "
            "88 parking spaces. How can I engage? Please give feedback. Documents Project Contact")
    desc = new_west._beheard_description(body)
    assert desc == "55 townhouse units with an FSR of 1.0 and 88 parking spaces."
    assert "Skip to main content" not in desc and "How can I engage" not in desc
    print("\n_beheard_description PASSED ✓")


if __name__ == "__main__":
    test_residential_files_and_units()
    test_mixed_use_rental_from_description()
    test_commercial_heritage_no_link()
    test_file_numbers_and_permit_type()
    test_beheard_description_extraction()
    print("\nAll New Westminster harvester tests passed.")
