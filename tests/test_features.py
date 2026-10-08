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


def test_number_of_stories_tallest_element_wins():
    # A podium stated before the towers must not hide the towers' height: the project's storey
    # count is its TALLEST element, so the max across all storey figures governs.
    podium_then_towers = ("mixed use four to six storey podiums supported by an expected eight "
                          "residential towers ranging in height from 30 to 40 storeys")
    assert features.number_of_stories(podium_then_towers) == 40
    towers_over_low = ("two 38-storey residential towers over a commercial podium and two "
                       "six-storey rental buildings")
    assert features.number_of_stories(towers_over_low) == 38
    # An "existing" building's height is still skipped in favour of the proposal's.
    assert features.number_of_stories("existing three storey building; proposed six storeys") == 6


def test_units_total_before_a_tenure_breakdown():
    # The grand total stated before "including ..." wins over the itemized sub-figures.
    text = ("66,000 square metres of residential floor area to provide 1,050 residential units, "
            "including 850 market condo units, 110 units of market rental and 125 units of "
            "affordable housing")
    assert features.units_total(text) == 1050


def test_unit_type_mix_ignores_commercial_parking():
    # "110 commercial and hotel parking spaces" is parking, not 110 commercial units.
    text = ("468 residential units and a 145 room hotel. A total of 110 commercial and hotel "
            "parking spaces are also proposed.")
    assert features.unit_type_mix(text) is None
    # An explicit "<N> commercial units" is still counted.
    assert features.unit_type_mix("6 commercial units at grade") == {"commercial": 6}


def test_tenure_mix_splits_ownership_and_rental():
    # The residential total itemized by tenure: condo (ownership) + rental + affordable.
    text = ("1,050 residential units, including 850 market condo units, 110 units of market "
            "rental and 125 units of affordable housing")
    assert features.tenure_mix(text) == {
        "market-condo": 850, "market-rental": 110, "affordable": 125,
    }
    # extract_all surfaces it as unit_mix when there is no bedroom or building-type split.
    assert features.extract_all(text)["unit_mix"] == {
        "market-condo": 850, "market-rental": 110, "affordable": 125,
    }


def test_parking_notes_keeps_full_breakdown():
    # Every parking/bicycle sentence is kept, so a multi-category breakdown is not lost to one
    # headline number.
    text = ("The application includes 144 resident parking spaces and 24 visitor parking spaces. "
            "A total of 110 commercial and hotel parking spaces are also proposed. All off-street "
            "vehicle parking would be below-grade. A total of 712 long-term and 24 short-term "
            "bicycle parking spaces would be provided.")
    notes = features.parking_notes(text)
    assert "144 resident parking" in notes
    assert "110 commercial and hotel parking" in notes
    assert "712 long-term and 24 short-term bicycle" in notes


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