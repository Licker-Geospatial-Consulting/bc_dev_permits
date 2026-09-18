"""Deterministic tests for the stat-card geometry backfill (text path) in predict.

Consultant summary sheets present the key numbers in a graphic 'stat card' - a value stacked
in its label's column - which linear PDF text extraction pulls apart. These tests cover the
pure pairing (label -> column-aligned number), the page picker, and the backfill rules
(fill-only-missing, combine bike sub-totals). The real fixture is the Amphion (DPV00291)
summary page whose figures the text model missed: 35,198 sf, 2 car, 6 short + 59 long bike.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bc_dev_permits.modeling import predict  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "amphion_stat_page_words.json"


def _amphion_words() -> list[tuple]:
    return [tuple(w) for w in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def _w(x0: float, y0: float, text: str, block: int, line: int) -> tuple:
    """Build one get_text('words') tuple: (x0, y0, x1, y1, text, block, line, wordno)."""
    return (float(x0), float(y0), float(x0 + 8 * len(text)), float(y0 + 12), text, block, line, 0)


# --------------------------------------------------------------------------- #
# pure pairing on the REAL Amphion stat card
# --------------------------------------------------------------------------- #
def test_pair_stats_from_words_real_amphion_stat_card():
    assert predict._pair_stats_from_words(_amphion_words()) == {
        "floor_area": 35198,
        "parking_vehicle_stalls": 2,
        "bike_short_stalls": 6,
        "bike_long_stalls": 59,
    }


def test_backfill_stats_recovers_amphion_and_keeps_model_values():
    fields = {
        "units_total": 42,
        "number_of_stories": 6,
        "floor_area": None,
        "parking_vehicle_stalls": None,
        "parking_bike_stalls": None,
        "parking_notes": None,
    }
    out = predict._backfill_stats(fields, _amphion_words())
    assert out["floor_area"] == 35198
    assert out["parking_vehicle_stalls"] == 2
    assert out["parking_bike_stalls"] == 65  # 6 short + 59 long, combined per schema convention
    assert "59 long-term" in out["parking_notes"] and "6 short-term" in out["parking_notes"]
    assert out["units_total"] == 42 and out["number_of_stories"] == 6  # model values untouched


# --------------------------------------------------------------------------- #
# backfill rules: fill only what the model missed, never overwrite
# --------------------------------------------------------------------------- #
def test_backfill_stats_never_overwrites_present_model_values():
    fields = {
        "floor_area": 999,
        "parking_vehicle_stalls": 7,
        "parking_bike_stalls": 12,
        "parking_notes": "model note",
    }
    assert predict._backfill_stats(dict(fields), _amphion_words()) == fields


def test_backfill_stats_fills_only_the_missing_fields():
    fields = {
        "floor_area": 999,  # present -> kept
        "parking_vehicle_stalls": None,  # missing -> filled from geometry
        "parking_bike_stalls": None,
        "parking_notes": None,
    }
    out = predict._backfill_stats(fields, _amphion_words())
    assert out["floor_area"] == 999
    assert out["parking_vehicle_stalls"] == 2
    assert out["parking_bike_stalls"] == 65


def test_backfill_stats_noop_without_words():
    fields = {"floor_area": None, "parking_vehicle_stalls": None, "parking_bike_stalls": None}
    assert predict._backfill_stats(dict(fields), []) == fields


# --------------------------------------------------------------------------- #
# pairing must ignore numbers outside the label's column / vertical window
# --------------------------------------------------------------------------- #
def test_pair_stats_ignores_numbers_outside_column_or_window():
    words = [
        _w(648, 337, "Floor", 1, 0),
        _w(690, 337, "Area", 1, 0),
        _w(648, 352, "35198", 1, 1),  # directly below -> the floor area
        _w(648, 470, "3.20", 2, 0),  # same column but far below (FSR) -> out of dy window
        _w(900, 350, "77", 3, 0),  # right vertical band but a different column -> out of x tol
    ]
    assert predict._pair_stats_from_words(words) == {"floor_area": 35198}


# --------------------------------------------------------------------------- #
# page picker: choose the stat card, not a prose page that names a label once
# --------------------------------------------------------------------------- #
class _FakePage:
    def __init__(self, text: str, words: list):
        self._text, self._words = text, words

    def get_text(self, kind: str = "text"):
        return self._words if kind == "words" else self._text


def test_find_stat_page_words_prefers_richest_page_over_prose_mention():
    prose = _FakePage("The building offers generous floor area for residents.", ["PROSE"])
    card = _FakePage("Floor Area  Car Parking Stalls  Long-term Bike Stalls", ["CARD"])
    assert predict._find_stat_page_words([prose, card]) == ["CARD"]


def test_find_stat_page_words_skips_page_below_two_labels():
    prose = _FakePage("mentions floor area only, once", ["PROSE"])
    assert predict._find_stat_page_words([prose]) == []


# --------------------------------------------------------------------------- #
# per-field provenance: deterministic sources beat the model, each field tagged
# --------------------------------------------------------------------------- #
def test_merge_by_precedence_deterministic_beats_model():
    merged, methods = predict._merge_by_precedence([
        ("pdf_text_regex", {"units_total": 30, "development_class": "residential"}),
        ("pdf_geometry", {"floor_area": 35198}),
        ("pdf_model_text", {"units_total": 99, "parking_bike_stalls": 12}),
    ])
    assert merged["units_total"] == 30  # regex wins over the model's 99
    assert methods["units_total"] == "pdf_text_regex"
    assert methods["floor_area"] == "pdf_geometry"
    assert merged["parking_bike_stalls"] == 12  # only the model had it
    assert methods["parking_bike_stalls"] == "pdf_model_text"


def test_merge_by_precedence_skips_empty_and_meta():
    merged, methods = predict._merge_by_precedence([
        ("pdf_text_regex", {"units_total": None, "development_class": "unknown", "confidence": 0.9}),
        ("pdf_model_text", {"units_total": 12}),
    ])
    assert merged["units_total"] == 12  # regex None/unknown are not values, model fills
    assert methods["units_total"] == "pdf_model_text"
    assert "confidence" not in methods and "development_class" not in merged


def test_tag_all_stamps_every_real_field():
    out = predict._tag_all(
        {"units_total": 9, "development_class": "unknown", "confidence": 0.4}, "pdf_model_vision"
    )
    assert out["field_methods"] == {"units_total": "pdf_model_vision"}


def test_stats_fields_returns_geometry_values():
    fields = predict._stats_fields(_amphion_words())
    assert fields["floor_area"] == 35198
    assert fields["parking_vehicle_stalls"] == 2
    assert fields["parking_bike_stalls"] == 65
    assert "59 long-term" in fields["parking_notes"]
