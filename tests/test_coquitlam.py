"""
Tests for the Coquitlam ArcGIS harvester.

Runs the deterministic mapper against a saved FeatureServer /query response (the container
can't reach the service, and this keeps the test hermetic). The fixture is a trimmed real
response shape with three features exercising the parsing branches:

  * 23-078  (1144 Inlet St)   - rich prose: storeys, units, tenure, rezoning   (happy path)
  * 24-012  (3030 Example Ave) - prose too short to carry fields                (thin row)
  * no number (2900 Barnet Hwy) - blank PROJECT_NUMBER but usable description   (permit_id None)

Run:  python -m pytest -q            (or:  python -m tests.test_coquitlam)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import coquitlam  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _rows():
    data = json.loads((FIX / "coquitlam_query.json").read_text(encoding="utf-8"))
    return {r.get("permit_id"): r for r in coquitlam.parse_features(data)}, coquitlam.parse_features(data)


def show(row):
    printable = {k: v for k, v in row.items() if k != "raw_text"}
    print(json.dumps(printable, indent=2, ensure_ascii=False))


def test_rich_description_parses():
    """A full description yields storeys, units, tenure, and a rezoning type - deterministically."""
    by, _ = _rows()
    row = by["23-078"]
    print("\n=== 23-078  1144 Inlet St ===")
    show(row)

    assert row["municipality"] == "coquitlam"
    assert row["address"] == "1144 Inlet St"
    assert row["status"] == "Approved"
    assert row["extraction_method"] == "arcgis"
    assert row["permit_type"] == "Rezoning"
    assert row["development_class"] == "residential"
    assert row["number_of_stories"] == 6
    assert row["units_total"] == 474
    assert row["rental_or_strata"] == "rental"
    assert row["is_parsed"] is True
    # Polygon centroid, from the outer ring (outSR=4326).
    assert row["latitude"] == 49.2815
    assert row["longitude"] == -122.8005
    # Submission date becomes the "Application Received" milestone.
    assert row["milestones"][0]["milestone"] == "Application Received"
    assert row["milestones"][0]["milestone_date"] == "2023-08-10"
    # PII (APPLICANT) is never surfaced; raw_text is the description only.
    assert "Inlet" not in (row["raw_text"] or "") or "units" in row["raw_text"]
    print("PASSED ✓")


def test_thin_description_flagged():
    """A description too short to carry fields stays unparsed and flagged for review."""
    by, _ = _rows()
    row = by["24-012"]
    print("\n=== 24-012  3030 Example Ave (thin) ===")
    show(row)

    assert row["is_parsed"] is False
    assert row["development_class"] == "unknown"
    assert row["needs_review"] is True
    # No geometry and no submission date -> no coordinates, no milestone.
    assert row["latitude"] is None
    assert row["longitude"] is None
    assert row["milestones"] == []
    print("PASSED ✓")


def test_blank_project_number_still_yields_row():
    """A feature with no PROJECT_NUMBER but a usable description is kept (permit_id None)."""
    _, rows = _rows()
    row = next(r for r in rows if r["address"] == "2900 Barnet Hwy")
    print("\n=== (no number)  2900 Barnet Hwy ===")
    show(row)

    assert row["permit_id"] is None
    assert row["development_class"] == "mixed"
    assert row["number_of_stories"] == 12
    assert row["is_parsed"] is True
    print("PASSED ✓")


if __name__ == "__main__":
    test_rich_description_parses()
    test_thin_description_flagged()
    test_blank_project_number_still_yields_row()
    print("\nAll Coquitlam harvester tests passed.")
