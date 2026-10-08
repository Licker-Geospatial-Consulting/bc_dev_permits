"""
Tests for the shared address-consolidation rule (bc_dev_permits/consolidate.py).

The rule collapses rows at one civic address into one record per distinct project. The cases
below exercise its three behaviors plus the "leave distinct developments alone" guard:

  * thin record folded into the strong record at the same address (125 McDonald)
  * two strong filings of ONE project (same program) folded together (OPA + Subdivision, 1205 units)
  * two strong records with DIFFERENT programs at one address left separate
  * punctuation-only address differences treated as the same address ("Blvd." vs "Blvd")

Run:  python -m pytest -q tests/test_consolidate.py   (or:  python -m tests.test_consolidate)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import consolidate  # noqa: E402


def _row(pid, addr, method="arcgis", **fields):
    row = {"permit_id": pid, "address": addr, "extraction_method": method,
           "latitude": None, "longitude": None, "raw_text": pid, "milestones": []}
    row.update(fields)
    return row


def test_address_key_normalizes_punctuation():
    """'125 McDonald Blvd.' and '125 McDonald Blvd' share one key; a numberless address has none."""
    assert consolidate.address_key("125 McDonald Blvd.") == consolidate.address_key("125 McDonald Blvd")
    assert consolidate.address_key("Rodgers Creek Lane") is None   # no civic number
    assert consolidate.address_key(None) is None
    print("\naddress_key PASSED ✓")


def test_thin_record_folds_into_strong():
    """A programless Site Plan folds into the Subdivision at the same address, as a related permit."""
    rows = [
        _row("D12SUB12.001", "125 McDonald Blvd.", units_total=108),       # strong
        _row("D11SPA21.001", "125 McDonald Blvd", status="Under Review"),   # thin (no program)
    ]
    dropped = consolidate.consolidate_by_address(rows)
    assert dropped == 1
    assert len(rows) == 1
    survivor = rows[0]
    assert survivor["permit_id"] == "D12SUB12.001"          # the strong record is kept
    assert survivor["units_total"] == 108
    assert [r["permit_id"] for r in survivor["related_permits"]] == ["D11SPA21.001"]
    assert "Related applications at this address" in survivor["raw_text"]
    print("\nthin-into-strong PASSED ✓")


def test_same_project_strong_filings_merge():
    """An OPA and a Subdivision at one address both stating 1205 units are one project -> merged."""
    rows = [
        _row("D14ZBA21.003", "10332 Eighth Line", units_total=1205),
        _row("D12SUB21.002", "10332 Eighth Line", units_total=1205),
    ]
    dropped = consolidate.consolidate_by_address(rows)
    assert dropped == 1
    assert len(rows) == 1
    assert rows[0]["units_total"] == 1205
    assert len(rows[0]["related_permits"]) == 1
    print("\nsame-project-merge PASSED ✓")


def test_distinct_strong_developments_stay_separate():
    """Two strong records at one address with DIFFERENT programs are genuinely distinct -> kept."""
    rows = [
        _row("A", "500 Main St", units_total=40, number_of_stories=4),
        _row("B", "500 Main St", units_total=200, number_of_stories=20),
    ]
    dropped = consolidate.consolidate_by_address(rows)
    assert dropped == 0
    assert len(rows) == 2
    print("\ndistinct-stay-separate PASSED ✓")


if __name__ == "__main__":
    test_address_key_normalizes_punctuation()
    test_thin_record_folds_into_strong()
    test_same_project_strong_filings_merge()
    test_distinct_strong_developments_stay_separate()
    print("\nAll consolidate tests passed.")
