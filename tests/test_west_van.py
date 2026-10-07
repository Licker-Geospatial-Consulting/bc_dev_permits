"""Tests for the West Vancouver HTML harvester + Claude-read PDF enrichment.

Deterministic parsing (address, prose, height, documents, document priority) runs against a
saved detail fixture - no network, no Playwright. The Claude-read path is exercised by applying
hand-built "PDF" fields through apply_claude_pdf, which must fill gaps, stamp pdf_claude
provenance, flag needs_review, and log an HTML-vs-PDF conflict without overwriting the HTML value.

Run:  python -m pytest -q            (or:  python -m tests.test_west_van)
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import features  # noqa: E402
from bc_dev_permits.harvesters import west_van as wv  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
URL = "https://westvancouver.ca/business-development/development-applications/1337-ottawa-avenue-rezoning"


def _detail():
    return (FIX / "west_van_detail.html").read_text(encoding="utf-8")


def test_parse_detail_html():
    """Address (descriptor stripped), prose fields, height, and milestones parse from HTML."""
    row = wv.parse_detail(_detail(), URL)
    assert row["municipality"] == "west_van"
    assert row["address"] == "1337 Ottawa Avenue"          # "(Rezoning)" descriptor stripped
    assert row["development_class"] == "residential"
    assert row["height_m"] == 11.2                          # "from 10.5 metres to 11.2 metres" -> 11.2
    assert row["extraction_method"] == "html"
    assert row["needs_pdf_extraction"] is True             # low-signal -> PDF candidate
    names = [m["milestone"] for m in row["milestones"]]
    assert names == ["Application received", "First reading"]
    assert row["milestones"][0]["milestone_date"] == "2025-10-02"
    # The staff contact line is not mistaken for prose.
    assert "Assistant Planner" not in (row["raw_text"] or "")


def test_document_priority_order():
    """select_pdf_sources returns Staff Report first, then architectural/plans, then preliminary."""
    docs = wv.select_pdf_sources(_detail(), URL)
    titles = [d["title"] for d in docs]
    assert titles[0] == "Staff Report"
    assert "Architectural" in titles[1]
    assert titles[2] == "Preliminary Drawings"
    assert wv._doc_role("Staff Report") == "staff_report"
    assert wv._doc_role("Architectural drawings - west lot") == "drawings"


def test_apply_claude_pdf_fills_and_tags():
    """Claude-read fields fill gaps, are stamped pdf_claude, and the row is flagged for review."""
    row = wv.parse_detail(_detail(), URL)
    before_stories = row.get("number_of_stories")
    filled = wv.apply_claude_pdf(
        row,
        {"units_total": 2, "number_of_stories": 3, "lot_area": 1414.9, "lot_area_unit": "m2"},
        {"title": "Staff Report", "url": "http://x/staff.pdf"},
    )
    assert before_stories is None and filled >= 3
    assert row["units_total"] == 2 and row["lot_area"] == 1414.9
    assert row["extraction_method"] == "html+claude_read"
    assert row["field_methods"]["units_total"] == features.METHOD_PDF_CLAUDE
    assert row["needs_review"] is True                     # non-deterministic read
    # The deterministic HTML height is untouched and stays html-provenanced.
    assert row["height_m"] == 11.2
    # The read document is marked extracted.
    assert any(d.get("extracted") for d in row["documents"])


def test_apply_claude_pdf_logs_conflict():
    """A genuine HTML-vs-PDF scalar disagreement is logged, and the HTML value is kept."""
    row = wv.parse_detail(_detail(), URL)
    row["number_of_stories"] = 6               # pretend the HTML stated 6 storeys
    row.setdefault("field_methods", {})["number_of_stories"] = features.METHOD_HTML
    wv.apply_claude_pdf(row, {"number_of_stories": 8}, {"title": "Staff Report", "url": "http://x/s.pdf"})
    assert row["number_of_stories"] == 6       # deterministic HTML wins
    conflicts = {c["field"]: c for c in row["conflicts"]}
    assert "number_of_stories" in conflicts
    assert conflicts["number_of_stories"]["pdf"]["value"] == 8
    assert row["needs_review"] is True


if __name__ == "__main__":
    test_parse_detail_html()
    test_document_priority_order()
    test_apply_claude_pdf_fills_and_tags()
    test_apply_claude_pdf_logs_conflict()
    print("\nAll West Van harvester tests passed.")
