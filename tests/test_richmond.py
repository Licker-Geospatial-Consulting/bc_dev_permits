"""City of Richmond (single current-development PDF) harvester tests.

The fixture is a trimmed-but-real two-page slice of the city's current-development PDF
(richmond_current_development.pdf): page 1 and page 9 of the live document. Between them they
exercise the two parsing subtleties:

  * a permit's folder number sits on the BOTTOM line of its block, with the Folder Type and
    Description wrapping upward above it (26-021946, a two-line block), and
  * folder-shaped tokens inside the Description prose ("RZ 21-930951" on page 9) must not be read
    as row anchors.

Do NOT edit the fixture to make a test pass - fix the parser instead.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import richmond

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PDF_BYTES = (FIXTURES / "richmond_current_development.pdf").read_bytes()
ROWS = richmond.parse_pdf(PDF_BYTES)
BY_ID = {r["permit_id"]: r for r in ROWS}


# --- row banding & anchors --------------------------------------------------
def test_every_row_has_a_folder_number():
    # 29 rows per page over two pages; a spurious anchor (from an in-prose "RZ 21-930951") or a
    # dropped line would change this count or leave an empty permit_id.
    assert len(ROWS) == 58
    assert all(r["permit_id"] for r in ROWS)
    assert all(r["municipality"] == "richmond" for r in ROWS)


def test_in_prose_folder_tokens_are_not_rows():
    # Page 9's 23-016766 description contains "RZ 21-930951 and SD 21-930952"; neither becomes a row.
    assert "21-930951" not in BY_ID
    assert "21-930952" not in BY_ID
    assert "RZ 21-930951" in (BY_ID["23-016766"]["raw_text"] or "")


def test_upward_wrapping_block_keeps_its_own_cells():
    # 26-021946 is a two-line block: Folder Type "Development Variance" / "Permit" and a Description
    # that wraps across both lines, with the folder number on the lower line. The adjacent rows
    # above (26-022329) and the type must not bleed in.
    row = BY_ID["26-021946"]
    assert row["address"] == "13888 Wireless Way"
    assert row["permit_type"] == "Development Variance Permit"
    assert row["status"] == "In Circulation"
    assert row["development_class"] == "industrial"
    assert row["number_of_stories"] == 3
    assert "three-storey light industrial building" in row["raw_text"]


# --- column -> field mapping ------------------------------------------------
def test_columns_map_to_fields():
    row = BY_ID["26-022329"]
    assert row["address"] == "6660 Francis Rd"
    assert row["permit_type"] == "Development Permit"
    assert row["status"] == "Review"
    assert row["units_total"] == 4
    assert row["development_class"] == "residential"
    assert row["source_url"] == richmond.PDF_URL
    assert row["extraction_method"] == "pdf"


def test_application_acceptance_becomes_milestone():
    row = BY_ID["26-022329"]
    assert row["milestones"] == [
        {"milestone": "Application Accepted", "milestone_date": "2026-09-28"}
    ]


def test_tallest_element_and_mixed_use_from_prose():
    # 5300 No 3 Rd DP: "three buildings (6-14 storeys)" -> tallest 14, residential + commercial.
    row = BY_ID["23-011558"]
    assert row["number_of_stories"] == 14
    assert row["development_class"] == "mixed"


def test_servicing_agreement_stays_unknown_class():
    # "This SA is associated with ..." carries no building program: no class is invented.
    row = BY_ID["23-016766"]
    assert row["permit_type"] == "Servicing Agreement"
    assert row["development_class"] == "unknown"
    assert row["units_total"] is None
