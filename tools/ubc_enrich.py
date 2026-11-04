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
    # ANSO Renewal (DP24027 Amendment 2) - MAC expansion, an academic (Anthropology & Sociology)
    # building. Perkins&Will PROJECT INFORMATION sheet (G00-01). Gross floor area (6,681 m2) is
    # already captured from the HTML, so only the fields the HTML lacks are added here.
    "DP24027": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "lot_area": 4708.2, "lot_area_unit": "m2", "parking_bike_stalls": 50,
         "occupancies": [{"occupancy": "institutional", "floor_area": 6681.7,
                          "floor_area_unit": "m2", "detail": "academic (Anthropology & Sociology)"}]},
        {"title": "Architectural - Issued for DP",
         "url": "https://planning.ubc.ca/sites/default/files/2024-09/UBC%20ANSO%20-%20Architectural%20-%20Issued%20for%20DP.pdf"},
    ),
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
    # --- batch reads: project-data / cover sheets read in-session from each project's PDF ---
    # UBC Exchange / Gage South Residence - a student residence over a transit exchange facility
    # (4 buildings A-D). Project data sheet (A000).
    "gage-south-student-residence-and-diesel-bus-transit-exchange": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 362,
         "unit_mix": {"nano": 71, "studio": 93, "1-bed": 77, "2-bed": 37, "4-bed": 84},
         "number_of_stories": 14, "height_m": 43.20, "floor_area_ratio": 1.12, "lot_area": 22285.0, "lot_area_unit": "m2", "lot_coverage": 33.8,
         "parking_vehicle_stalls": 0, "parking_bike_stalls": 624},
        {"title": "Architectural - Transit Exchange / Student Residence",
         "url": "https://planning.ubc.ca/sites/default/files/2019-12/04473V%202016-09-23%20Reissued%20for%20DP-Arch%20Transit%20Exchange.pdf"},
    ),
    # Totem Park Residence Infill Phase 2 - a student residence. Cover / project statistics (A0.00).
    "totem-park-residence-infill-phase-2": (
        {"development_class": "residential", "occupancy_types": ["residential"], "units_total": 354,
         "unit_mix": {"single-room": 340, "resident-advisor": 12, "coordinator-suite": 2},
         "number_of_stories": 6, "height_m": 19.9, "floor_area_ratio": 1.69, "lot_area": 5237.0, "lot_area_unit": "m2", "lot_coverage": 37.0,
         "parking_vehicle_stalls": 33, "parking_bike_stalls": 354},
        {"title": "Cover - Project Statistics",
         "url": "https://planning.ubc.ca/sites/default/files/2019-12/DP15032%20Cover%20-%20Project%20Statistics.pdf"},
    ),
    # Undergrad Life Sciences Teaching Labs (Biological Sciences addition) - an academic building.
    "biological-sciences-building-ubc-undergraduate-life-sciences": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "floor_area": 14244.0, "floor_area_unit": "m2", "parking_bike_stalls": 120},
        {"title": "Architectural - Cover Sheet",
         "url": "https://planning.ubc.ca/sites/default/files/2019-12/DP15038%20Biosciences%20Addition%20-%20Arch%20-%20May%209%20Resubmission_27.pdf"},
    ),
    # Wayne and William White Engineering Design Centre - an academic (engineering) building.
    "wayne-and-william-white-engineering-design-centre-edc": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 3, "floor_area": 1922.0, "floor_area_unit": "m2",
         "floor_area_ratio": 1.12, "lot_area": 1685.0, "lot_area_unit": "m2", "lot_coverage": 44.0,
         "height_m": 14.3, "footprint_area": 742.0, "parking_bike_stalls": 24},
        {"title": "Cover + Site",
         "url": "https://planning.ubc.ca/sites/default/files/2020-02/EDC%20Cover%2BSite.pdf"},
    ),
    # UBC LIFE Building (AMS student union + Norm Theatre) renewal - an institutional building.
    "life-building": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 2, "floor_area": 15705.0, "floor_area_unit": "m2",
         "parking_vehicle_stalls": 0, "parking_bike_stalls": 252},
        {"title": "Project Statistics + Area Schedules + Site Plan",
         "url": "https://planning.ubc.ca/sites/default/files/2020-02/lb%20Project%20Statistics%20%2B%20Area%20Schedules%20%2B%20Site%20Plan%20%2B%20Existing%20Site%20Photos.pdf"},
    ),
    # UBC Rugby Pavilion (McGavin Rugby Centre) Phase One - a sports/recreation pavilion.
    "mcgavin-rugby-centre": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "parking_bike_stalls": 24},
        {"title": "Cover Sheet",
         "url": "https://planning.ubc.ca/sites/default/files/2020-02/rugby%20phase%202.pdf"},
    ),
    # UBC Recreation Centre North - a recreation (assembly) building.
    "rec-north": (
        {"development_class": "institutional", "occupancy_types": ["institutional"]},
        {"title": "Architectural - Project Data and Notes",
         "url": "https://planning.ubc.ca/sites/default/files/2022-06/220303%20-%20UBC%20REC%20NORTH%20-%20Re-Issued%20for%20DP%20-%20Architectural.pdf"},
    ),
    # Museum of Anthropology - Great Hall & Skylight seismic upgrade - a museum (institutional).
    "museum-anthropology-great-hall-renewal": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "floor_area": 697.0, "floor_area_unit": "m2", "height_m": 15.54},
        {"title": "Architectural - Cover / Drawing Index",
         "url": "https://planning.ubc.ca/sites/default/files/2020-06/2020_06_05%20-%20Architectural.pdf"},
    ),
    # Hebb Building Upgrade - seismic upgrade & renewal of the Physics & Astronomy academic building.
    "hebb-seismic-upgrade-and-landscape-renewal": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 6, "floor_area": 6135.37, "floor_area_unit": "m2",
         "lot_area": 1957.30, "lot_area_unit": "m2", "height_m": 21.58},
        {"title": "Architectural - Cover",
         "url": "https://planning.ubc.ca/sites/default/files/2019-12/DP17021%20Hebb%20Building%20Upgrade.pdf"},
    ),
    # Wesbrook Place Lot 3 (Webber House) - a faculty residential development.
    "wesbrook-place-lot-3-webber-house-formerly-lot-45-village": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "lot_area": 1430.0, "lot_area_unit": "m2"},
        {"title": "DP14010 June 2015 Resubmission",
         "url": "https://planning.ubc.ca/sites/default/files/2019-12/DP14010%20June%202015%20Resubmission_WesbrookPlaceLot3Webber.pdf"},
    ),
    # UBC Pump House replacement - a water pump station (Group F industrial / utility building).
    "ubc-pump-house-replacement": (
        {"development_class": "industrial", "occupancy_types": ["industrial"],
         "number_of_stories": 2, "floor_area": 155.6, "floor_area_unit": "m2",
         "floor_area_ratio": 0.32, "lot_area": 485.15, "lot_area_unit": "m2", "height_m": 10.09},
        {"title": "Architectural - Sheet List and Statistics",
         "url": "https://planning.ubc.ca/sites/default/files/2019-12/UBC-PH_Issuefordp_Architectural.pdf"},
    ),
    # Third Space Commons (Solar Decathlon 2023 entry, now a campus collaborative design hub) - an
    # assembly building.
    "solar-decathlon-third-space-temporary-building": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 1, "height_m": 7.85},
        {"title": "DP Drawings",
         "url": "https://planning.ubc.ca/sites/default/files/2022-04/Third%20Quadrant%20DP%20Drawings%20MN%20Signed.pdf"},
    ),
}

# Longer free-text context appended to raw_text (breakdowns a single scalar can't hold).
NOTES = {
    "DP24027": "Architect PROJECT INFORMATION (UBC ANSO Renewal, 6303 NW Marine Dr): gross floor "
               "area 6,681.7 m2 (L00 1,069.6 / L01 2,768.6 / L02 2,280.4 / L03 563.1), site "
               "4,708.2 m2 (78.5 x 113.1 m), site coverage 59.0%; bicycle parking 50 (Class A 26 + "
               "Class B 24); roof-level geodetic elevations 67.7-78.6 m; loading from Cecil Green "
               "Park Road (no on-site vehicle parking stated).",
    "DP24018": "Parking (DP Board Report): 240 resident + 32 visitor (incl 4 EV, 4 accessible) + 31 "
               "accessible = 303 vehicle stalls; 675 Class I (long-term) + 70 Class II (visitor) "
               "bicycle stalls. Units: 189 in the 14-storey market-rental tower + 114 faculty/staff "
               "in four 6-storey buildings; 56% studio/1/2-bed, 44% 3/4-bed.",
    "DP26024": "Project data (A002): Lot 8 - 30-storey tower, 371 units, FSR 10.13, 154 vehicle + "
               "666 Class I bike stalls; Lot 9 - 6-storey, 124 units, FSR 1.85, 47 vehicle + 204 "
               "Class I bike stalls. Combined 495 units, 201 vehicle stalls, floor area 31,749 m2.",
    "DP25042": "Targeted seismic renovation (building area 2,750.9 m2): 0 new dwelling units, 0 new "
               "parking (to match existing), 4 existing Class 2 bicycle spaces.",
    "gage-south-student-residence-and-diesel-bus-transit-exchange": (
        "Project data (UBC Exchange / Gage South Residence, 5960 Student Union Blvd): a transit "
        "exchange facility + student residence; site 22,285 m2, site coverage 33.8%, FSR 1.12; gross "
        "floor area 27,263.1 m2; 362 dwelling units / 651 beds (71 nano / 93 studio / 77 one-bed / "
        "19 two-bed / 84 four-bed / 18 city-home two-bed); 4 buildings (A 10-storey/31.85 m, B "
        "14-storey/43.20 m, C 10-storey/32.49 m, D 7-storey/24.69 m); 0 car parking, bicycle 495 "
        "long-term + 129 short-term."),
    "totem-park-residence-infill-phase-2": (
        "Development permit summary (UBC Totem Park Infill Phase 2, 2525 West Mall): student residence, "
        "354 dwelling units (340 single resident rooms + 12 resident-advisor rooms + 2 resident-"
        "coordinator suites); gross building floor area 8,862 m2, net 6,635 m2, building footprint "
        "1,936 m2; FSR 1.69, site coverage 37%, building height 19.9 m; 33 surface parking stalls, "
        "354 long-term bicycle spaces."),
    "biological-sciences-building-ubc-undergraduate-life-sciences": (
        "Project data (UBC Undergrad Life Sciences Teaching Labs / Biological Sciences addition, 6270 "
        "University Blvd): net area 9,211 m2, gross area 14,244 m2 (existing North Wing 4,869 + new "
        "East Wing 9,112 + NW corner 263, incl existing greenhouse); building height 14.5 m at Main "
        "Mall / 21 m at the lane; bicycle 120 (118 Class 1 + 2 Class A); no dwelling units."),
    "wayne-and-william-white-engineering-design-centre-edc": (
        "Project statistics (Wayne and William White Engineering Design Centre, 2345 East Mall): total "
        "site 1,685 m2, site coverage 44%, FSR 1.12; GFA 1,922 m2 (Level 1 681 + mezzanine 87 + Level "
        "2 581 + Level 3 573); 3 storeys + mezzanine, building area 742 m2, height ~14.3 m; Group A "
        "Division 2; 24 on-site bike stalls, shared loading."),
    "life-building": (
        "Project statistics (UBC LIFE Building, 6138 Student Union Blvd - AMS student union + Norm "
        "Theatre): total project area 8,993.94 m2; floor area existing 15,021 m2 -> proposed 15,705 "
        "m2 (AMS 4,390 + Norm Theatre 757 included); 2 storeys above ground + basement + mechanical "
        "penthouse; no dwelling units; no on-site vehicle parking; bicycle 148 Class A + 104 Class B."),
    "mcgavin-rugby-centre": (
        "Project data (UBC Rugby Pavilion Phase One, 6066 Thunderbird Blvd, at Frank Buck / Arthur "
        "Lord Fields): gross area 580.5 m2 (Level 1 235.8 + Level 2 344.7), Phase 1 net 188.9 m2; "
        "building height 18.0 m to the playing field; 24 covered bicycle parking spaces."),
    "rec-north": (
        "Project data (UBC Recreation Centre North, 6140 Student Union Blvd): gross building area "
        "9,830 m2 (gymnasiums, fitness spaces, track, public fitness); up to 6 storeys; Group A-2 "
        "assembly, non-combustible, sprinklered."),
    "museum-anthropology-great-hall-renewal": (
        "Project data (Museum of Anthropology - Great Hall & Skylight seismic upgrade, 6393 NW Marine "
        "Dr): net floor area 656.2 m2 (gallery 565.9 + electrical 14.6 + storage 75.7), gross 697.0 "
        "m2; building height 15.54 m (unchanged)."),
    "hebb-seismic-upgrade-and-landscape-renewal": (
        "Project data (UBC Hebb Building Upgrade, 2045 East Mall - Physics & Astronomy, undergraduate "
        "classrooms + teaching labs): total existing + proposed gross area 6,135.37 m2; site 1,957.30 "
        "m2, site coverage 1,483.50 m2; building height 21.58 m, 6 storeys; seismic upgrade & renewal, "
        "Group A Division 2; no parking, Class 1 bike cage at Volkoff Lane."),
    "wesbrook-place-lot-3-webber-house-formerly-lot-45-village": (
        "Development program (Lot 3 Wesbrook Village / Webber House): a faculty residential development "
        "of ~43,100 sq ft (2.8 FSR) on a ~1,430 m2 (15,392 sq ft) corner site; unit types range from "
        "one-bedroom to four-bedroom, with three-bedroom units the primary accommodation."),
    "ubc-pump-house-replacement": (
        "Project statistics (UBC Pump House, Education Rd): a water pump station (Group F Division 3 "
        "industrial); total site 485.15 m2, FSR 0.32; gross floor area 155.6 m2 (Level 1 113.6 + "
        "Level 2 42.0), 2 storeys, building height 10.09 m."),
    "solar-decathlon-third-space-temporary-building": (
        "Building information (Third Space Commons, 2136 West Mall - the Solar Decathlon 2023 Build "
        "Challenge entry, transitioned to a campus collaborative design hub): gross floor area 247.1 "
        "m2 (2,660 sq ft), 1 storey, building height 7.85 m; Group A Division 2 assembly."),
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
        # Append the context note once; guard so re-running the script never duplicates it.
        if note and note not in (row.get("raw_text") or ""):
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
