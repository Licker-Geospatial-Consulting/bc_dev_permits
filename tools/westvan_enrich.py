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
    # --- corrections from the user's review (project data read from report / data sheets) ---
    # 2810/2814/2818 Bellevue: foreshore rezoning of three waterfront residences; the meeting
    # notice lists each house's floor area (228.2 / 283.7 / 399.8 m2 -> 911.7 m2 total).
    "2810-2814-2818-bellevue-avenue": (
        {"units_total": 3, "floor_area": 911.7, "floor_area_unit": "m2"},
        {"title": "Meeting notice and information flyer",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/2810-2814-and-2818-bellevue-avenue-notice-and-information-package.pdf"},
    ),
    # 3181/3389 Chippendale & 3861/3875 Uplands Way (Beedie): a four-building strata rezoning - 20
    # storey 63 units, 25 storey 93 units, 16 storey 81 units, 20 storey 87 units (324 units,
    # ~505,000 sq ft -> ~46,900 m2). The per-building list is in the page's prose.
    "3181-3389-chippendale-rd-3861-3875-uplands-way-beedie": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 324,
         "number_of_stories": 25, "floor_area": 46914.0, "floor_area_unit": "m2",
         "rental_or_strata": "strata"},
        None,
    ),
    # 376 Moyne Drive: RS3, lot 1,887.5 m2, 2 dwelling units (primary + the proposed detached
    # secondary suite / coach house of 290.91 m2), FAR 0.26, 2 parking.
    "376-moyne-drive-coach-house-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "unit_mix": {"principal": 1, "secondary-suite": 1}, "floor_area": 290.91,
         "floor_area_unit": "m2", "lot_area": 1887.5, "lot_area_unit": "m2",
         "zoning_density": "FAR 0.26", "parking_vehicle_stalls": 2, "height_m": 4.56},
        {"title": "Proposed Architectural Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/376%20Moyne%20Dr%20-%20Proposed%20Architectural%20Drawings.pdf"},
    ),
    # 875-885 Jefferson Avenue: Report to Council - rezoning RS3 -> RS5 and subdivision of the two
    # lots (2,240.3 m2 total) into four single-family lots, ~200-204 m2 allowable floor area each.
    "875-885-jefferson-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 4,
         "unit_mix": {"detached": 4}, "lot_area": 2240.3, "lot_area_unit": "m2",
         "floor_area": 810.2, "floor_area_unit": "m2", "zoning_density": "RS3 -> RS5"},
        {"title": "Report to Council: June 26, 2023", "url": "https://westvancouver.ca/media/2477"},
    ),
    # 1248 Keith Road: a new duplex (2 units), lot 592.8 m2.
    "1248-keith-road-duplex-dp": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 2,
         "lot_area": 592.8, "lot_area_unit": "m2"},
        {"title": "Proposed architectural plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/1248-keith-road_dp-revision-092526-architectural-plans.pdf"},
    ),
    # 2833 Chippendale Road: a 12-unit townhouse development (HTML), total floor area ~1,755 m2
    # (FAR 0.6); all but two units are 3-bedroom.
    "2833-chippendale-road-rodgers-creek-area": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 12,
         "unit_mix": {"3-bed": 10}, "floor_area": 1755.0, "floor_area_unit": "m2",
         "zoning_density": "FAR 0.6"},
        {"title": "Report to Council: June 5, 2023", "url": "https://westvancouver.ca/media/2330"},
    ),
    # 4020 Marine Drive: a Minor DVP adding one coach house.
    "4020-marine-drive-minor-development-variance-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "unit_mix": {"coach-house": 1}},
        {"title": "Plans", "url": "https://westvancouver.ca/sites/default/files/media/documents/4020-marine-drive-plans-updated-sep-2026.pdf"},
    ),
    # 4310 Marine Drive (Ferndale Townhomes): RS4 -> CD multi-family, 9 units (8x 3-bed, 1x 2-bed),
    # 1,863.8 m2 GFA, FAR 0.86, 3 levels, lot 2,177.4 m2, 18 vehicle + 18 bike stalls.
    "4310-marine-drive": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 9,
         "unit_mix": {"3-bed": 8, "2-bed": 1}, "number_of_stories": 3, "floor_area": 1863.8,
         "floor_area_unit": "m2", "lot_area": 2177.4, "lot_area_unit": "m2",
         "zoning_density": "FAR 0.86", "parking_vehicle_stalls": 18, "parking_bike_stalls": 18},
        {"title": "Architectural plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/Architectural%20Drawings%20-%204310%20Marine%20Dr.pdf"},
    ),
    # 6330 & 6332 Argyle Avenue: six new ground-oriented residential units in three blocks;
    # ~9,321.55 sq ft (866 m2), FSR 0.714.
    "6330-6332-argyle-avenue-minor-development-permit": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 6,
         "number_of_stories": 3, "floor_area": 866.0, "floor_area_unit": "m2",
         "zoning_density": "FSR 0.714"},
        {"title": "Proposed Plans",
         "url": "https://westvancouver.ca/sites/default/files/media/documents/6330%20Argyle%20250416-COM.pdf"},
    ),
    # 3190 Travers Avenue (Roy Residence): RS4 single-family dwelling, site 1,578.42 m2; the Minor
    # DVP is for waterfront accessory structures (terraces/trellis) - the existing house is unchanged.
    "3190-travers-avenue": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 1,
         "lot_area": 1578.42, "lot_area_unit": "m2", "zoning_density": "RS4"},
        {"title": "Plans", "url": "https://westvancouver.ca/media/12117"},
    ),
}

# Per-building detail appended to raw_text for the multi-building applications.
BREAKDOWNS = {
    "875-885-jefferson-avenue": (
        "Proposed four single-family lots: 875-A 565.3 m2, 875-B 575.7 m2, 885-A 544.5 m2, "
        "885-B 554.8 m2 (RS3 minimum is 975 m2). Allowable floor area ~204.4 m2 per 875 lot and "
        "~200.7 m2 per 885 lot."
    ),
    "2810-2814-2818-bellevue-avenue": (
        "Floor area by address: 2810 Bellevue 228.2 m2 (2,456 sq ft); 2814 Bellevue 283.7 m2 "
        "(3,054 sq ft); 2818 Bellevue 399.8 m2 (4,304 sq ft)."
    ),
    "3181-3389-chippendale-rd-3861-3875-uplands-way-beedie": (
        "Proposed buildings: Lot 1 / 3181 Chippendale Rd - 20 storeys, ~108,000 sq ft, 63 strata "
        "units; Lot 9 / 3389 Chippendale Rd - 25 storeys, ~150,000 sq ft, 93 strata units; Lot 12 "
        "/ 3875 Uplands Way - 16 storeys, ~111,000 sq ft, 81 strata units (converted from rental); "
        "Lot 13 / 3861 Uplands Way - 20 storeys, ~136,000 sq ft, 87 strata units over a shared parkade."
    ),
}


# HTML fields to clear before enrichment because the regex misfired on a zoning LIMIT rather than
# the proposal (so the Claude-read proposal value fills cleanly instead of losing to a bad parse).
OVERRIDE_HTML = {
    "3181-3389-chippendale-rd-3861-3875-uplands-way-beedie": ["number_of_stories"],  # "16-storey limit" -> 25
    "6330-6332-argyle-avenue-minor-development-permit": ["number_of_stories"],
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
        for field in OVERRIDE_HTML.get(permit_id, []):
            row.pop(field, None)
            (row.get("field_methods") or {}).pop(field, None)
        filled = wv.apply_claude_pdf(row, fields, document)
        note = BREAKDOWNS.get(permit_id)
        if note:
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
