"""Township of Langley (Prospero tracker) harvester tests.

Fixtures are trimmed-but-real HTML captured from e-connect.tol.ca:
  langley_list.html      - first four ACTIVE result cards + a pager
  langley_AL100478.html  - an ALR application with two own documents, no related apps
  langley_DP101542.html  - a Development Permit with ACTIVE related RZ/DV applications
  langley_RZ100805.html  - the related rezoning (its own documents)

Do NOT edit the fixtures to make a test pass - fix the parser instead.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import (
    langley,
    pdf_enrich,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIST_HTML = (FIXTURES / "langley_list.html").read_text(encoding="utf-8")
AL = (FIXTURES / "langley_AL100478.html").read_text(encoding="utf-8")
DP = (FIXTURES / "langley_DP101542.html").read_text(encoding="utf-8")
RZ = (FIXTURES / "langley_RZ100805.html").read_text(encoding="utf-8")
BASE = langley.DETAIL_BASE


def _fetch(url: str) -> str:
    """Serve a related application's detail page from the fixtures (else raise, as a dead link)."""
    folder = url.rsplit("folderNumber=", maxsplit=1)[-1]
    path = FIXTURES / f"langley_{folder}.html"
    if not path.exists():
        raise FileNotFoundError(folder)
    return path.read_text(encoding="utf-8")


# --- list -------------------------------------------------------------------
def test_list_yields_one_row_per_card():
    rows = langley.parse_list(LIST_HTML)
    assert len(rows) == 4
    assert all(r["municipality"] == "langley" for r in rows)
    first = rows[0]
    assert first["permit_id"] == "AL100480"
    assert first["permit_type"] == "Agricultural Land Reserve"
    assert first["address"] == "3437 264 ST"
    assert first["source_url"].endswith("folderNumber=AL100480")


# --- detail -----------------------------------------------------------------
def test_detail_core_fields_and_documents():
    d = langley.parse_detail(AL, f"{BASE}?folderNumber=AL100478")
    assert d["permit_type"] == "Agricultural Land Reserve"
    assert d["status"] == "ACTIVE"
    assert d["addresses"] == ["22311 NO 10 HWY"]
    titles = [doc["title"] for doc in d["documents"]]
    assert "Report to Council - June 15, 2026" in titles
    assert "Site Plan 2026-03-19" in titles
    assert all("FileDownload" in doc["url"] for doc in d["documents"])
    assert d["related"] == []
    # The "... PASSED" featured-task is folded into the dated milestone, not duplicated.
    names = [m["milestone"] for m in d["milestones"]]
    assert names.count("COUNCIL CONSIDERATION") == 1


def test_detail_related_applications():
    d = langley.parse_detail(DP, f"{BASE}?folderNumber=DP101542")
    assert d["permit_type"] == "DEVELOPMENT PERMIT"
    assert len(d["addresses"]) == 7  # one folder spans several addresses
    related = {r["permit_id"]: r for r in d["related"]}
    assert set(related) == {"RZ100805", "DV100216"}
    assert related["RZ100805"]["status"] == "ACTIVE"
    assert related["RZ100805"]["url"].endswith("folderNumber=RZ100805")


# --- PDF source ordering ----------------------------------------------------
def test_own_documents_ordered_most_recent_first():
    # Report to Council (2026-06-15) is newer than the Site Plan (2026-03-19).
    cands = langley.select_pdf_sources(AL, f"{BASE}?folderNumber=AL100478", _fetch)
    titles = [c["document"]["title"] for c in cands]
    assert titles[0] == "Report to Council - June 15, 2026"
    assert titles[1] == "Site Plan 2026-03-19"


def test_own_documents_come_before_related_fallback():
    # DP101542 has its own Site Plan; the ACTIVE related rezoning RZ100805 is the fallback.
    cands = langley.select_pdf_sources(DP, f"{BASE}?folderNumber=DP101542", _fetch)
    sources = [c["source_url"].split("folderNumber=")[-1] for c in cands]
    assert sources[0] == "DP101542"  # own document first
    assert "RZ100805" in sources  # the active related rezoning is reached as a fallback
    assert sources.index("DP101542") < sources.index("RZ100805")


def test_conflict_logged_like_victoria():
    # The shared Victoria conflict mechanism: a genuine HTML-vs-PDF disagreement is recorded.
    row = {"units_total": 4, "field_methods": {"units_total": "html"}}
    pdf_enrich.apply_claude_pdf(row, {"units_total": 9}, {"title": "Site Plan", "url": "x"})
    assert row["units_total"] == 4  # deterministic HTML is never overwritten
    assert any(c["field"] == "units_total" for c in row.get("conflicts", []))


def test_pdf_address_matching_any_html_address_is_not_a_conflict():
    # A folder spans several addresses; a PDF address matching ANY of them is no disagreement.
    row = {
        "address": "5670 264 ST",
        "addresses": ["5670 264 ST", "26477 56 AVE"],
        "field_methods": {"address": "html"},
    }
    # "26477 56 Avenue" matches the second listed address (modulo AVE/Avenue) -> no conflict.
    pdf_enrich.apply_claude_pdf(row, {"address": "26477 56 Avenue"}, {"title": "Site", "url": "x"})
    assert row["address"] == "5670 264 ST"  # HTML primary kept
    assert not any(c["field"] == "address" for c in row.get("conflicts") or [])
    # A genuinely different address IS still a conflict.
    row2 = {
        "address": "5670 264 ST",
        "addresses": ["5670 264 ST"],
        "field_methods": {"address": "html"},
    }
    pdf_enrich.apply_claude_pdf(
        row2, {"address": "100 Main Street"}, {"title": "Site", "url": "x"}
    )
    assert any(c["field"] == "address" for c in row2.get("conflicts") or [])


def test_pdf_address_matched_from_raw_text_list():
    # Works even when the full list lives only in the raw_text "Addresses:" line (current data).
    row = {
        "address": "5670 264 ST",
        "raw_text": "Purpose...\n\nAddresses: 5670 264 ST; 26477 56 AVE",
        "field_methods": {"address": "html"},
    }
    pdf_enrich.apply_claude_pdf(row, {"address": "26477 56 Avenue"}, {"title": "Site", "url": "x"})
    assert not any(c["field"] == "address" for c in row.get("conflicts") or [])


def test_pdf_address_replaces_na_placeholder():
    # An "N/A" placeholder address is not real data, so a PDF-read address replaces it
    # (no conflict logged) - this is how a missing address gets filled from PDF tables.
    row = {"address": "N/A", "field_methods": {"address": "html"}}
    pdf_enrich.apply_claude_pdf(
        row, {"address": "7831 204 Street"}, {"title": "Drawings", "url": "x"}
    )
    assert row["address"] == "7831 204 Street"
    assert not any(c["field"] == "address" for c in row.get("conflicts") or [])
    # A real HTML address is still protected (PDF disagreement is a logged conflict, not a replace).
    row2 = {"address": "100 Main Street", "field_methods": {"address": "html"}}
    pdf_enrich.apply_claude_pdf(
        row2, {"address": "7831 204 Street"}, {"title": "Drawings", "url": "x"}
    )
    assert row2["address"] == "100 Main Street"
    assert any(c["field"] == "address" for c in row2.get("conflicts") or [])


def test_pdf_queue_select_queue():
    from tools import langley_pdf_queue as q

    rows = [
        # One project at 7687 200 St: a DP with no docs + an enriched RO sibling -> skipped (covered).
        {
            "permit_id": "DP101299",
            "address": "7687 200 ST",
            "documents": [],
            "related_permits": [{"status": "ACTIVE"}],
            "extraction_method": "html",
        },
        {
            "permit_id": "RO100224",
            "address": "7687 200 ST",
            "documents": [{"url": "x"}],
            "extraction_method": "html+claude_read",
        },
        # A project with no enriched sibling -> the richest-type member (DP) represents it.
        {
            "permit_id": "RZ100500",
            "address": "100 Main St",
            "documents": [{"url": "a"}],
            "extraction_method": "html",
        },
        {
            "permit_id": "DP101500",
            "address": "100 Main St",
            "documents": [{"url": "b"}, {"url": "c"}],
            "extraction_method": "html",
        },
        # A flagged row with no documents and no active relation -> not queued.
        {
            "permit_id": "DV100999",
            "address": "200 Main St",
            "needs_pdf_extraction": True,
            "documents": [],
            "related_permits": [],
            "extraction_method": "html",
        },
    ]
    # Default queue: 7687 project is covered (RO enriched), 100 Main is represented by its DP.
    assert q.select_queue(rows) == ["DP101500"]
    # include_read keeps the covered project and returns one representative (the DP) ordered first.
    got = q.select_queue(rows, include_read=True)
    assert "DP101500" in got and "DP101299" in got or "RO100224" in got
    # Explicit permit_ids bypass project grouping and the read filter.
    assert q.select_queue(rows, permit_ids=["RO100224", "DP101299"]) == ["RO100224", "DP101299"]


def test_inherit_class_from_related():
    # A variance with no building use of its own inherits its project's class from a related
    # Development Permit, and the chain resolves regardless of order (fixpoint).
    dp = {
        "permit_id": "DP1",
        "development_class": "residential",
        "occupancy_types": ["residential"],
        "related_permits": [],
    }
    dv = {
        "permit_id": "DV1",
        "development_class": "unknown",
        "occupancy_types": [],
        "related_permits": [
            {"permit_id": "DP1", "permit_type": "Development Permit", "status": "ACTIVE"}
        ],
    }
    # RO is linked only to DV (which is itself unknown until DV resolves) -> needs a second pass.
    ro = {
        "permit_id": "RO1",
        "development_class": "unknown",
        "occupancy_types": [],
        "related_permits": [
            {"permit_id": "DV1", "permit_type": "Rezoning/OCP", "status": "ACTIVE"}
        ],
    }
    langley._inherit_class_from_related([ro, dv, dp])
    assert dv["development_class"] == "residential"
    assert dv["occupancy_types"] == ["residential"]
    assert ro["development_class"] == "residential"


def test_inherit_class_never_overrides_known_or_fabricates():
    # A row already classified deterministically is left untouched; an unknown row with no
    # classified relation stays unknown (no fabrication).
    known = {
        "permit_id": "DP2",
        "development_class": "industrial",
        "occupancy_types": ["industrial"],
        "related_permits": [
            {"permit_id": "DP3", "permit_type": "Development Permit", "status": "ACTIVE"}
        ],
    }
    other = {
        "permit_id": "DP3",
        "development_class": "residential",
        "occupancy_types": ["residential"],
        "related_permits": [],
    }
    orphan = {
        "permit_id": "DV2",
        "development_class": "unknown",
        "occupancy_types": [],
        "related_permits": [{"permit_id": "GONE", "permit_type": "Rezoning", "status": "ACTIVE"}],
    }
    langley._inherit_class_from_related([known, other, orphan])
    assert known["development_class"] == "industrial"  # not overridden by related residential
    assert orphan["development_class"] == "unknown"  # related folder absent -> stays unknown
