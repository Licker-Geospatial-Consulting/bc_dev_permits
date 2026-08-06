"""
Unit tests for the deterministic field extractors (bc_dev_permits.features).

Focus: reading counts that CNV spells out in prose ("three-unit townhouse", "one
on-site parking stall") without being fooled by a zone *name* that also contains a
number word ("RT-1 (Two Unit Residential) zone").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits import features  # noqa: E402

# The real 215 West Keith Road description.
KEITH = (
    "Golden Line Homes Ltd. has applied for a rezoning from the existing RT-1 "
    "(Two Unit Residential) zone to a new Comprehensive Development zone to allow for "
    "the construction of a three-unit townhouse development. Each townhouse unit will "
    "have one basement suite and one on-site parking stall."
)


def test_units_total_spelled_out_hyphenated():
    assert features.units_total("a three-unit townhouse development") == 3


def test_units_total_ignores_zone_name_false_positive():
    # "Two Unit Residential" is the *existing zone's* name, not the proposal's unit count.
    # The proposal is a three-unit townhouse — that is what units_total must report.
    assert features.units_total(KEITH) == 3


def test_units_total_digit_form_still_works():
    # Existing behaviour must not regress.
    assert features.units_total("Of the proposed 40 residential units, 19 are one-bedroom") == 40
    assert features.units_total("a total of 55 rental units") == 55


def test_units_total_absent_returns_none():
    assert features.units_total("a single-family dwelling") is None


def test_parking_notes_captures_stall_sentence():
    notes = features.parking_notes(KEITH)
    assert notes is not None
    assert "parking stall" in notes.lower()
    assert notes.endswith(".")


def test_parking_notes_underground_still_works():
    text = "Vehicle access to one level of underground parking is provided."
    assert "underground parking" in (features.parking_notes(text) or "")


def test_extract_all_on_keith():
    out = features.extract_all(KEITH)
    assert out["permit_type"] == "Rezoning"
    assert out["development_class"] == "residential"
    assert out["occupancy_types"] == ["residential"]
    assert out["units_total"] == 3
    assert out["parking_notes"] and "parking stall" in out["parking_notes"].lower()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} PASSED ✓")
    print("\nAll features tests passed.")