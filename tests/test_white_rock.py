"""Tests for the City of White Rock ArcGIS harvester.

Runs the deterministic mappers against a saved fixture (four real application features with their
real attribute shapes + geometry, plus the real Milestones-table rows), so the test is hermetic.
It exercises each branch:

  * OCP00011 (23-016) - a strong OCP/Zoning file whose PURPOSE states the program (100 rental
    units, 6 storeys, mixed use), with the DVP00045 variance at the same civic address folding in.
  * ZON00016 (19-017) - a standalone Zoning Amendment whose own PURPOSE carries 87 units / 4 storeys.
  * BOV00046 (26-032) - a thin Board of Variance appeal: no program, class unknown, needs_review.
  * CIVIC_ADDRESS tail-dropping, permit_type from the TYPE attribute, and the Milestones-table
    join (dedup + date order, with the map's own Application date).

Run:  python -m pytest -q            (or:  python -m tests.test_white_rock)
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import consolidate  # noqa: E402
from bc_dev_permits.harvesters import white_rock as wr  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _fixture():
    return json.loads((FIX / "white_rock_query.json").read_text(encoding="utf-8"))


def _rows_by_id(fx):
    """Base (pre-consolidation) rows, keyed by permit_id."""
    milestones = wr.group_milestones(fx["milestones"])
    rows = []
    for feat in wr.dedupe_by_folder(fx["applications"]):
        folder = feat["attributes"]["FOLDER_NUMBER"]
        rows.append(wr.row_from_feature(feat, milestones.get(folder, [])))
    return {r["permit_id"]: r for r in rows}


def test_core_attribute_mapping():
    """permit_id from FILE_NUMBER, permit_type from TYPE, status + CIVIC_ADDRESS (tail dropped)."""
    row = _rows_by_id(_fixture())["19-017"]
    assert row["municipality"] == "white_rock"
    assert row["permit_id"] == "19-017"                 # FILE_NUMBER, not the FOLDER_NUMBER
    assert row["permit_type"] == "ZONING AMENDMENT"     # the TYPE attribute, not parsed prose
    assert row["status"] == "IN-PROGRESS"
    assert row["address"] == "15704 NORTH BLUFF RD"     # CIVIC_ADDRESS, "WHITE ROCK BC ..." tail dropped
    assert row["latitude"] is not None and row["longitude"] is not None
    assert row["extraction_method"] == "arcgis"
    assert row["source_url"].endswith("0083423c595b431e80c8575fdcef4ed1/page/Page")


def test_purpose_parsed_into_program():
    """A strong file's PURPOSE yields units / storeys / class / tenure."""
    rows = _rows_by_id(_fixture())

    zon = rows["19-017"]
    assert zon["development_class"] == "residential"
    assert zon["units_total"] == 87
    assert zon["number_of_stories"] == 4

    ocp = rows["23-016"]
    assert ocp["development_class"] == "mixed"
    assert ocp["units_total"] == 100
    assert ocp["number_of_stories"] == 6
    assert ocp["rental_or_strata"] == "rental"
    assert ocp["is_parsed"] is True


def test_thin_variance_left_unknown():
    """A Board of Variance appeal states no program: class unknown, flagged for review, not guessed."""
    row = _rows_by_id(_fixture())["26-032"]
    assert row["permit_type"] == "BOARD OF VARIANCE APPEAL"
    assert row["development_class"] == "unknown"
    assert row["units_total"] is None and row["number_of_stories"] is None
    assert row["needs_review"] is True


def test_milestones_joined_deduped_ordered():
    """The Milestones table joins on FOLDER_NUMBER; the map's Application date leads, dates sorted."""
    row = _rows_by_id(_fixture())["19-017"]
    names = [m["milestone"] for m in row["milestones"]]
    dates = [m["milestone_date"] for m in row["milestones"]]
    assert names[0] == "Application Received"            # from Application_Date (epoch ms)
    assert "PUBLIC HEARING / NOTIFICATION" in names      # from the related Milestones table
    assert dates == sorted(dates)                        # date-ordered
    assert len(names) == len(set(zip(names, dates, strict=True)))   # (name, date) deduped


def test_same_address_variance_folds_into_strong_record():
    """DVP00045 (26-002) at 14780 North Bluff folds into the OCP file; its milestones merge."""
    fx = _fixture()
    milestones = wr.group_milestones(fx["milestones"])
    rows = [wr.row_from_feature(f, milestones.get(f["attributes"]["FOLDER_NUMBER"], []))
            for f in wr.dedupe_by_folder(fx["applications"])]
    consolidate.consolidate_by_address(rows)

    by = {r["permit_id"]: r for r in rows}
    assert "26-002" not in by                            # the thin variance was folded away
    primary = by["23-016"]                               # the strong OCP file absorbs it
    assert primary["related_permits"][0]["permit_id"] == "26-002"
    # Milestones from both folders survive on the primary, deduped and date-ordered.
    dates = [m["milestone_date"] for m in primary["milestones"]]
    assert dates == sorted(dates)
    # 14693 West Beach (no sibling) and 15704 North Bluff (distinct program) stay separate.
    assert "26-032" in by and "19-017" in by


if __name__ == "__main__":
    test_core_attribute_mapping()
    test_purpose_parsed_into_program()
    test_thin_variance_left_unknown()
    test_milestones_joined_deduped_ordered()
    test_same_address_variance_folds_into_strong_record()
    print("\nAll White Rock harvester tests passed.")
