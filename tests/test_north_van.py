"""
Test harvester for the North Vancouver HTML scraper.

Runs the deterministic parser against saved fixtures (the container can't reach
cnv.org, and this keeps the test hermetic anyway). Fixtures are built from the real
115 East 18th Street page and a no-prose fallback page.

Run:  python -m tests.test_north_van       (from the project root)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import north_van  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return (FIX / name).read_text(encoding="utf-8")


def show(row):
    printable = {k: v for k, v in row.items() if k not in ("raw_text",)}
    print(json.dumps(printable, indent=2, ensure_ascii=False))


def test_115_east_18th():
    url = "https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications/115-East-18th-Street"
    row = north_van.parse_detail(_load("115_east_18th.html"), url)
    print("\n=== 115 East 18th Street (deterministic HTML parse) ===")
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
    assert len(row["documents"]) == 3
    assert {m["milestone"] for m in row["milestones"]} >= {"Application Accepted"}
    assert row["milestones"][0]["milestone_date"] == "2025-10-23"
    print("PASSED ✓")


def test_651_east_1st_fallback():
    url = "https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Active-Applications/651-East-1st-Street"
    row = north_van.parse_detail(_load("651_east_1st.html"), url)
    print("\n=== 651 East 1st Street (no prose -> PDF fallback) ===")
    show(row)

    assert row["is_parsed"] is False
    assert row["needs_pdf_extraction"] is True          # Ollama will read the PDF later
    assert row["needs_review"] is True
    assert row["development_class"] == "unknown"
    assert len(row["documents"]) == 1
    assert row["documents"][0]["url"].endswith(".PDF")
    assert row["development_name"] == "The Trails Future Phases Development Application"
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
    test_115_east_18th()
    test_651_east_1st_fallback()
    test_link_discovery()
    print("\nAll North Van harvester tests passed.")