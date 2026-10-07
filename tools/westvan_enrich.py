"""Apply the in-session Claude-read PDF fields to the three West Van example rows.

Run after `python -m bc_dev_permits.dataset --municipality west_van --out json`. The fields
below were extracted by Claude reading each application's priority PDF in-session (Deer Ridge
"Plans"; Ottawa "Staff Report"; Bellevue "Architectural Drawings"), per the Claude-read workflow
in the pdf-parsing memory. apply_claude_pdf reuses the shared merge/conflict/provenance code.
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import west_van as wv

# permit_id -> (claude-read fields, source document) from reading each priority PDF.
CLAUDE_READ = {
    # Plans sheet P-7 shows a 7-level building (Level Seven = penthouse); the rooftop sauna is an
    # accessory structure (height variance 18.2 -> 19.6 m already captured from the HTML).
    "11-3085-deer-ridge-close": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "number_of_stories": 7},
        {"title": "Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/11-3085-deer-ridge-close-plans-for-webpage.pdf"},
    ),
    # Staff Report: RS3 site of 1,414.9 m2 subdivided into two ~706 m2 lots, a single-family
    # dwelling + secondary suite on each lot.
    "1337-ottawa-avenue-rezoning": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "units_total": 2, "unit_mix": {"detached": 2, "secondary-suite": 2},
         "lot_area": 1414.9, "lot_area_unit": "m2", "zoning_density": "RS3 -> reduced site area"},
        {"title": "Staff Report",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/26may11-16.3.pdf"},
    ),
    # Architectural Drawings (RZ101 site plan): three-storey commercial building at #1507 with two
    # parkade levels below; building footprint 899.8 m2 (9,685 sq ft).
    "1507-bellevue-avenue": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "number_of_stories": 3, "footprint_area": 899.8},
        {"title": "Architectural Drawings",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/ARCH%20Dwg-250503-1507%20Bellevue%20-%20REissued%20for%20Rezoning%20%28ARCH%29%20sealed.pdf"},
    ),
    # --- remaining flagged applications (priority PDF read in-session) ---
    # 221 Maple Lane: Proposed Plans - minor addition to the primary dwelling (powder room), a
    # 33.13 sq ft floor-area variance; single-family.
    "221-maple-lane-edp-variance": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1},
        {"title": "Proposed Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/221%20Maple%20Lane%20-%20Proposed%20Plans.pdf"},
    ),
    # 2213 Lawson Avenue: subdivision survey - Lot 16 (RS5) split into two ~529 m2 lots.
    "2213-lawson-avenue-minor-development-variance-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "lot_area": 1057.6, "lot_area_unit": "m2", "zoning_density": "RS5"},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/2213-lawson-ave-survey-topo-wv6123-signed.pdf"},
    ),
    # 2340 Palmerston Avenue: Difficult-Terrain survey - RS5 site ~1,085.3 m2 subdivided into two
    # ~540 m2 lots; existing 1-storey house, two-storey building envelope.
    "2340-palmerston-avenue-difficult-terrains-development": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "number_of_stories": 2, "lot_area": 1085.3, "lot_area_unit": "m2", "zoning_density": "RS5"},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/2340-palmerston-ave-plans-updated.pdf"},
    ),
    # 2512 Haywood Avenue: full Project Data & Zoning Summary - RS-4 single-family, lot 735.5 m2,
    # gross floor area 310.77 m2 (FAR 259.28 m2 / ~0.35, relaxation), height 7.39 m, 2 storeys +
    # basement, 1 dwelling / 1 parking space.
    "2512-haywood-avenue-edp-variance": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "number_of_stories": 2, "height_m": 7.39, "floor_area": 310.77, "floor_area_unit": "m2",
         "lot_area": 735.5, "lot_area_unit": "m2", "zoning_density": "FAR 0.35",
         "parking_vehicle_stalls": 1},
        {"title": "Proposed architecture plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/2512-haywood-avenue_architectural-plans-july-27-2026_for-neighbourhood-notification.pdf"},
    ),
    # 3963 Uplands Way: landscape plan for the Hawksley strata amenity area (DP amendment); no new
    # building program, so only the residential (strata) class is added.
    "3963-uplands-way": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        {"title": "Proposed plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/3963-uplands-way-proposed-plans.pdf"},
    ),
    # 6707 Nelson Avenue: a 2-car private garage within the Sewell's Landing residential parkade.
    "6707-nelson-avenue-minor-development-variance-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "parking_vehicle_stalls": 2},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/260408-sewells-p4-private-garage-1.pdf"},
    ),
    # 8645 Lawrence Way: RS2 single-family, site 2,847.63 m2, total floor area 5,897.76 sq ft
    # (~547.9 m2), three levels (lower/main/upper), FAR 0.35 max.
    "8645-lawrence-way": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "number_of_stories": 3, "floor_area": 547.9, "floor_area_unit": "m2",
         "lot_area": 2847.63, "lot_area_unit": "m2", "zoning_density": "FAR 0.35"},
        {"title": "Proposed Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/23-127%20-%208645%20Lawrence%20Way%20-%20Proposed%20Plans.pdf"},
    ),
    # DP16-055 Rodgers Creek Lane: "Executive Homes at Rodgers Creek Lane", Area 3 East, Lots 21-41
    # - a 21-home single-family development (this amendment redesigns Lots 22-28).
    "DP16-055": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 21,
         "unit_mix": {"detached": 21}},
        {"title": "Architectural Drawings - Part 1 (original proposal)",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Architectural%20Drawings_Part1.pdf"},
    ),
    # 2810/2814/2818 Bellevue Avenue is a foreshore rezoning of an existing waterfront residence;
    # its only PDFs are a landscape plan, a notice flyer and a rezoning map - no building program -
    # so it is intentionally left HTML-only.
}


def main() -> None:
    """Apply the Claude-read fields to data/processed/west_van.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "west_van.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        filled = wv.apply_claude_pdf(row, fields, document)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
