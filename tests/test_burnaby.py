"""City of Burnaby (single Major Development Projects PDF) harvester tests.

The fixture is a trimmed-but-real two-page slice of the city's Major Development Projects PDF
(burnaby_major_projects.pdf). The table is ruled, so pymupdf's find_tables() recovers the columns
directly - these tests lock in the column->field mapping, the Type-keyword classification, the
strata/rental unit counts, and the status->milestone parse.

Do NOT edit the fixture to make a test pass - fix the parser instead.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.harvesters import burnaby

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROWS = burnaby.parse_pdf((FIXTURES / "burnaby_major_projects.pdf").read_bytes())
BY_ID = {r["permit_id"]: r for r in ROWS}


def test_rows_parsed_with_ids_and_no_header_rows():
    assert len(ROWS) >= 15
    assert all(r["municipality"] == "burnaby" for r in ROWS)
    assert all(r["permit_id"] for r in ROWS)
    # The repeated "Rezoning" header row and the "CITY OF BURNABY" banner are not rows.
    assert not any("rezoning" == (r["permit_id"] or "").lower() for r in ROWS)
    assert all(r["source_url"] == burnaby.PDF_URL for r in ROWS)


def test_type_is_permit_type_and_rezoning_is_id():
    r = BY_ID["REZ #17-10017"]
    assert r["permit_type"] == "Residential"
    assert r["address"] == "5325 and 5385 Kincaid Street"
    assert r["development_class"] == "residential"
    assert r["units_total"] == 18          # "18 Lots" in the strata column
    assert r["rental_or_strata"] == "strata"


def test_type_keyword_classifies_industrial_over_commercial_component():
    # "...industrial/office building with a minor commercial component" -> industrial, not commercial.
    r = BY_ID["REZ #22-25"]
    assert r["permit_type"] == "Industrial"
    assert r["development_class"] == "industrial"
    assert r["number_of_stories"] == 6
    assert r["units_total"] is None        # "N/A" in both unit columns


def test_units_total_sums_mixed_strata_and_rental():
    # 267 strata + 355 rental (no prose total on this row) -> 622 (a mixed project is additive).
    r = BY_ID["REZ #22-29"]
    assert r["development_class"] == "residential"
    assert r["number_of_stories"] == 20    # tallest ("one mid-rise (20 storey) ...")
    assert r["units_total"] == 622         # 267 strata + 355 rental
    assert r["milestones"] == [
        {"milestone": "Third Reading", "milestone_date": "2024-06-24"}
    ]


def test_unit_mix_captures_both_strata_and_rental_breakdowns():
    # 3405 Willingdon: strata total + the rental tenure split, with distinct keys.
    assert BY_ID["REZ #22-29"]["unit_mix"] == {
        "strata": 267, "market rental": 213, "non-market rental": 142,
    }
    # 3588 Wayburne: a strata townhouse bedroom mix AND a rental apartment bedroom mix together.
    assert BY_ID["REZ #25-16"]["unit_mix"] == {
        "2-bed townhouse": 5, "3-bed townhouse": 70, "3+-bed townhouse": 8,
        "studio market rental": 12, "1-bed market rental": 66,
        "2-bed market rental": 45, "3-bed market rental": 3,
    }


def test_status_label_strips_the_date():
    assert burnaby._status_label("Second Reading 2019.02.11") == "Second Reading"
    assert burnaby._status_label("Final Adoption 2026.02.25") == "Final Adoption"
    assert burnaby._status_label(None) is None


def test_cell_breakdown_and_total():
    assert burnaby._mix_from_cell("213 units Market Rental 91 units Non-Mkt Rental") == {
        "market rental": 213, "non-market rental": 91}
    assert burnaby._cell_total("5 2-Bedroom Twnhouse 70 3-Bedroom Twnhouse 8 3+ Bedroom Twnhouse") == 83
    assert burnaby._mix_from_cell("18 Lots") == {"lots": 18}
    assert burnaby._cell_total("N/A") is None
    assert burnaby._desc_total("A total of 622 residential dwelling units are proposed") == 622
