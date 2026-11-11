"""Apply the in-session Claude-read PDF fields to the Delta rows (the PDF-enrichment pass).

Run after `python -m bc_dev_permits.dataset --municipality delta --out json`. Delta's HTML pages
give the headline (class, storeys, units, FSR) but the parking breakdown, unit mix, exact floor area
and building height live in each application's PDF. The fields below were extracted by Claude reading
each project's best source in-session, per the Claude-read workflow in the pdf-parsing memory:

  * a Council / Staff / New Application Report (a text PDF with a project-data table + proposal
    prose) where one exists, and
  * the architectural set's SITE CALCULATIONS / Project Statistics sheet (rendered to PNG and read
    visually) for the two projects that publish only drawings (Walker House 2, Eden Crescent).

`pdf_enrich.apply_claude_pdf` reuses the shared merge / conflict / provenance code: a deterministic
HTML value is never overwritten and a real HTML-vs-PDF disagreement is logged to row['conflicts'].
CLASS_OVERRIDE / FIELD_OVERRIDE force a value AFTER the merge for a CONFIRMED HTML mis-parse (e.g. an
apartment read as "mixed", or an address civic number read as a tiny stall count) that the
merge would otherwise keep - kept tiny and verified against the PDF.
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import pdf_enrich

DL = "https://letstalk.delta.ca/download_file/"

# permit_id -> (claude-read fields, source document) from reading each project's best PDF.
CLAUDE_READ = {
    # Ladner Trunk Rd Apartment - Staff Report + architectural Development Data: two six-storey
    # apartment buildings, 222 units (revised proposal converts the former 87 rental units to market
    # strata, 211 -> 222), FSR 2.39, net site 7,264 m2. The current revised arch set is not published
    # (only the superseded 211-unit set, GFA 16,844 m2), so floor_area is derived (FSR 2.39 x net lot).
    "LU009536": (
        {"number_of_stories": 6, "height_m": 23.0, "lot_area": 7264.0, "lot_area_unit": "m2",
         "rental_or_strata": "strata", "rental_subtype": "market strata"},
        {"title": "Staff Report - First, Second and Third Readings", "url": DL + "1010/557"},
    ),
    # 11199 84 Ave Mixed-Use - Staff Report + architectural PROJECT INFORMATION (A-001): six-storey
    # mixed-use, 84 residential units + 1,145 m2 ground-floor commercial (7 CRUs); total FSR floor
    # area 80,784 sq ft (7,505 m2), site 2,856 m2; 147 vehicle + 114 bicycle stalls; unit split from
    # the parking ratios (63 studio/1-bed + 21 two-plus-bed).
    "LU009611": (
        {"units_total": 84, "number_of_stories": 6,
         "unit_mix": {"studio-or-1-bed": 63, "2-or-more-bed": 21},
         "floor_area": 7505.0, "floor_area_unit": "m2", "lot_area": 2856.0, "lot_area_unit": "m2",
         "parking_bike_stalls": 114},
        {"title": "Staff Report + Architectural Project Information (A-001)", "url": DL + "2701/672"},
    ),
    # Ladner Willows - Council Report + architectural PROJECT DATA (A0.01): 146-unit non-market
    # rental, five storeys, FSR 1.44, roof height 17.1 m, GFA 11,659 m2, site 7,785 m2; bedroom mix
    # studio 23 / 1-bed 46 / 2-bed 55 / 3-bed 20 / 4-bed 2; affordability split 50% non-market /
    # 30% affordable-market / 20% deep-subsidy.
    "LU009635": (
        {"zoning_density": "FSR 1.44", "height_m": 17.1,
         "floor_area": 11659.0, "floor_area_unit": "m2", "lot_area": 7785.0, "lot_area_unit": "m2",
         "unit_mix": {"studio": 23, "1-bed": 46, "2-bed": 55, "3-bed": 20, "4-bed": 2},
         "rental_or_strata": "rental", "rental_subtype": "non-market rental", "parking_bike_stalls": 162,
         "rental_mix": {"non-market": 73, "affordable-market-rental": 44, "deep-subsidy": 29}},
        {"title": "Council Report + Architectural Project Data (A0.01)", "url": DL + "3018/669"},
    ),
    # 11944 92 Ave - Council Report + architectural PROJECT DATA (SD1.10/1.11A/1.13B): a six-storey
    # apartment (Lot A, 22.1 m) + 51 three-storey townhouses (Lot B); 191 units total. Overall gross
    # site 13,918 m2; total GFA 18,933 m2 (apartment 104,051 sq ft / 9,666 m2 + townhouse 99,749 sq
    # ft / 9,267 m2). Apartment parking 159 res + 12 vis. (The arch set shows 134 apartment units;
    # the council report states 140 - kept at the council figure.)
    "LU009671": (
        {"units_total": 191, "number_of_stories": 6, "height_m": 22.1,
         "zoning_density": "FSR 2.1 (apartment) / 1.2 (townhouse)",
         "floor_area": 18933.0, "floor_area_unit": "m2", "lot_area": 13918.0, "lot_area_unit": "m2",
         "parking_vehicle_stalls": 171, "parking_bike_stalls": 145,
         "unit_mix": {"apartment-1-and-2-bed": 140, "townhouse-3-bed": 51},
         "address": "11944 92 Avenue"},
        {"title": "Council Report + Architectural Project Data (SD1.10/1.11A/1.13B)",
         "url": DL + "3040/580"},
    ),
    # Ladner Landing II - Council Report + architectural DEVELOPMENT DATA (A-01): six-storey market
    # rental, 78 units (1- and 2-bed, 16 adaptable), FSR 2.19, GFA 58,218 sq ft / 5,409 m2 (excl
    # parking); parking 40 resident + 2 visitor + 7 car-share = 49, 83 bicycle; site 2,410 m2.
    "LU009686": (
        {"rental_or_strata": "rental", "rental_subtype": "market rental",
         "parking_vehicle_stalls": 49, "parking_bike_stalls": 83,
         "parking_notes": "40 resident + 2 visitor + 7 car-share = 49 vehicle stalls; 83 bicycle stalls",
         "floor_area": 5409.0, "floor_area_unit": "m2", "lot_area": 2410.0, "lot_area_unit": "m2"},
        {"title": "Council Report + Architectural Development Data (A-01)", "url": DL + "3039/597"},
    ),
    # Brixton Apartments - architectural PROGRAM SUMMARY (A-0.3): six-storey apartment, 158 units
    # (43 studio/1-bed + 113 2-bed + 2 3-bed, 32 adaptable), FAR 2.86, total floor area 117,968 sq ft
    # (10,959 m2), site 3,836 m2; 209 vehicle + 163 bicycle stalls.
    "LU009687": (
        {"units_total": 158, "number_of_stories": 6, "lot_area": 3836.0, "lot_area_unit": "m2",
         "floor_area": 10959.0, "floor_area_unit": "m2",
         "unit_mix": {"studio-1-bed": 43, "2-bed": 113, "3-bed": 2},
         "parking_bike_stalls": 163},
        {"title": "Architectural Drawings - Program Summary (A-0.3)", "url": DL + "1478/579"},
    ),
    # Walker House 2 - architectural SITE CALCULATIONS (DP-0.01): a 32-storey, 102 m mixed-use tower +
    # 5-storey rental building; 395 units (studio 70 / 1-bed 133 / 2-bed 192), 336 strata + 59 rental;
    # FSR 8.22, GFA 274,651 sq ft (25,516 m2); 391 stalls; net lot 3,360.7 m2.
    "LU009706": (
        {"units_total": 395, "number_of_stories": 32, "height_m": 102.0,
         "floor_area": 25516.0, "floor_area_unit": "m2",
         "unit_mix": {"studio": 70, "1-bed": 133, "2-bed": 192},
         "rental_or_strata": "mixed", "rental_mix": {"strata": 336, "rental": 59},
         "lot_area": 3360.7, "lot_area_unit": "m2", "parking_bike_stalls": 416},
        {"title": "Architectural Drawings - Site Calculations (DP-0.01)", "url": DL + "1600/606"},
    ),
    # 1 Avenue - Council Report (Attachment C project-data table, pp19-21): a 10-lot subdivision with
    # 27 small-scale dwelling units (3 fourplexes + 1 triplex + 6 duplexes) on a 7,302.5 m2 site,
    # 2.5 storeys, max roof-ridge height 10.5 m, per-lot FSR 0.55-1.09; 2 spaces/unit + 2 visitor = 56.
    "LU009726": (
        {"units_total": 27, "number_of_stories": 3, "lot_area": 7302.5, "lot_area_unit": "m2",
         "zoning_density": "FSR 0.55-1.09 (per lot)", "parking_vehicle_stalls": 56},
        {"title": "Council Report - Item 8.4 (project-data table)", "url": DL + "2981/640"},
    ),
    # Two Six-Storey Apartments on 90 Ave - New Application Report (+ applicant boards for the address
    # and site size): 270 market strata units across two six-storey buildings on a 1.62 ac (6,556 m2)
    # site at 11810-11838 90 Avenue.
    "LU009880": (
        {"rental_or_strata": "strata", "rental_subtype": "market strata",
         "address": "11810, 11818, 11820, 11824, 11828 & 11838 90 Avenue",
         "lot_area": 6556.0, "lot_area_unit": "m2",
         "floor_area": 19340.0, "floor_area_unit": "m2"},  # derived: FSR 2.95 x 6,556 m2 lot
        {"title": "New Application Report + Applicant Boards", "url": DL + "2935/645"},
    ),
    # Eden Crescent Townhomes - architectural Project Statistics (A100): 27 townhouse units in 7
    # buildings, 3 storeys, roof ridge 11.3 m, FSR 1.05, GFA 4,303.1 m2, lot 4,109.5 m2; 42 stalls.
    "LU009888": (
        {"units_total": 27, "number_of_stories": 3, "height_m": 11.3, "zoning_density": "FSR 1.05",
         "floor_area": 4303.1, "floor_area_unit": "m2", "lot_area": 4109.5, "lot_area_unit": "m2",
         "address": "5695 & 5711 10A Avenue, 1059 & 1069 Eden Crescent"},
        {"title": "Architectural Drawings - Project Statistics (A100)", "url": DL + "2973/655"},
    ),
    # Dennison Gate - Council Report (+ landscape plans for the address): 50 stacked townhouse units
    # in eight 3-storey buildings (25 ground-oriented + 25 multi-level, 1- to 4-bedroom), FSR 0.62;
    # parking 75 resident, 62 bicycle; at 686 52 Street & 5236 7A Avenue.
    "LU009901": (
        {"units_total": 50, "number_of_stories": 3, "zoning_density": "FSR 0.62",
         "parking_vehicle_stalls": 75, "parking_bike_stalls": 62,
         "unit_mix": {"ground-oriented": 25, "multi-level": 25},
         "address": "686 52 Street & 5236 7A Avenue",
         "floor_area": 4446.0, "floor_area_unit": "m2", "lot_area": 7547.0, "lot_area_unit": "m2"},
        {"title": "1st, 2nd and 3rd Readings Report + Architectural Statistics (A001)",
         "url": DL + "3026/649"},
    ),
    # KinVillage Phase 2 - Staff Report + architectural SYNOPSIS (A0.3): six-storey (21 m) seniors
    # affordable rental, 143 units (133 one-bed + 10 two-bed) + a 786 m2 ground-floor community
    # centre; FSR 2.2, GFA 10,578 m2, net site 4,801.5 m2; Phase 2 parking 72 resident + 18 visitor +
    # 23 community-centre = 113 stalls, 23 bicycle.
    "LU009921": (
        {"units_total": 143, "number_of_stories": 6, "height_m": 21.0,
         "floor_area": 10578.0, "floor_area_unit": "m2",
         "unit_mix": {"1-bed": 133, "2-bed": 10},
         "rental_or_strata": "rental", "rental_subtype": "affordable seniors rental",
         "lot_area": 4801.5, "lot_area_unit": "m2", "parking_bike_stalls": 23,
         "parking_notes": "72 resident + 18 visitor vehicle stalls + 23 community-centre stalls = "
                          "113; 23 resident bicycle stalls; ground-floor 786 m2 community centre"},
        {"title": "Staff Report + Architectural Synopsis (A0.3)", "url": DL + "3047/651"},
    ),
    # Malcolm Centre - HTML page + New Application Report: a six-storey mixed-use building with four
    # ground-floor commercial units and 45 rental apartments, FSR 2.5; 35 residential + 14 commercial
    # parking spaces (49) and 1 loading space; at 1505 56 Street.
    "LU009955": (
        {"units_total": 45, "rental_or_strata": "rental",
         "parking_vehicle_stalls": 49, "parking_bike_stalls": 55,
         "floor_area": 3775.0, "floor_area_unit": "m2", "lot_area": 1534.0, "lot_area_unit": "m2",
         "height_m": 24.0,
         "parking_notes": "35 residential + 14 commercial = 49 vehicle stalls; 1 loading space"},
        {"title": "New Application Report + Architectural Statistics (A006)", "url": DL + "2315/660"},
    ),
    # The Park - New Application Report: confirms 67 apartment units with no lock-off suites (revised
    # from 65 units + 13 lock-off). The bedroom mix is not published as counts, so unit_mix stays null.
    "LU009764": (
        {},
        {"title": "New Application Report", "url": DL + "1685/617"},
    ),
    # Townhouses in East Ladner - architectural DEVELOPMENT DATA sheet (AC-001): 16 four-bedroom
    # townhouse units, 3 storeys, height 10.8 m, FSR 1.13, GFA 2,062 m2, site 1,821.7 m2; 32 stalls.
    "LU009770": (
        {"units_total": 16, "height_m": 10.8, "unit_mix": {"4-bed": 16},
         "lot_area": 1821.7, "lot_area_unit": "m2"},
        {"title": "Architectural - Site Plan and Elevations (Development Data)", "url": DL + "1798/621"},
    ),
    # 4501 Arthur Drive - Staff Report + Project Data Table (ATT C) + architectural Site Plan (ATT E):
    # 24 three-storey townhouse units in four buildings (15 three-bed + 9 four-bed), FSR 1.0, net site
    # 3,291 m2, roof-ridge height 13 m; two parking spaces per unit (48 stalls). No document states a
    # single gross floor area, so floor_area is derived (FSR 1.0 x net lot = 3,291 m2).
    "LU009952": (
        {"unit_mix": {"3-bed": 15, "4-bed": 9}, "lot_area": 3291.0, "lot_area_unit": "m2",
         "floor_area": 3291.0, "floor_area_unit": "m2", "height_m": 13.0},
        {"title": "Staff Report + Project Data Table + Architectural Site Plan", "url": DL + "2999/659"},
    ),
}

# Confirmed deterministic mis-parses, forced AFTER the merge (the shared code keeps HTML otherwise),
# each verified against the PDF. Brixton and Dennison Gate are apartment / townhouse projects the
# prose's "mix of unit sizes" made the parser read as "mixed".
CLASS_OVERRIDE = {"LU009687": "residential", "LU009901": "residential", "LU009770": "residential"}
# A FIELD_OVERRIDE value of None clears a mis-parse (an amenity / commercial / community-centre area
# the HTML read as the whole GFA; a junk parking_notes); a value replaces an outdated figure (the
# tenure / subtype of a revised application the HTML read from its original prose; a roof height /
# parking stall the HTML mis-read).
FIELD_OVERRIDE = {
    "LU009536": {"floor_area": 17361.0, "rental_or_strata": "strata", "rental_subtype": "market strata"},
    "LU009611": {"zoning_density": "FSR 2.3",  # report: proposed 2.3, max 2.6
                 "parking_vehicle_stalls": 147,  # arch total 74 residential + 73 commercial
                 "floor_area": 7505.0},  # arch GFA; HTML read a 929 m2 amenity area
    "LU009635": {"height_m": 17.1, "rental_subtype": "non-market rental"},
    "LU009686": {"parking_notes": "40 resident + 2 visitor + 7 car-share = 49 vehicle stalls; "
                                  "83 bicycle stalls"},
    "LU009706": {"floor_area": 25516.0, "floor_area_unit": "m2"},
    "LU009880": {"rental_subtype": "market strata"},
    "LU009888": {"parking_vehicle_stalls": 42, "zoning_density": "FSR 1.05"},  # stats sheet 1.05
    "LU009901": {"zoning_density": "FSR 0.62"},     # report 0.62 (HTML rounded to 0.6)
    "LU009955": {"parking_notes": "35 residential + 14 commercial = 49 vehicle stalls; "
                                  "1 loading space"},
    # KinVillage: HTML read the 786 m2 community centre as the GFA and the junk parking_notes;
    # the architectural Synopsis gives GFA 10,578 m2 and 113 Phase-2 stalls.
    "LU009921": {"floor_area": 10578.0, "parking_vehicle_stalls": 113,
                 "parking_notes": "72 resident + 18 visitor vehicle stalls + 23 community-centre "
                                  "stalls = 113; 23 resident bicycle stalls; ground-floor 786 m2 "
                                  "community centre"},
    # The development-data sheet supersedes the HTML prose figures for these fields.
    "LU009770": {"zoning_density": "FSR 1.13", "floor_area": 2062.0, "floor_area_unit": "m2",
                 "parking_vehicle_stalls": 32},
    "LU009952": {"parking_vehicle_stalls": 48},  # report: two spaces per unit (24 units) - HTML read "2"
    # The architectural Program Summary / Synopsis supersedes the rounded / partial HTML figures.
    "LU009687": {"zoning_density": "FSR 2.86", "parking_vehicle_stalls": 209},
}

NOTES = {
    "LU009706": "Project statistics (Walker House 2, 9321 & 9361 120 St / 11958 93A Ave): a 32-storey "
                "(102 m) mixed-use tower over a one-storey commercial podium plus a 5-storey rental "
                "building. 395 units - tower 336 market strata (studio 55 / 1-bed 111 / 2-bed 170) + "
                "rental 59 (studio 15 / 1-bed 22 / 2-bed 22); 60 adaptable. FSR 8.22, GFA 274,651 sq "
                "ft (25,516 m2); 391 vehicle stalls (311 tower + 30 rental + 40 visitor + 10 "
                "commercial), 416 bicycle spaces; net lot 3,360.7 m2, site coverage 48.3%.",
    "LU009671": "Program (11944 92 Ave): Lot A - a six-storey apartment building, 140 units (1- and "
                "2-bed, 28 adaptable, 42-78 m2), FSR 2.1, roof height 22.1 m, two parkade levels, 159 "
                "resident + 12 visitor stalls, 145 bicycle. Lot B - 51 three-storey townhouses "
                "(all 3-bedroom, 176+ m2), FSR 1.2, roof height 11.3-13.0 m. Site 13,918 m2.",
    "LU009635": "Program (Ladner Willows, 5625 Ladner Trunk Rd): a 5-storey, 146-unit non-market "
                "rental apartment (studio / 1- / 2-bed), FSR 1.44, roof height 17.1 m, on a 7,785 m2 "
                "site (replacing a 40-unit non-profit townhouse complex). Tenure mix ~50% non-market "
                "/ 30% affordable-market / 20% deep-subsidy rentals; 139 resident + 15 visitor "
                "vehicle stalls, 157 resident + 5 visitor bicycle + 5 scooter.",
    "LU009888": "Project statistics (Eden Crescent Townhomes, 5695 & 5711 10A Ave / 1059 & 1069 Eden "
                "Crescent): 27 townhouse units in 7 buildings (types A/B/C), 3 storeys, roof ridge "
                "11.3 m; GFA 4,303.1 m2, lot 4,109.5 m2, FSR 1.05, lot coverage 44%; 42 resident "
                "stalls (no visitor), 65.7 units/ha.",
    "LU009770": "Development data (Townhouses in East Ladner, 4739 & 4749 60B St): 16 four-bedroom "
                "townhouse units (types D/D1/D2/E/E1/E2, 126-137 m2 each) in two 3-storey buildings; "
                "height 10.8 m, FSR 1.13, GFA 2,062 m2, site 1,821.7 m2, lot coverage 54.4%; 30 "
                "resident + 2 visitor = 32 parking stalls.",
    "LU009952": "Program (4501 Arthur Drive, Ladner): 24 three-storey townhouse units in four "
                "buildings - 15 three-bedroom + 9 four-bedroom, 130-161 m2 each; FSR 1.0, site 3,291 "
                "m2; two parking spaces per unit (exceeds the 1.5/unit minimum). Includes retention "
                "of an existing 2-storey heritage dwelling.",
    "LU009764": "New Application Report (The Park): the revised proposal is 67 apartment units with "
                "no lock-off suites (the original was 65 units + 13 lock-off); the bedroom mix (1- to "
                "4-bedroom) is not published as counts.",
}


def main() -> None:
    """Apply the Claude-read fields to data/processed/delta.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "delta.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        filled = pdf_enrich.apply_claude_pdf(row, fields, document)
        # Forced overrides for confirmed deterministic mis-parses (after the merge).
        if permit_id in CLASS_OVERRIDE:
            row["development_class"] = CLASS_OVERRIDE[permit_id]
            row["occupancy_types"] = [CLASS_OVERRIDE[permit_id]]
            row.setdefault("field_methods", {})["development_class"] = "pdf_claude"
        for key, value in FIELD_OVERRIDE.get(permit_id, {}).items():
            if value is None:
                row.pop(key, None)
                row.get("field_methods", {}).pop(key, None)
            else:
                row[key] = value
                row.setdefault("field_methods", {})[key] = "pdf_claude"
            # Drop any logged conflict for a field we have now resolved authoritatively.
            row["conflicts"] = [c for c in (row.get("conflicts") or []) if c["field"] != key]
        note = NOTES.get(permit_id)
        if note and note not in (row.get("raw_text") or ""):
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        row.pop("needs_pdf_extraction", None)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
