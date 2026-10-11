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
    test_apply_claude_pdf_fills_and_conflicts()
    print("\nAll Surrey harvester tests passed.")
