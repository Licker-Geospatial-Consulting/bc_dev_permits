"""
Tests for the UBC HTML harvester + Claude-read PDF enrichment.

Deterministic parsing (project cards, detail fields, boilerplate/PII stripping, document priority)
runs against small saved HTML fixtures - no network. The Claude-read path is exercised by applying
hand-built "PDF" fields through apply_claude_pdf, which must fill gaps, stamp pdf_claude provenance,
and log an HTML-vs-PDF conflict without overwriting the deterministic HTML value.

Run:  python -m pytest -q tests/test_ubc.py   (or:  python -m tests.test_ubc)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import features  # noqa: E402
from bc_dev_permits.harvesters import ubc  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
URL = "https://planning.ubc.ca/bcr7"


def _list():
    return (FIX / "ubc_list.html").read_text(encoding="utf-8")


def _detail():
    return (FIX / "ubc_detail.html").read_text(encoding="utf-8")


def test_get_project_cards():
    """Only the DP-numbered project cards are returned, with their status; nav links are ignored."""
    cards = {c["permit_id"]: c for c in ubc.get_project_cards(_list())}
    assert set(cards) == {"DP24018", "DP25042", "DP26024"}       # about-us / community skipped
    assert cards["DP24018"]["status"] == "Under Construction"
    assert cards["DP26024"]["status"] == "Application"
    assert cards["DP24018"]["url"].endswith("/bcr7")
    print("\nget_project_cards PASSED ✓")


def test_parse_detail_html():
    """Name + permit from the <h1>, Location as address, prose fields; boilerplate + PII dropped."""
    row = ubc.parse_detail(_detail(), URL, {"permit_id": "DP24018", "status": "Under Construction"})
    assert row["municipality"] == "ubc"
    assert row["permit_id"] == "DP24018"
    assert row["development_name"] == "Wesbrook Place Lot BCR 7"
    assert row["address"].startswith("Wesbrook Place Lot BCR 7")   # from the Location field
    assert row["development_class"] == "residential"
    assert row["units_total"] == 303
    assert row["number_of_stories"] == 14                          # tallest element (14-storey tower)
    assert row["floor_area"] == 21784.1 and row["floor_area_unit"] == "m2"
    assert row["status"] == "Under Construction"
    assert row["extraction_method"] == "html"
    # The PDFs carry parking / unit mix the HTML omits, so the row is a Claude-read candidate.
    assert row["needs_pdf_extraction"] is True
    # The "UBC campus consists of 994 acres" boilerplate must not pollute the area/raw_text, and the
    # staff contact (a person's name) must not leak into the address or prose.
    assert "994 acres" not in (row["raw_text"] or "")
    assert "Karen Russell" not in (row["raw_text"] or "") and "Karen Russell" not in row["address"]
    print("PASSED ✓")


def test_document_priority_order():
    """select_pdf_sources returns the Development Permit Board report first, then drawings."""
    titles = [d["title"] for d in ubc.select_pdf_sources(_detail(), URL)]
    assert titles[0] == "Development Permit Board Report"
    assert titles.index("Architectural Plans") < titles.index("Civil Plans")
    assert titles.index("Civil Plans") < titles.index("Attachment B: Arborist Report")
    print("\ndocument-priority PASSED ✓")


def test_apply_claude_pdf_fills_and_conflicts():
    """Claude-read fields fill gaps + stamp provenance; a scalar disagreement is logged, not applied."""
    row = ubc.parse_detail(_detail(), URL, {"permit_id": "DP24018"})
    doc = {"title": "Development Permit Board Report", "url": "https://planning.ubc.ca/x.pdf"}
    filled = ubc.apply_claude_pdf(row, {
        "parking_vehicle_stalls": 303,
        "unit_mix": {"market-rental": 189, "faculty-staff-rental": 114},
        "floor_area": 20000.0,   # disagrees with the HTML's 21,784.1 -> conflict, HTML kept
    }, doc)

    assert row["parking_vehicle_stalls"] == 303                   # gap filled from the PDF
    assert row["unit_mix"] == {"market-rental": 189, "faculty-staff-rental": 114}
    assert row["floor_area"] == 21784.1                           # deterministic HTML NOT overwritten
    assert filled >= 2
    assert row["extraction_method"] == "html+claude_read"
    assert row["field_methods"]["parking_vehicle_stalls"] == features.METHOD_PDF_CLAUDE
    assert row["needs_review"] is True
    conflicts = {c["field"] for c in row.get("conflicts") or []}
    assert "floor_area" in conflicts                              # the disagreement was logged
    print("\napply_claude_pdf PASSED ✓")


if __name__ == "__main__":
    test_get_project_cards()
    test_parse_detail_html()
    test_document_priority_order()
    test_apply_claude_pdf_fills_and_conflicts()
    print("\nAll UBC harvester tests passed.")
