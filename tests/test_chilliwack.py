"""Tests for the City of Chilliwack (Build Chilliwack / WordPress) harvester.

Runs the deterministic mappers against a synthetic project-page fixture (matching the real DOM: an
og:title, a Project Status / Project Type pair, a "Project Details" grid, prose, and a photo gallery)
plus an inline markersData snippet, so the test is hermetic. It locks in the parts that are easy to
get wrong: the markersData parse (+ empty-permalink skip), the Project Details label -> field mapping,
the comma-separated Project Type -> development_class, the title -> address, and the gallery-image
flag that drives the Claude-read unit-mix pass.

Run:  python -m pytest -q            (or:  python -m tests.test_chilliwack)
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import chilliwack as cwk  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
MARKER = {
    "title": "Example Mixed-Use at 45999 Example Street",
    "permalink": "https://buildchilliwack.com/project/example-project/",
    "coordinates": [-121.95, 49.17],
}
ROW = cwk.parse_project((FIX / "chilliwack_project.html").read_text(encoding="utf-8"), MARKER)


def test_markers_parse_and_skip_empty():
    """markersData is read from the map page; a marker with no permalink is dropped."""
    html = ('<script>let markersData = ['
            '{"title":"A","permalink":"https://buildchilliwack.com/project/a/","coordinates":[-1,2]},'
            '{"title":"B","permalink":""}];</script>')
    markers = cwk.parse_markers(html)
    assert len(markers) == 1
    assert markers[0]["permalink"].endswith("/project/a/")


def test_core_fields_from_marker_and_page():
    assert ROW["municipality"] == "chilliwack"
    assert ROW["permit_id"] == "example-project"            # the permalink slug (no permit number)
    assert ROW["source_url"] == MARKER["permalink"]
    assert ROW["development_name"] == MARKER["title"]
    assert ROW["address"] == "45999 Example Street"         # civic address pulled from the title
    assert ROW["latitude"] == 49.17 and ROW["longitude"] == -121.95   # coordinates are [lon, lat]
    assert ROW["status"] == "Under Construction"
    assert ROW["permit_type"] == "Condominium , Commercial"


def test_type_maps_to_class():
    """Two distinct Project Types (Condominium + Commercial) -> mixed, residential+commercial."""
    assert ROW["development_class"] == "mixed"
    assert ROW["occupancy_types"] == ["residential", "commercial"]
    assert cwk.class_from_types("Townhouse", None)[0] == "residential"
    assert cwk.class_from_types("Industrial", None)[0] == "industrial"
    assert cwk.class_from_types("Daycare", None)[0] == "institutional"
    assert cwk.class_from_types("Restaurant", None)[0] == "commercial"


def test_project_details_mapped():
    """The Project Details grid yields storeys / units / height / parking (value numbers extracted)."""
    assert ROW["number_of_stories"] == 6
    assert ROW["units_total"] == 82
    assert ROW["height_m"] == 21.5                          # "21.5m" -> 21.5
    assert ROW["parking_vehicle_stalls"] == 96              # "96 Spaces" -> 96
    assert ROW["parking_notes"] == "Parking: 96 Spaces"
    # "Size: 118,270ft 2" -> floor_area in m2 (the superscript 2 collapses to "ft 2").
    assert ROW["floor_area"] == round(118270 * cwk._SQFT_TO_M2, 1)
    assert ROW["floor_area_unit"] == "m2"
    # Trees and Buildings have no scalar home, but the full block survives in raw_text.
    assert "Trees: 24" in ROW["raw_text"]


def test_address_from_prose_when_title_has_none():
    """A project whose title names no address takes one from the body prose."""
    marker = {"title": "Petro-Canada and Starbucks",
              "permalink": "https://buildchilliwack.com/project/45044-commercial-court/",
              "coordinates": [-121.97, 49.14]}
    html = ("<html><head><meta property='og:title' content='Petro-Canada and Starbucks'></head>"
            "<body><p>The newly completed commercial development at 45044 Commercial Court in "
            "Chilliwack, BC, is now open to the public under Development Permit DP001455.</p>"
            "</body></html>")
    row = cwk.parse_project(html, marker)
    assert row["address"] == "45044 Commercial Court"


def test_nested_permalinks_get_unique_ids():
    """A nested /project/<parent>/<child>/ permalink joins the path so child slugs never collide."""
    base = "https://buildchilliwack.com/project/"
    assert cwk._permit_id(base + "9299-main-street/") == "9299-main-street"   # single segment
    assert cwk._permit_id(base + "vantage/building-1/") == "vantage-building-1"
    assert cwk._permit_id(base + "webb-avenue/building-1/") == "webb-avenue-building-1"
    # The two 'building-1' children resolve to different ids.
    assert cwk._permit_id(base + "vantage/building-1/") != cwk._permit_id(base + "webb-avenue/building-1/")


def test_gallery_and_pdf_flag():
    """Gallery images lose their WordPress size suffix; a site-plan image + missing unit_mix flags it."""
    urls = [d["url"] for d in ROW["documents"]]
    assert "https://buildchilliwack.com/wp-content/uploads/2024/06/example-site-plan.png" in urls
    assert all("-438x292" not in u and "-600x365" not in u for u in urls)   # size suffix stripped
    assert ROW["needs_pdf_extraction"] is True              # unit_mix missing + a site-plan image


if __name__ == "__main__":
    test_markers_parse_and_skip_empty()
    test_core_fields_from_marker_and_page()
    test_type_maps_to_class()
    test_project_details_mapped()
    test_address_from_prose_when_title_has_none()
    test_nested_permalinks_get_unique_ids()
    test_gallery_and_pdf_flag()
    print("\nAll Chilliwack harvester tests passed.")
