"""City of Vancouver harvester tests (CityHallWatch snapshot + ShapeYourCity detail).

Fixtures are trimmed-but-real markup:
  vancouver_list.html            - a snapshot post: New / Active / Approved sections of <li> items
  vancouver_868_e_hastings.html  - a ShapeYourCity page: full-description block + document links

Do NOT edit the fixtures to make a test pass - fix the parser instead.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import vancouver

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIST_HTML = (FIXTURES / "vancouver_list.html").read_text(encoding="utf-8")
DETAIL_HTML = (FIXTURES / "vancouver_868_e_hastings.html").read_text(encoding="utf-8")
LETTER_PDF = (FIXTURES / "vancouver_prior_to_letter.pdf").read_bytes()
URL_868 = "https://www.shapeyourcity.ca/868-e-hastings-st"


# --- list parsing -----------------------------------------------------------
def test_new_and_active_dedup_and_exclude_approved():
    # New is a subset of Active, so New+Active returns the Active set; Approved is excluded.
    cards = vancouver.parse_list(LIST_HTML, ("new", "active"))
    ids = {c["permit_id"] for c in cards}
    assert ids == {"DP-2026-00743", "DP-2026-00731", "DP-2026-00099", "DP-2025-00656"}
    assert len(cards) == 4  # New (3) is a subset of Active (4); deduped by url


def test_civic_range_address_not_split_on_hyphen():
    cards = {c["permit_id"]: c for c in vancouver.parse_list(LIST_HTML, ("active",))}
    assert cards["DP-2026-00731"]["address"] == "2120-2150 W 10th Ave"


def test_address_note_after_endash_is_dropped():
    cards = {c["permit_id"]: c for c in vancouver.parse_list(LIST_HTML, ("active",))}
    # "5687 Maple St - formerly 2021 W 41st Ave - DP-2026-00099" -> address is the first segment.
    assert cards["DP-2026-00099"]["address"] == "5687 Maple St"


def test_card_fields_and_posted_date():
    card = next(c for c in vancouver.parse_list(LIST_HTML, ("new",))
                if c["permit_id"] == "DP-2026-00743")
    assert card["address"] == "868 E Hastings St"
    assert card["permit_type"] == "Development Permit"
    assert card["stream"] == "new"
    assert card["status"] == "New"
    assert card["posted_date"] == "2026-10-08"
    assert card["verb"] == "posted"


def test_approved_stream_verbs():
    cards = {c["permit_id"]: c for c in vancouver.parse_list(LIST_HTML, ("approved",))}
    assert cards["DP-2026-00595"]["verb"] == "approved"
    assert cards["DP-2026-00595"]["status"] == "Approved"
    assert cards["DP-2025-00885"]["verb"] == "permit issued"


# --- detail parsing ---------------------------------------------------------
def test_detail_prose_class_and_documents():
    card = next(c for c in vancouver.parse_list(LIST_HTML, ("new",))
                if c["permit_id"] == "DP-2026-00743")
    row = vancouver.parse_detail(DETAIL_HTML, URL_868, card)
    assert row["permit_id"] == "DP-2026-00743"
    assert row["address"] == "868 E Hastings St"
    assert "permanent outdoor patio" in row["raw_text"]
    assert row["development_class"] == "commercial"  # "Restaurant Class-1" prose
    assert len(row["documents"]) == 4
    roles = {d["title"]: d["doc_role"] for d in row["documents"]}
    assert roles["Site plan project details"] == "site_plan"
    assert roles["Patio elevations"] == "elevation"
    # Document titles have the "(814 KB) (pdf)" size suffix stripped.
    assert all("(pdf)" not in d["title"] and "KB" not in d["title"] for d in row["documents"])
    # No floor area / height / parking in the HTML, but documents exist -> a PDF-read candidate.
    assert row["needs_pdf_extraction"] is True


def test_select_pdf_sources_prioritises_site_plan_then_elevation():
    order = [d["title"] for d in vancouver.select_pdf_sources(DETAIL_HTML, URL_868)]
    assert order[0] == "Site plan project details"
    assert order[1] == "Patio elevations"
    assert order[-1] in ("Notification postcard", "Operational letter")


# --- Claude-read PDF + conflict mechanism -----------------------------------
def test_apply_claude_pdf_fills_gaps_without_overwriting_html():
    row = {"development_class": "commercial", "occupancy_types": ["commercial"],
           "number_of_stories": None, "floor_area": None, "extraction_method": "html",
           "documents": [], "field_methods": {}}
    filled = vancouver.apply_claude_pdf(
        row, {"number_of_stories": 2, "floor_area": 320.9, "floor_area_unit": "m2"}, None)
    assert filled == 3
    assert row["number_of_stories"] == 2
    assert row["field_methods"]["number_of_stories"] == "pdf_claude"
    assert row["extraction_method"] == "html+claude_read"


def test_apply_claude_pdf_logs_conflict_keeps_html():
    # A daycare data sheet (institutional) disagreeing with the page's "commercial": HTML wins,
    # the disagreement is logged, not dropped.
    row = {"development_class": "commercial", "occupancy_types": ["commercial"],
           "extraction_method": "html", "documents": [], "field_methods": {}}
    vancouver.apply_claude_pdf(
        row, {"development_class": "institutional", "occupancy_types": ["institutional"]}, None)
    assert row["development_class"] == "commercial"
    conflicts = {c["field"]: c for c in row.get("conflicts", [])}
    assert "development_class" in conflicts
    assert conflicts["development_class"]["kept"]["value"] == "commercial"
    assert conflicts["development_class"]["pdf"]["value"] == "institutional"


# --- approved: prior-to letter parsing (deterministic text) -----------------
def test_letter_parking_counts():
    assert vancouver._letter_parking("providing ninety-five (95) parking spaces") == 95
    assert vancouver._letter_parking("a total of three parking spaces") == 3
    assert vancouver._letter_parking("providing zero parking spaces") == 0
    assert vancouver._letter_parking("one surface parking space") == 1
    # "over four levels of underground parking" has no "parking space" token -> no count
    assert vancouver._letter_parking("over four levels of underground parking") is None


def test_parse_prior_to_letter_reads_approval_paragraph():
    fields = vancouver.parse_prior_to_letter(LETTER_PDF)
    assert fields is not None
    assert fields["development_class"] == "mixed"          # "mixed-use ... commercial retail"
    assert fields["number_of_stories"] == 6                # "six (6)-storey"
    assert fields["units_total"] == 89                     # "eighty-nine (89) ... units"
    assert fields["parking_vehicle_stalls"] == 95          # "ninety-five (95) parking spaces"
    assert fields["rental_or_strata"] == "rental"
    assert "approved to develop" in fields["approval_text"]


def test_parse_prior_to_letter_none_without_approval():
    # A PDF with no "approved to ..." paragraph yields None (not every letter is an approval).
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((54, 72), "Dear applicant, your file is under review.", fontsize=10)
    assert vancouver.parse_prior_to_letter(doc.tobytes()) is None
