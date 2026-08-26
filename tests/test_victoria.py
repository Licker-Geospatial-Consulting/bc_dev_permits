"""City of Victoria (Prospero tracker) harvester tests.

Fixtures are trimmed-but-real HTML captured from tender.victoria.ca:
  victoria_list.html   - first four ACTIVE result rows + pager + hidden form fields
  victoria_detail.html - one application's addresses/status/tasks + a related REZ block

Do NOT edit the fixtures to make a test pass - fix the parser instead.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import victoria  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIST_HTML = (FIXTURES / "victoria_list.html").read_text(encoding="utf-8")
DETAIL_HTML = (FIXTURES / "victoria_detail.html").read_text(encoding="utf-8")


def _rows():
    return victoria.parse_list(LIST_HTML)


def test_list_yields_one_row_per_result():
    rows = _rows()
    assert len(rows) == 4
    assert [r["permit_id"] for r in rows] == ["DPV00312", "DPV00307", "DPV00303", "DPV00305"]


def test_core_fields_from_list_row():
    row = _rows()[0]
    assert row["municipality"] == "victoria"
    assert row["permit_id"] == "DPV00312"
    assert row["address"] == "512 PEMBROKE ST"  # wide padding collapsed
    assert (
        row["source_url"]
        == "https://tender.victoria.ca/webapps/ourcity/Prospero/Details.aspx?folderNumber=DPV00312"
    )
    assert row["extraction_method"] == "html"
    assert row["is_parsed"] is True
    assert row["status"] == "ACTIVE"  # the list is ACTIVE-filtered


def test_permit_type_comes_from_tracker_field_not_prose():
    # Every row states its type explicitly; we keep that verbatim rather than the
    # prose-regex guess (which would flatten it to plain "Development Permit").
    assert all(r["permit_type"] == "Development Permit with Variance" for r in _rows())


def test_storeys_and_class_parsed_from_purpose():
    by_id = {r["permit_id"]: r for r in _rows()}
    # "...mixed use thirteen storey building with ground floor commercial uses..."
    assert by_id["DPV00307"]["number_of_stories"] == 13
    assert by_id["DPV00307"]["development_class"] == "mixed"


def test_detail_returns_all_primary_addresses():
    d = victoria.parse_detail(DETAIL_HTML, "x")
    assert d["addresses"][0] == "846 BROUGHTON ST"
    assert len(d["addresses"]) == 9  # one application spanning nine lots
    assert all("FORT ST" in a or "BROUGHTON ST" in a for a in d["addresses"])


def test_detail_status_and_milestone():
    d = victoria.parse_detail(DETAIL_HTML, "x")
    assert d["status"] == "ACTIVE"
    assert {"milestone": "Application Received", "milestone_date": "2025-08-06"} in d["milestones"]


def test_related_applications_are_excluded():
    # The fixture carries two related REZ blocks (one ARCHIVED, dated 2017). They must be
    # stripped before parsing so their status/dates never leak into this permit.
    from bs4 import BeautifulSoup

    assert "ARCHIVED" in DETAIL_HTML and "2017" in DETAIL_HTML  # present in the source
    soup = BeautifulSoup(DETAIL_HTML, "lxml")
    assert soup.select("div.related-project")
    victoria._drop_related(soup)
    assert not soup.select("div.related-project")
    assert "ARCHIVED" not in soup.get_text()
    assert "2017" not in soup.get_text()
    # end to end: the parsed permit stays ACTIVE with no 2017 milestone
    d = victoria.parse_detail(DETAIL_HTML, "x")
    assert d["status"] == "ACTIVE"
    assert not any("2017" in (m["milestone_date"] or "") for m in d["milestones"])


def test_merge_detail_keeps_primary_address_and_preserves_full_list():
    row = _rows()[0]
    victoria._merge_detail(row, victoria.parse_detail(DETAIL_HTML, "x"))
    assert row["address"] == "846 BROUGHTON ST"  # primary becomes the detail's first
    assert "899 FORT ST" in row["raw_text"]  # every address preserved for provenance


def test_advertised_pages_read_from_pager():
    # The fixture keeps the real pager (pagination(1..5) controls).
    assert victoria._advertised_pages(LIST_HTML) >= 5
    assert victoria._advertised_pages("<html><body>no pager</body></html>") == 0


@pytest.mark.parametrize(
    ("prose", "expected_units", "expected_class"),
    [
        ("129 new purpose built rental units.", 129, "residential"),
        ("a 6 story, 51 unit multi-family development.", 51, "residential"),
        ("proposal for 2 triplex buildings.", 6, "residential"),
        ("a 3 storey, 3 unit strata houseplex.", 3, "residential"),
    ],
)
def test_victoria_prose_examples(prose, expected_units, expected_class):
    from bc_dev_permits import features

    out = features.extract_all(prose)
    assert out["units_total"] == expected_units
    assert out["development_class"] == expected_class
