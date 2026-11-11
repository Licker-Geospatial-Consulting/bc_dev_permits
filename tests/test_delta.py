"""Tests for the City of Delta (Let's Talk Delta / EngagementHQ) harvester.

The fixture (delta_project.html) is a trimmed, synthetic reproduction of a Delta building-application
page - a hive-block-content description, an Application Process timeline ending in "Building Permit",
and a Document Library. It locks in the parts that are easy to get wrong:

  * the Building-Permit timeline filter (the rule that selects a building development application),
  * reading the REVISED program (67 units, 115 stalls) and never the stale Original Overview (65 / 143),
  * the "from A to B" revision and address-in-prose cleaning, and the number-first FSR form,
  * milestone / document parsing (dedup, role, the " PDF (size)" suffix).

Run:  python -m pytest -q            (or:  python -m tests.test_delta)
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import delta

FIX = Path(__file__).parent / "fixtures"


def _soup():
    return delta._soup((FIX / "delta_project.html").read_text(encoding="utf-8"))


ROW = delta.parse_project(_soup(), "lu009999", "Example Mixed-Use Building at 100 Example Street")


def test_building_permit_filter():
    """A timeline with a Building Permit stage passes; one without it does not."""
    assert delta.has_building_permit(_soup()) is True
    no_bp = delta._soup(
        '<div class="timeline-item"><p class="sr-only">Timeline item 1 - active</p>'
        '<p class="ljs">Construction</p></div>')
    assert delta.has_building_permit(no_bp) is False


def test_core_fields():
    assert ROW["municipality"] == "delta"
    assert ROW["permit_id"] == "LU009999"                       # File Number line
    assert ROW["address"] == "100 Example Street and 102, 104 Example Street"
    assert ROW["source_url"] == "https://letstalk.delta.ca/lu009999"
    assert ROW["permit_type"] == "Development Application"
    assert ROW["development_class"] == "mixed"
    assert ROW["occupancy_types"] == ["residential", "commercial"]


def test_revised_program_not_original():
    """The revised figures are read; the stale Original Application Overview is never parsed."""
    assert ROW["units_total"] == 67          # revised, not the original 65
    assert ROW["number_of_stories"] == 6     # revised, not the original 5
    assert ROW["parking_vehicle_stalls"] == 115   # revised, not the original 143
    assert ROW["parking_bike_stalls"] == 90
    assert ROW["floor_area"] == 10842.0      # revised residential GFA, not the original 6,467
    assert ROW["height_m"] == 23.0
    assert ROW["zoning_density"] == "FSR 1.36"    # "from 0.98 to 1.36" -> the final value


def test_fsr_number_first_and_guard():
    """FSR parses whether the number leads or trails, and an implausible value is rejected."""
    assert delta._delta_fsr("2.95 Floor Space Ratio (FSR) 303 residential parking spaces") == 2.95
    assert delta._delta_fsr("a floor space ratio of 3.09.") == 3.09
    assert delta._delta_fsr("Floor Space Ratio (FSR) 303 residential parking spaces") is None


def test_milestones_and_status():
    ms = {m["milestone"]: m["milestone_date"] for m in ROW["milestones"]}
    assert ms["Original Application - LU009990"] == "2024-01-05"
    assert ms["Public Information Meeting #1"] == "2024-06-04"   # weekday + "(Completed)" tolerated
    assert ROW["status"] == "3rd Reading Conditions"            # the active stage
    assert ROW["needs_pdf_extraction"] is True                  # unit_mix missing + drawings present


def test_documents():
    docs = ROW["documents"]
    assert len(docs) == 4                                       # the duplicated link collapses to one
    by_title = {d["title"]: d["doc_role"] for d in docs}
    assert by_title["Architectural Plans - Updated - LU009999"] == "drawings"   # " PDF (62.06 MB)" dropped
    assert by_title["LU009999 - New Application Report"] == "staff_report"
    assert by_title["LU009999 Resident Notice.pdf"] == "notice"
    assert all(d["url"].startswith("https://letstalk.delta.ca/download_file/") for d in docs)


def test_development_candidates():
    """Only Development-category cards become candidates (a Parks card is skipped)."""
    listing = delta._soup(
        '<div class="project card" data-project-category=\'["Development"]\' '
        'data-project-name="The Park"><a href="https://letstalk.delta.ca/lu009764">x</a></div>'
        '<div class="project card" data-project-category=\'["Parks"]\' '
        'data-project-name="Dog Parks"><a href="https://letstalk.delta.ca/dogparks">y</a></div>')
    cands = delta.development_candidates(listing)
    assert [c["slug"] for c in cands] == ["lu009764"]
    assert cands[0]["name"] == "The Park"


if __name__ == "__main__":
    test_building_permit_filter()
    test_core_fields()
    test_revised_program_not_original()
    test_fsr_number_first_and_guard()
    test_milestones_and_status()
    test_documents()
    test_development_candidates()
    print("\nAll Delta harvester tests passed.")
