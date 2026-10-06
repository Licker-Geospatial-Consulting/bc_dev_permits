"""Tests for the Port Moody ArcGIS + Engage harvester.

Runs the deterministic mappers against a saved fixture (hand-built map features with the real
observed attribute shapes, plus the real Engage listing + project descriptions), so the test is
hermetic. It exercises each branch:

  * ORD00008 - MASTER FOLDER, empty Purpose: every development field comes from the matched
    Engage project (units/storeys/parking), and the duplicated milestone rows collapse.
  * 2331-2335 St Johns - four sibling applications across two addresses that match one Engage
    project consolidate into one geocodable record (units 48, FAR 2.63, 885 m2 commercial).
  * DVP00027 - a small file whose own Purpose carries the fields and has no Engage project.
  * address <-> Engage slug matching, including the non-matches that must stay unmatched.

Run:  python -m pytest -q            (or:  python -m tests.test_port_moody)
"""
import json
from pathlib import Path
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import port_moody as pm  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _fixture():
    return json.loads((FIX / "port_moody_query.json").read_text(encoding="utf-8"))


def _base_rows(fx):
    """Base (pre-enrichment) rows, keyed by permit_id."""
    milestones = pm.group_milestones(fx["milestones"])
    rows = pm.parse_applications(fx["applications"], milestones)
    return {r["permit_id"]: r for r in rows}


def _index(fx):
    """Build the Engage address index from the fixture listing (no network), as the harvester does."""
    idx = {}
    for a in BeautifulSoup(fx["engage_projects_html"], "lxml").find_all("a", href=True):
        slug = a["href"].strip("/")
        if any(c.isdigit() for c in slug):
            names, nums, ranges = pm._slug_signature(slug)
            if nums:
                idx[slug] = {"title": a.get_text(" ", strip=True), "names": names,
                             "nums": nums, "ranges": ranges}
    return idx


def test_dedupe_and_milestones():
    """Two features share ORD00008 -> one row; duplicate milestone rows collapse; order is by date."""
    fx = _fixture()
    rows = pm.parse_applications(fx["applications"], pm.group_milestones(fx["milestones"]))
    assert sum(1 for r in rows if r["permit_id"] == "ORD00008") == 1

    ord8 = _base_rows(fx)["ORD00008"]
    assert ord8["latitude"] is not None  # the geometry-bearing feature won dedupe
    names = [m["milestone"] for m in ord8["milestones"]]
    assert names == ["Application Received", "APPLICATION SUBMISSION", "1ST READING"]  # deduped, sorted
    dates = [m["milestone_date"] for m in ord8["milestones"]]
    assert dates == sorted(dates)


def test_address_matching():
    """Street token + civic number match the right Engage slug; non-addresses stay unmatched."""
    fx = _fixture()
    idx = _index(fx)

    spring = fx["slugs"]["spring_electronic"]
    assert pm.match_engage_slug("3060 SPRING ST", idx) == spring
    assert pm.match_engage_slug("80 ELECTRONIC AVE", idx) == spring  # the other site in the same project
    assert pm.match_engage_slug("2325 ST JOHNS ST", idx) == fx["slugs"]["st_johns"]  # 2325 ~ 2331 range
    assert pm.match_engage_slug("832 BALLANTRAE CRT", idx) is None  # no Engage project
    # A year number alone (the 2026 budget page) never matches a street address.
    assert "2026-budget-consultation" in idx
    assert pm.match_engage_slug("2026 CLARKE ST", idx) is None


def test_engage_enriches_master_folder():
    """ORD00008 has no Purpose: units/storeys/parking/class all come from the Engage description."""
    fx = _fixture()
    row = _base_rows(fx)["ORD00008"]
    assert row["units_total"] is None and row["number_of_stories"] is None
    assert row["extraction_method"] == "arcgis"

    assert pm.apply_engage(row, fx["slugs"]["spring_electronic"],
                           fx["engage_descriptions"][fx["slugs"]["spring_electronic"]])
    pm.finalize(row)

    assert row["units_total"] == 983
    assert row["number_of_stories"] == 38            # tallest tower
    assert row["parking_vehicle_stalls"] == 868
    assert row["development_class"] == "mixed"
    assert row["occupancy_types"] == ["residential", "commercial"]
    assert row["rental_or_strata"] == "strata"
    assert row["extraction_method"] == "arcgis+engage"
    assert row["needs_review"] is True
    assert row["enrichment_url"].endswith(fx["slugs"]["spring_electronic"])  # Engage source recorded
    assert "engage.portmoody.ca" in row["raw_text"]
    assert row["unit_mix"] is None                   # parking-prose misparse not overlaid
    assert "industrial" not in row["occupancy_types"]


def test_consolidates_sibling_applications():
    """Four St Johns folders at one Engage project collapse into one enriched, geocodable record."""
    fx = _fixture()
    rows = _base_rows(fx)
    slug = fx["slugs"]["st_johns"]
    group = [rows[pid] for pid in ("ORD00006", "SA000076", "SA000047", "TUP00026")]

    primary, secondaries = pm.consolidate_group(group, slug, fx["engage_descriptions"][slug])
    pm.finalize(primary)

    # The Engage page names ORD00006 as its application, so it is the primary.
    assert primary["permit_id"] == "ORD00006"
    assert {s["permit_id"] for s in secondaries} == {"SA000076", "SA000047", "TUP00026"}

    # Rich fields extracted from the (single-copy) Engage prose - no doubling (48, not 96).
    assert primary["units_total"] == 48
    assert primary["number_of_stories"] == 6
    assert primary["development_class"] == "mixed"
    assert primary["rental_or_strata"] == "rental"
    assert primary["zoning_density"] == "FSR 2.63"
    assert primary["floor_area"] == 55206.0 and primary["floor_area_unit"] == "m2"
    # The 885 m2 commercial sub-area rides along as a structured occupancy.
    commercial = [o for o in primary["occupancies"] if o["occupancy"] == "commercial"]
    assert commercial and commercial[0]["floor_area"] == 885.0

    # Siblings are preserved on the primary, not stored as duplicate rows.
    rel_ids = {r["permit_id"] for r in primary["related_permits"]}
    assert rel_ids == {"SA000076", "SA000047", "TUP00026"}

    # Primary stays geocodable even though ORD00006 has no polygon (inherited from a sibling).
    assert primary["latitude"] is not None
    assert primary["enrichment_url"].endswith(slug)  # Engage source recorded on the row

    # Milestones from every folder merge, deduped and date-ordered.
    names = [m["milestone"] for m in primary["milestones"]]
    assert "1ST READING" in names and "APPLICATION SUBMISSION" in names
    dates = [m["milestone_date"] for m in primary["milestones"]]
    assert dates == sorted(dates)
    assert primary["extraction_method"] == "arcgis+engage"


def test_small_file_uses_own_purpose():
    """DVP00027 parses its own Purpose and matches no Engage project (stays 'arcgis')."""
    fx = _fixture()
    idx = _index(fx)
    row = _base_rows(fx)["DVP00027"]
    assert pm.match_engage_slug(row["address"], idx) is None
    pm.finalize(row)
    assert row["permit_type"] == "DEVELOPMENT VARIANCE PERMIT"
    assert row["extraction_method"] == "arcgis"
    assert row["address"] == "832 BALLANTRAE CRT"    # city/postal tail dropped
    assert row["latitude"] is not None


if __name__ == "__main__":
    test_dedupe_and_milestones()
    test_address_matching()
    test_engage_enriches_master_folder()
    test_consolidates_sibling_applications()
    test_small_file_uses_own_purpose()
    print("\nAll Port Moody harvester tests passed.")
