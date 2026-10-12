"""
Tests for the Surrey PDF-first harvester + Claude-read enrichment.

Surrey has no HTML detail pages: the application attributes come from each council planning report
PDF. The list scrape, the PDF page-1 PROPOSAL parse (deterministic, from the report's own text), the
row mapping, and the Claude-read merge all run against small saved fixtures - no network.

Run:  python -m pytest -q tests/test_surrey.py   (or:  python -m tests.test_surrey)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import features  # noqa: E402
from bc_dev_permits.harvesters import surrey  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def test_get_report_rows():
    """Each list row yields permit id, address, status, dates and the report PDF url."""
    rows = {r["permit_id"]: r for r in
            surrey.get_report_rows((FIX / "surrey_list.html").read_text(encoding="utf-8"))}
    assert set(rows) == {"7925-0315-00", "7926-0216-00"}
    r = rows["7925-0315-00"]
    assert r["address"] == "13216 - 104 Avenue"
    assert r["status"] == "Active"
    assert r["meeting_date"] == "2026-09-21"
    assert r["pdf_url"].endswith("/PLR_7925-0315-00.pdf")
    print("\nget_report_rows PASSED ✓")


def test_pdf_proposal_parses_header():
    """The report PDF's page-1 PROPOSAL yields the description, application types and zoning change."""
    pytest.importorskip("pymupdf")
    proposal = surrey.pdf_proposal((FIX / "surrey_report.pdf").read_bytes())
    assert "24-storey residential building with 230 rental units" in proposal["description"]
    assert proposal["permit_types"] == ["Rezoning", "Development Variance Permit"]
    assert proposal["zoning"] == "R3 and CG-2 -> MR10"
    print("\npdf_proposal PASSED ✓")


def test_parse_report_from_proposal():
    """The PROPOSAL prose parses deterministically into units / storeys / class; types + zoning kept."""
    meta = {"permit_id": "7925-0315-00", "address": "13216 - 104 Avenue", "status": "Active",
            "meeting_date": "2026-09-21", "report_date": "September 21, 2025",
            "pdf_url": "https://www.surrey.ca/x/PLR_7925-0315-00.pdf"}
    proposal = {"description": "Revisions to a proposed 24-storey residential building with 230 "
                "rental units.", "permit_types": ["Rezoning", "Development Variance Permit"],
                "zoning": "R3 and CG-2 -> MR10", "text": ""}
    row = surrey.parse_report(meta, proposal)
    assert row["municipality"] == "surrey"
    assert row["permit_type"] == "Rezoning, Development Variance Permit"   # from the PDF bullets
    assert row["development_class"] == "residential"
    assert row["units_total"] == 230 and row["number_of_stories"] == 24
    assert row["rental_or_strata"] == "rental"
    assert row["zoning_density"] == "R3 and CG-2 -> MR10"
    assert row["extraction_method"] == "pdf_text"
    assert row["needs_pdf_extraction"] is True            # data-sheet (parking/unit mix) still to read
    assert row["documents"][0]["url"].endswith("/PLR_7925-0315-00.pdf")
    names = {m["milestone"] for m in row["milestones"]}
    assert names == {"Planning Report", "Council Meeting"}
    print("\nparse_report PASSED ✓")


def _w(text, x, y, width=42):
    """Build a pymupdf-style word tuple (x0, y0, x1, y1, text) at a column x and row y."""
    return (x, y, x + width, y + 12, text)


# Column x-anchors shared by the positional-parking fixtures below: label / Required / Proposed.
_LX, _RX, _PX = 140, 500, 800


def test_parking_number_of_stalls_table():
    """The Proposed column's Total row is the vehicle count, the Secure row the bicycle count."""
    words = [_w("Required", _RX, 100), _w("Proposed", _PX, 100),
             _w("Number", _LX, 140), _w("Stalls", _LX + 90, 140),
             _w("Residential:", _LX, 170), _w("0", _RX, 170), _w("199", _PX, 170),
             _w("Total:", _LX, 200), _w("22", _RX, 200), _w("223", _PX, 200),
             _w("Bicycle", _LX, 230),
             _w("Secure", _LX + 60, 260), _w("125", _RX, 260), _w("125", _PX, 260)]
    assert surrey._parking_from_words(words) == (223, 125)
    print("\nparking number-of-stalls PASSED ✓")


def test_parking_sums_components_when_total_blank():
    """A blank Total row falls back to the sum of the Proposed component rows above it."""
    words = [_w("Required", _RX, 100), _w("Proposed", _PX, 100),
             _w("Number", _LX, 140), _w("Stalls", _LX + 90, 140),
             _w("Commercial:", _LX, 170), _w("46", _RX, 170), _w("47", _PX, 170),
             _w("Residential:", _LX, 190), _w("0", _RX, 190), _w("116", _PX, 190),
             _w("Total:", _LX, 210),                      # no value in either column
             _w("Bicycle", _LX, 250)]
    assert surrey._parking_from_words(words)[0] == 163     # 47 + 116
    print("\nparking component-sum PASSED ✓")


def test_parking_part5_single_column():
    """A Parking (Part 5) table with one Proposed column reads Total + a Bicycle row."""
    words = [_w("Parking", _LX, 300), _w("(Part", _LX + 70, 300), _w("5)", _LX + 120, 300),
             _w("Proposed", _PX, 300),
             _w("Residential:", _LX, 330), _w("83", _PX, 330),
             _w("Total:", _LX, 360), _w("92", _PX, 360),
             _w("Bicycle", _LX, 390), _w("110", _PX, 390)]
    assert surrey._parking_part5(words) == (92, 110)
    print("\nparking part-5 PASSED ✓")


def test_parking_declines_multi_lot():
    """A per-lot table (two Proposed columns) is ambiguous and reads nothing, not a lot's partial."""
    nos = [_w("Required", _RX, 100), _w("Proposed", 700, 100), _w("Proposed", 1000, 100),
           _w("Number", _LX, 140), _w("Stalls", _LX + 90, 140),
           _w("Total:", _LX, 200), _w("150", 700, 200), _w("196", 1000, 200)]
    assert surrey._parking_from_words(nos) == (None, None)
    part5 = [_w("Parking", _LX, 300), _w("(Part", _LX + 70, 300), _w("5)", _LX + 120, 300),
             _w("Proposed", 700, 300), _w("Proposed", 1000, 300),
             _w("Total:", _LX, 360), _w("150", 700, 360), _w("196", 1000, 360)]
    assert surrey._parking_part5(part5) == (None, None)
    print("\nparking multi-lot decline PASSED ✓")


def test_apply_claude_pdf_fills_and_conflicts():
    """Claude-read data-sheet fields fill gaps + stamp provenance; a disagreement is logged."""
    meta = {"permit_id": "7925-0315-00", "address": "13216 - 104 Avenue", "status": "Active",
            "pdf_url": "https://www.surrey.ca/x/PLR_7925-0315-00.pdf"}
    proposal = {"description": "a 24-storey residential building with 230 rental units.",
                "permit_types": ["Rezoning"], "zoning": None, "text": ""}
    row = surrey.parse_report(meta, proposal)
    doc = surrey.select_pdf_sources(row)[0]
    filled = surrey.apply_claude_pdf(row, {
        "parking_vehicle_stalls": 243,
        "unit_mix": {"studio": 40, "1-bed": 120, "2-bed": 70},
        "units_total": 260,   # disagrees with the deterministic 230 -> conflict, HTML/text kept
    }, doc)

    assert row["parking_vehicle_stalls"] == 243
    assert row["unit_mix"] == {"studio": 40, "1-bed": 120, "2-bed": 70}
    assert row["units_total"] == 230                      # deterministic value NOT overwritten
    assert filled >= 2
    assert row["extraction_method"] == "pdf_text+claude_read"
    assert row["field_methods"]["parking_vehicle_stalls"] == features.METHOD_PDF_CLAUDE
    assert "units_total" in {c["field"] for c in row.get("conflicts") or []}
    print("\napply_claude_pdf PASSED ✓")


if __name__ == "__main__":
    test_get_report_rows()
    test_pdf_proposal_parses_header()
    test_parse_report_from_proposal()
    test_parking_number_of_stalls_table()
    test_parking_sums_components_when_total_blank()
    test_parking_part5_single_column()
    test_parking_declines_multi_lot()
    test_apply_claude_pdf_fills_and_conflicts()
    print("\nAll Surrey harvester tests passed.")
