"""Victoria PDF-fallback source selection (for low-signal rows).

When a detail page has little usable prose, `victoria.select_pdf_source` decides WHICH
document to send to the Ollama PDF extractor (references/ollama_pdf_extraction.md). The
selection is deterministic and tested here against trimmed-real fixtures for three
scored-0.4 applications; the Ollama call itself (extract_via_pdf) needs a local server
and is not exercised here.

Rule under test:
  * ACTIVE related application with a Details link -> use THAT application's documents.
  * otherwise -> use this page's own documents.
  * within the set: newest "letter to mayor/council", else newest document.

Do NOT edit the fixtures to make a test pass - fix the selector instead.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import victoria  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE = victoria.DETAIL_BASE + "?folderNumber="


def _html(folder: str) -> str:
    return (FIXTURES / f"victoria_{folder}.html").read_text(encoding="utf-8")


class _Fetcher:
    """Injected fetch that serves related pages from fixtures and records its calls."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        folder = re.search(r"folderNumber=(\w+)", url).group(1)
        return _html(folder)


def _select(folder: str):
    fetch = _Fetcher()
    src = victoria.select_pdf_source(_html(folder), BASE + folder, fetch)
    return src, fetch


def test_no_active_related_uses_own_most_recent_document():
    # 2931 Shelbourne (DPV00285): related apps all ARCHIVED, so use this page's own docs;
    # no council letter exists -> the most recent document (the only one) is chosen.
    src, fetch = _select("DPV00285")
    assert fetch.calls == []  # did not follow any related application
    assert src["source_url"].endswith("DPV00285")
    assert src["document"]["title"] == "2025-10-30 - Plans_Revisions_Bubbled_Compressed for DT.pdf"
    assert src["document"]["url"].startswith("https://tender.victoria.ca")


def test_own_documents_prefer_council_letter():
    # 535 Yates (DVP00240): no related app -> own docs; a mayor/council letter is chosen
    # over the plans/signage documents.
    src, fetch = _select("DVP00240")
    assert fetch.calls == []
    assert src["source_url"].endswith("DVP00240")
    assert victoria._is_council_letter(src["document"]["title"])


def test_active_related_followed_for_latest_council_letter():
    # 1908 Foul Bay (DPV00294): no own documents, but the ACTIVE related REZ00896 has a
    # Details link -> follow it and take its LATEST "Letter to Council".
    src, fetch = _select("DPV00294")
    assert len(fetch.calls) == 1
    assert fetch.calls[0].endswith("REZ00896")
    assert src["source_url"].endswith("REZ00896")
    assert src["document"]["title"] == "2026-06-03 - Letter to Council.pdf"


def test_pick_document_prefers_latest_letter_by_date():
    docs = [
        {"title": "2025-05-22 - Letter to Council.pdf", "url": "a"},
        {"title": "2026-06-03 - Letter to Council.pdf", "url": "b"},
        {"title": "2026-07-01 - Plans_Revisions.pdf", "url": "c"},  # newer, but not a letter
    ]
    assert victoria._pick_document(docs)["url"] == "b"  # newest letter wins over newer plans


def test_pick_document_falls_back_to_newest_when_no_letter():
    docs = [
        {"title": "2024-01-01 - Plans.pdf", "url": "old"},
        {"title": "2025-09-09 - Revisions.pdf", "url": "new"},
    ]
    assert victoria._pick_document(docs)["url"] == "new"


def test_select_returns_none_without_documents_or_active_related():
    assert victoria.select_pdf_source("<html><body></body></html>", BASE + "X", lambda u: "") is None


# --------------------------------------------------------------------------- #
# batch enrichment merge logic (deterministic; no Ollama)
# --------------------------------------------------------------------------- #
def test_needs_pdf_predicate():
    assert victoria._needs_pdf({"needs_pdf_extraction": True})
    assert victoria._needs_pdf({"extraction_confidence": 0.4})
    assert not victoria._needs_pdf({"extraction_confidence": 0.8})
    assert victoria._needs_pdf({})  # no confidence at all -> treat as low signal


def test_merge_pdf_fills_gaps_but_keeps_deterministic_values():
    row = {"units_total": 3, "number_of_stories": None, "development_class": "unknown"}
    fields = {
        "units_total": 9,  # must NOT overwrite the deterministic 3
        "number_of_stories": 6,  # gap -> fill
        "development_class": "residential",  # 'unknown' counts as a gap -> fill
        "confidence": 1.0,  # model metadata -> not copied onto the row
    }
    filled = victoria._merge_pdf_fields(row, fields)
    assert row["units_total"] == 3
    assert row["number_of_stories"] == 6
    assert row["development_class"] == "residential"
    assert "confidence" not in row
    assert filled == 2


def test_record_provenance_marks_document_and_forces_review():
    row = {"development_class": "residential", "address": "846 BROUGHTON ST", "documents": []}
    fields = {
        "extraction_method": "ollama_pdf",
        "pdf_document": {"url": "https://x/letter.pdf", "title": "Letter to Council"},
    }
    victoria._record_pdf_provenance(row, fields)
    assert row["needs_review"] is True  # LLM-derived values always reviewed
    assert row["extraction_method"] == "html+ollama_pdf"
    assert row["documents"][0]["url"] == "https://x/letter.pdf"
    assert row["documents"][0]["extracted"] is True
