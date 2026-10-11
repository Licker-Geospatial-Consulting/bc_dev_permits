"""Apply the in-session Claude-read PDF fields to the UBC example rows.

Run after `python -m bc_dev_permits.dataset --municipality ubc --out json`. The fields below were
extracted by Claude reading each project's priority PDF in-session (bcr7 "Development Permit Board
Report"; WPS Lots 8 & 9 "Architectural Plans" project-data sheet; Kenny "Architectural Plans"
cover sheet), per the Claude-read workflow in the pdf-parsing memory. ubc.apply_claude_pdf reuses
the shared merge / conflict / provenance code: deterministic HTML is never overwritten and a real
HTML-vs-PDF disagreement is logged to row['conflicts'].
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import ubc

# permit_id -> (claude-read fields, source document) from reading each priority PDF.
CLAUDE_READ = {
    # Development Permit Board Report: 303 units = 189 in the 14-storey market-rental tower + 114
    # faculty/staff in four 6-storey buildings. Parking: 240 resident + 32 visitor + 31 accessible
    # = 303 vehicle stalls; 675 Class I (long-term) + 70 Class II (visitor) = 745 bicycle stalls.
    "DP24018": (
        {"rental_or_strata": "rental",
         "unit_mix": {"market-rental": 189, "faculty-staff-rental": 114},
         "parking_vehicle_stalls": 303, "parking_bike_stalls": 745},
        {"title": "Development Permit Board Report",
         "url": "https://planning.ubc.ca/sites/default/files/2024-07/DPBoard%20Report%20Lot%20BCR7.pdf"},
    ),
    # Architectural project-data sheet (A002): Lot 8 (30-storey tower) + Lot 9 (6-storey) combine to
    # 495 units. Floor area 31,749 m2 (Lot 8 23,956 + Lot 9 7,793); vehicle parking 201 (154 + 47);
    # Class I bicycle 845 (641 + 204). FSR 10.13 (tower) / 1.85 (Lot 9).
    "DP26024": (
        # unit_mix stays the HTML "495 market rental" (deterministic); the bedroom split
        # (335 studio / 86 one-bed / 18 two-bed / 56 three-bed) is kept in the note below.
        {"units_total": 495,
         "parking_vehicle_stalls": 201, "parking_bike_stalls": 845,
         "floor_area": 31749.0, "floor_area_unit": "m2", "zoning_density": "FSR 10.13",
         "occupancies": [
             {"occupancy": "commercial", "floor_area": 1518.0, "floor_area_unit": "m2"},
             {"occupancy": "institutional", "floor_area": 1592.0, "floor_area_unit": "m2",
              "detail": "child care"},
             {"occupancy": "residential", "floor_area": 28639.0, "floor_area_unit": "m2"},
         ]},
        {"title": "Architectural Plans",
         "url": "https://planning.ubc.ca/sites/default/files/2026-07/20260703_Lots%208%20and%209_Architectural%20Drawings.pdf"},
    ),
    # Architectural cover sheet: a targeted SEISMIC RENOVATION of the Douglas T. Kenny (Psychology)
    # academic building - building area 2,750.9 m2, 0 new dwelling units, 0 new parking (to match
    # existing), 4 existing Class 2 bicycle spaces. An institutional (academic) building.
    "DP25042": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "parking_vehicle_stalls": 0},
        {"title": "Architectural Plans",
         "url": "https://planning.ubc.ca/sites/default/files/2026-01/25.12.19%20-%20UBCKTSU%20-%20Architectural.pdf"},
    ),
}

# Longer free-text context appended to raw_text (breakdowns a single scalar can't hold).
NOTES = {
    "DP24018": "Parking (DP Board Report): 240 resident + 32 visitor (incl 4 EV, 4 accessible) + 31 "
               "accessible = 303 vehicle stalls; 675 Class I (long-term) + 70 Class II (visitor) "
               "bicycle stalls. Units: 189 in the 14-storey market-rental tower + 114 faculty/staff "
               "in four 6-storey buildings; 56% studio/1/2-bed, 44% 3/4-bed.",
    "DP26024": "Project data (A002): Lot 8 - 30-storey tower, 371 units, FSR 10.13, 154 vehicle + "
               "666 Class I bike stalls; Lot 9 - 6-storey, 124 units, FSR 1.85, 47 vehicle + 204 "
               "Class I bike stalls. Combined 495 units, 201 vehicle stalls, floor area 31,749 m2.",
    "DP25042": "Targeted seismic renovation (building area 2,750.9 m2): 0 new dwelling units, 0 new "
               "parking (to match existing), 4 existing Class 2 bicycle spaces.",
}


def main() -> None:
    """Apply the Claude-read fields to data/processed/ubc.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "ubc.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        filled = ubc.apply_claude_pdf(row, fields, document)
        note = NOTES.get(permit_id)
        if note:
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
