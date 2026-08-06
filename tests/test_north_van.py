"""
Tests for the North Vancouver (CNV) HTML harvester.

Runs the deterministic parser against saved fixtures (the container can't reach
cnv.org, and this keeps the test hermetic). Fixtures are trimmed real pages: each keeps
the real ``#page-content`` subtree plus the site-footer City Hall address block, so the
"footer must not leak into the application prose" regression is genuinely exercised.

  * 115_east_18th  — description lives in a <p>            (happy path)
  * 215_west_keith — description lives in a bare <div>     (regression: previously the
                     parser missed it and captured the City Hall footer address instead)
  * 453_west_24th  — no usable prose, only a PDF link      (PDF-fallback path)

Run:  python -m pytest -q            (or:  python -m tests.test_north_van)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import north_van  # noqa: E402

FIX = Path(__file__).parent / "fixtures"

# The City Hall address that sits in every CNV page footer. It must never end up in the
# application prose — that was the 215 West Keith Road bug.
CITY_HALL = "141 West 14th"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def _parse(name):
    url = f"https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications/{name}"
    return north_van.parse_detail(_load(f"{name}.html"), url)


def show(row):
    printable = {k: v for k, v in row.items() if k not in ("raw_text",)}
    print(json.dumps(printable, indent=2, ensure_ascii=False))


def test_115_east_18th_happy_path():
    """Description is a <p>: fields parse deterministically and the footer stays out."""
    row = _parse("115-East-18th-Street")
    print("\n=== 115 East 18th Street (prose in <p>) ===")
    show(row)

    assert row["address"] == "115 East 18th Street"
    assert row["permit_id"] == "PLN2025-00010"          # recovered from PDF filename
    assert row["permit_type"] == "Rezoning"
    assert row["development_class"] == "residential"
    assert row["number_of_stories"] == 6
    assert row["units_total"] == 40
    assert row["unit_mix"] == {"1-bed": 19, "2-bed": 12, "3-bed": 3, "suite": 6}
    assert row["parking_vehicle_stalls"] == 21
    assert row["parking_bike_stalls"] == 56
    assert row["rental_or_strata"] == "rental"
    assert "rental" in (row["rental_subtype"] or "")
    assert row["is_parsed"] is True
    assert row["needs_pdf_extraction"] is False
    assert len(row["documents"]) == 6
    assert {m["milestone"] for m in row["milestones"]} >= {"Application Accepted"}
    assert row["milestones"][0]["milestone_date"] == "2025-10-23"
    # The real description, not the City Hall footer address.
    assert row["raw_text"].startswith("1480821 B.C LTD.")
    assert CITY_HALL not in row["raw_text"]
    print("PASSED ✓")


def test_215_west_keith_div_prose_regression():
    """Description is a bare <div>: it must be captured, NOT the footer address.

    Previously ``_prose`` only looked at <p> tags, found none in the content, and the
    only qualifying <p> on the page was the City Hall address in the footer — so raw_text
    became "141 West 14th Street ..." and the row was wrongly flagged for PDF extraction.
    """
    row = _parse("215-West-Keith-Road")
    print("\n=== 215 West Keith Road (prose in bare <div>) ===")
    show(row)
    print("RAW_TEXT:", repr(row["raw_text"]))

    # The core bug: we now get the application description, and never the footer address.
    assert row["raw_text"].startswith("Golden Line Homes Ltd.")
    assert CITY_HALL not in row["raw_text"]
    assert "townhouse" in row["raw_text"]

    # With real prose in hand we parse deterministically instead of falling back to PDFs.
    assert row["is_parsed"] is True
    assert row["needs_pdf_extraction"] is False

    assert row["address"] == "215 West Keith Road"
    assert row["permit_type"] == "Rezoning"
    assert row["development_class"] == "residential"
    assert row["occupancy_types"] == ["residential"]

    # Spelled-out counts that only appear once we have the real text.
    assert row["units_total"] == 3                       # "three-unit townhouse development"
    assert row["parking_notes"] and "parking stall" in row["parking_notes"].lower()
    print("PASSED ✓")


def test_453_west_24th_fallback():
    """No usable prose (one short line) → keep the PDF link for the Ollama fallback pass."""
    row = _parse("453-West-24th-Street")
    print("\n=== 453 West 24th Street (no usable prose → PDF fallback) ===")
    show(row)

    assert row["is_parsed"] is False
    assert row["needs_pdf_extraction"] is True           # Ollama will read the PDF later
    assert row["needs_review"] is True
    assert row["development_class"] == "unknown"
    assert len(row["documents"]) == 1
    assert row["documents"][0]["url"].lower().endswith(".pdf")
    # Even on the fallback path the footer address must not leak into raw_text.
    assert CITY_HALL not in (row["raw_text"] or "")
    print("PASSED ✓")


def test_link_discovery():
    list_html = """
    <main>
      <h2>Major Applications</h2>
      <a href="/Business-Development/Building/Land-Use-Approvals/Active-Applications/115-East-18th-Street">115 East 18th Street</a>
      <a href="/Business-Development/Building/Land-Use-Approvals/Active-Applications/651-East-1st-Street">651 East 1st Street</a>
      <a href="/Business-Development/Building/Land-Use-Approvals/Active-Applications">Active Applications</a>
    </main>"""
    links = north_van.get_application_links(list_html)
    print("\n=== link discovery ===")
    print(json.dumps(links, indent=2))
    assert len(links) == 2                               # index link excluded
    assert all("/Active-Applications/" in u for u in links)
    print("PASSED ✓")


if __name__ == "__main__":
    test_115_east_18th_happy_path()
    test_215_west_keith_div_prose_regression()
    test_453_west_24th_fallback()
    test_link_discovery()
    print("\nAll North Van harvester tests passed.")