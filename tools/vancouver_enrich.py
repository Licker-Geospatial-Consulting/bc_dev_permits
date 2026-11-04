"""Apply the in-session Claude-read PDF fields to the Vancouver New/Active rows.

Run after `python -m bc_dev_permits.dataset --municipality vancouver --out json`. The fields below
were extracted by Claude reading each application's priority PDF in-session - the site plan /
elevation / project-statistics sheet whose table carries the floor area, building height, site
area, and vehicle + bicycle parking the ShapeYourCity page omits - per the Claude-read workflow in
the pdf-parsing memory. `vancouver.apply_claude_pdf` reuses the shared merge / conflict / provenance
code: deterministic HTML is never overwritten and a real HTML-vs-PDF disagreement is logged to
`row['conflicts']` (the Victoria conflict mechanism).

Document selection: the site plan / project-statistics sheet is read first (vancouver.select_pdf_
sources orders them); when a project has neither a site plan nor an elevation sheet, the next most
informative PDF is read instead (a design rationale, project-information cover, or operational
letter). The ``doc`` hint below names the sheet actually read; main() resolves it to the row's own
document URL. Only New + Active applications are covered here; the Approved backlog is a later
iteration.

Each value is (fields, doc_title_hint). A field is included only when the PDF stated it; counts and
areas are taken as the project totals. Several rows intentionally disagree with the HTML (a daycare
the prose called "commercial", a unit count the prose under-read) - those surface as conflicts, not
overwrites.
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import vancouver

# permit_id -> (claude-read fields, doc-title hint naming the sheet read)
CLAUDE_READ: dict[str, tuple[dict, str]] = {
    # 868 E Hastings - "The Garden Strathcona": permanent ~350 sq ft patio for an existing
    # Restaurant Class-1. Site plan A0.1 statistics: site 283.35 m2, 2 storeys, 7.25 m, total
    # proposed floor area 3,453.9 sq ft (1.13 FSR), 0 vehicle, bicycle Class A 2 + Class B 1.
    "DP-2026-00743": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "number_of_stories": 2, "height_m": 7.25, "lot_area": 283.35, "lot_area_unit": "m2",
         "floor_area": 320.9, "floor_area_unit": "m2", "zoning_density": "FSR 1.13",
         "parking_vehicle_stalls": 0, "parking_bike_stalls": 3,
         "parking_notes": "Bicycle: Class A 2, Class B 1. Vehicle: 0 (one exterior space removed "
                          "for the patio)."},
        "Site plan"),
    # 1375 Graveley - convert an existing 2-unit to a 3-unit Multiple Conversion Dwelling; 3 rental
    # units under one ownership (NOT strata), 2 storeys, 4,238 sq ft lot. Units 2bd/2bd/2bd.
    "DP-2026-00623": (
        {"rental_or_strata": "rental", "unit_mix": {"2-bed": 3},
         "lot_area": 393.7, "lot_area_unit": "m2"},
        "Context plan"),
    # 1837 Hosmer - new single house replacing a demolished heritage house (3-car garage).
    "DP-2026-00358": (
        {"development_class": "residential", "occupancy_types": ["residential"]},
        "Design rationale"),
    # 2339 Main - change of use (2nd/3rd floor residential -> hotel) in an existing mixed-use
    # building, one surface parking space off the lane (C-3A).
    "DP-2026-00577": (
        {"parking_vehicle_stalls": 1},
        "Notification postcard"),
    # 2397 E 11th - small-scale pharmacy tenant improvement (commercial) in a mixed-use building;
    # the notification postcard states a proposed floor area of ~1,357 sq ft (126.1 m2).
    "DP-2026-00737": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "floor_area": 126.07, "floor_area_unit": "m2"},
        "Notification postcard"),
    # 1950 Triumph (Parallel 49 Brewing) - annual private patio for the existing brewery/restaurant;
    # the patio area on private property is ~2,482 sq ft (230.6 m2).
    "DP-2026-00152": (
        {"floor_area": 230.6, "floor_area_unit": "m2"},
        "Site plan and elevations"),
    # 1109 Commercial Dr - mixed-use (7 units). Site plan statistics: site 699 m2 (7,524 sq ft),
    # building area 490 m2 (5,280 sq ft), 3 storeys.
    "DP-2026-00387": (
        {"number_of_stories": 3, "lot_area": 699.0, "lot_area_unit": "m2",
         "floor_area": 490.0, "floor_area_unit": "m2"},
        "Site plan"),
    # 438 W King Edward - preschool (institutional); the operational letter reserves three
    # underground parkade stalls for preschool use.
    "DP-2026-00070": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "parking_notes": "Preschool reserves 3 underground parkade stalls (2, 3 & 8); relaxation "
                          "of 3 off-street passenger Class-A stalls and 1 Class-A bicycle space."},
        "Operational letter"),
    # 396 E 2nd - secured rental (RS-3 -> rezoning), 273 units: site 2,581.9 m2, FAR floor area
    # 16,547 m2, FSR 6.41, 66 m; 125 vehicle + 518 bicycle stalls.
    "DP-2026-00568": (
        {"rental_or_strata": "rental", "rental_subtype": "secured rental",
         "lot_area": 2581.9, "lot_area_unit": "m2", "floor_area": 16547.0, "floor_area_unit": "m2",
         "zoning_density": "FSR 6.41", "height_m": 66.0,
         "parking_vehicle_stalls": 125, "parking_bike_stalls": 518},
        "Project stats"),
    # 1394 Nanaimo - preschool on the ground floor of an existing building (institutional, 1 storey).
    "DP-2026-00792": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 1},
        "Site plan"),
    # 2411 Nanaimo - daycare tenant improvement of an existing restaurant. The data sheet calls it a
    # daycare (A2), i.e. institutional, disagreeing with the prose's "commercial": site 845.98 m2,
    # building area 314.44 m2, 1 storey, bicycle area for 11.
    "DP-2026-00705": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 1, "lot_area": 845.98, "lot_area_unit": "m2",
         "floor_area": 314.44, "floor_area_unit": "m2", "parking_bike_stalls": 11},
        "Site plan"),
    # 1145 Union - single-detached house with secondary suite (RT-3): site 283.59 m2, principal
    # house 8.5 m / 2 storeys, 2 Class A bicycle spaces.
    "DP-2026-00502": (
        {"development_class": "residential", "occupancy_types": ["residential"],
         "number_of_stories": 2, "height_m": 8.5, "lot_area": 283.59, "lot_area_unit": "m2",
         "parking_bike_stalls": 2},
        "Site plan"),
    # 2120-2150 W 10th - mixed-use secured rental (Broadway Plan), 125 units, 20 storeys (68.1 m),
    # FSR 7.68. Unit mix (market + below-market combined): 17 one-bed, 91 two-bed, 17 three-bed.
    "DP-2026-00731": (
        {"rental_or_strata": "rental", "zoning_density": "FSR 7.68", "height_m": 68.1,
         "unit_mix": {"1-bed": 17, "2-bed": 91, "3-bed": 17}},
        "Design rationale"),
    # 2212 W 10th - "Regent": 25-storey tower + 4-storey podium, site 2,322.9 m2 (25,003 sq ft).
    "DP-2026-00651": (
        {"lot_area": 2322.9, "lot_area_unit": "m2", "number_of_stories": 25},
        "Site plan"),
    # 4545 W 10th - mixed-use (C-2 -> CD-1): site 11,966.4 m2, density 46,953 m2, FSR 3.92, towers
    # 19 & 20 storeys over 6-storey podiums (tallest 20; the prose read 22).
    "DP-2026-00569": (
        {"lot_area": 11966.4, "lot_area_unit": "m2", "zoning_density": "FSR 3.92",
         "floor_area": 46953.0, "floor_area_unit": "m2", "number_of_stories": 20},
        "Project statistics"),
    # 531-595 W 27th - secured rental, 227 units, 6 storeys: site 4,850 m2, FSR 3.0, 19.2 m,
    # 139 vehicle (127 resident + 12 visitor), 449 bicycle (435 Class A + 14 Class B).
    "DP-2026-00668": (
        {"rental_or_strata": "rental", "lot_area": 4850.0, "lot_area_unit": "m2",
         "zoning_density": "FSR 3.0", "height_m": 19.2,
         "parking_vehicle_stalls": 139, "parking_bike_stalls": 449},
        "Project data"),
    # 717-743 W 28th - six-storey secured rental, 94 units (Transit-Oriented Area).
    "DP-2026-00708": (
        {"rental_or_strata": "rental", "rental_subtype": "secured rental"},
        "Design rationale"),
    # 619 W Hastings - new 32-storey tower (hotel + office) adjoining a 16-storey heritage building:
    # site 580 m2, 16 vehicle stalls, 39 bicycle (27 Class A + 12 Class B).
    "DP-2026-00499": (
        {"number_of_stories": 32, "lot_area": 580.0, "lot_area_unit": "m2",
         "parking_vehicle_stalls": 16, "parking_bike_stalls": 39},
        "Project data"),
    # 150 Water St - "Pod Hotel" change of use in a Gastown (HA-2) building: Unit 300, three floors,
    # 856 m2 (122 sleeping pods).
    "DP-2026-00691": (
        {"floor_area": 856.0, "floor_area_unit": "m2", "number_of_stories": 3},
        "Project stats"),
    # 3075 Arbutus & 2115 W 15th - "Melon Seeds Daycare": new 2-storey childcare building (696 m2),
    # institutional, 7 parking stalls.
    "DP-2026-00404": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "number_of_stories": 2, "floor_area": 696.0, "floor_area_unit": "m2",
         "parking_vehicle_stalls": 7},
        "Site plan"),
    # 990 Broughton - convert two ground-floor amenity rooms to two rental units (1-bed + 2-bed)
    # in an existing 13-storey, 80-unit building (RM-5); converted area 111.58 m2.
    "DP-2025-00539": (
        {"rental_or_strata": "rental", "unit_mix": {"1-bed": 1, "2-bed": 1},
         "floor_area": 111.58, "floor_area_unit": "m2"},
        "Project information"),
    # 1188 Cardero - secured rental (CD-1), 221 units, 24 storeys. Project data sheet: site
    # 1,606.9 m2, GFA 15,006.3 m2, FSR 8.83, unit mix 49 studio / 95 one-bed / 72 two-bed /
    # 5 three-bed; 61 vehicle stalls, 353 bicycle (341 Class A + 12 Class B).
    "DP-2026-00672": (
        {"rental_or_strata": "rental", "lot_area": 1606.9, "lot_area_unit": "m2",
         "floor_area": 15006.3, "floor_area_unit": "m2", "zoning_density": "FSR 8.83",
         "unit_mix": {"studio": 49, "1-bed": 95, "2-bed": 72, "3-bed": 5},
         "parking_vehicle_stalls": 61, "parking_bike_stalls": 353},
        "Project data"),
    # 500 Dunsmuir - temporary at-grade commercial surface parking lot on a vacant site (interim
    # use); the site plan states the lot accommodates a total of 62 stalls.
    "DP-2026-00351": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "parking_vehicle_stalls": 62},
        "Site plan"),
    # 5687 Maple (formerly 2001-2021 W 41st) - mixed-use secured rental, 6 storeys: 66 rental units
    # (9 studio, 34 one-bed, 23 two-bed) over retail + future daycare, FSR 3.5, 22.78 m (the prose
    # read 58 units).
    "DP-2026-00099": (
        {"units_total": 66, "unit_mix": {"studio": 9, "1-bed": 34, "2-bed": 23},
         "rental_or_strata": "rental", "zoning_density": "FSR 3.5", "height_m": 22.78},
        "Design rationale"),
    # 1905 Ogden - HAVN Saunas Beauty & Wellness addition at the Museum of Vancouver (CD-1):
    # 815.1 m2, 10.36 m, 54 vehicle stalls, 28 bicycle (2 Class A + 26 Class B).
    "DP-2026-00589": (
        {"development_class": "commercial", "occupancy_types": ["commercial"],
         "floor_area": 815.1, "floor_area_unit": "m2", "height_m": 10.36,
         "parking_vehicle_stalls": 54, "parking_bike_stalls": 28},
        "Project stats"),
    # 7474 Ross St - Ross Park public washroom + fieldhouse (Park Board, institutional): site
    # 15,347.83 m2, building 93.56 m2, 4.1 m.
    "DP-2026-00519": (
        {"development_class": "institutional", "occupancy_types": ["institutional"],
         "floor_area": 93.56, "floor_area_unit": "m2", "height_m": 4.1,
         "lot_area": 15347.83, "lot_area_unit": "m2"},
        "Project data"),
    # 998 Thurlow - "Washington Court": residential tower over a 6-storey heritage building,
    # 109 units. Data sheet: site 8,652 sq ft (803.8 m2), proposed floor area 88,182 sq ft
    # (8,192 m2), FSR 10.19, 141 ft (43 m), to L14 (the prose read a 6-storey / 8,652 figure).
    "DP-2026-00140": (
        {"lot_area": 803.8, "lot_area_unit": "m2", "floor_area": 8192.0, "floor_area_unit": "m2",
         "zoning_density": "FSR 10.19", "height_m": 43.0, "number_of_stories": 14},
        "Project data"),
    # 328 (324) W 10th - mixed-use secured rental (CD-1), 172 units, ~19 storeys (65.58 m): unit mix
    # 18 studio, 90 one-bed, 44 two-bed, 20 three-bed; 64 family units; ground-floor retail (the
    # prose read residential-only).
    "DP-2026-00313": (
        {"development_class": "mixed", "occupancy_types": ["residential", "commercial"],
         "number_of_stories": 19, "height_m": 65.58, "rental_or_strata": "rental",
         "unit_mix": {"studio": 18, "1-bed": 90, "2-bed": 44, "3-bed": 20},
         "zoning_density": "CD-1"},
        "Project stats"),
    # 2245-2283 W Broadway - mixed-use (residential + retail/office/daycare), 198 units, 25 storeys:
    # ~80 residential vehicle stalls, ~403 bicycle (384 Class A + 19 Class B).
    "DP-2026-00625": (
        {"parking_vehicle_stalls": 80, "parking_bike_stalls": 403,
         "parking_notes": "Residential: 62 regular + 10 visitor + 8 accessible vehicle stalls; "
                          "384 Class A + 19 Class B bicycle (incl. commercial). Plus non-residential "
                          "staff stalls for retail/office/daycare."},
        "Project stats"),
}

# Per-project context notes appended once to raw_text (not schema columns).
NOTES: dict[str, str] = {
    "DP-2026-00743": "Site plan statistics describe the existing restaurant building; the "
                     "application itself is a ~350 sq ft permanent outdoor patio (M-1 conditional use).",
    "DP-2026-00623": "Three rental units under one ownership (not strata), converted from an "
                     "existing two-unit dwelling.",
    "DP-2026-00705": "Data sheet classifies the use as a daycare (A2 - institutional), disagreeing "
                     "with the page's 'commercial' reading.",
    "DP-2026-00731": "Includes below-market rental units; continuous active ground-floor retail "
                     "along Arbutus Street.",
    "DP-2026-00691": "Pod Hotel change of use: 122 sleeping pods across three floors of Unit 300.",
    "DP-2025-00539": "Two ground-floor amenity rooms converted to rental units in an existing "
                     "13-storey, 80-unit building (now 82); building GFA 5,150.6 m2, FSR 3.16.",
    "DP-2026-00099": "Level 1 retail + future daycare; 66 secured rental units on levels 2-6; "
                     "building height relaxation to 22.78 m requested.",
    "DP-2026-00140": "Residential tower rising over a retained 6-storey heritage building; the "
                     "page's 8,652 figure is the site area, not the floor area.",
    "DP-2026-00313": "Secured market + below-market rental over ground-floor retail; 64 family "
                     "units (35.4%).",
    "DP-2026-00737": "Floor area is the ~1,357 sq ft pharmacy tenant-improvement suite, not the "
                     "whole building.",
    "DP-2026-00152": "Floor area is the ~2,482 sq ft private patio (an annual patio permit for the "
                     "existing Parallel 49 brewery/restaurant).",
    "DP-2026-00387": "Site plan figure is the building footprint area (490 m2); mixed-use, 7 units.",
    "DP-2026-00568": "100% secured rental, 20% below-market; podium 6 storeys with tower above.",
}


def _resolve_document(row: dict, hint: str) -> dict | None:
    """Return the row document whose title matches the hint, else the top-priority PDF."""
    docs = row.get("documents") or []
    if hint:
        for d in docs:
            if hint.lower() in (d.get("title") or "").lower():
                return {"title": d.get("title"), "url": d.get("url")}
    ordered = vancouver.order_documents(docs)
    if ordered:
        d = ordered[0]
        return {"title": d.get("title"), "url": d.get("url")}
    return None


def main() -> None:
    """Apply the Claude-read fields to data/processed/vancouver.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "vancouver.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    applied = 0
    for permit_id, (fields, doc_hint) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        document = _resolve_document(row, doc_hint)
        filled = vancouver.apply_claude_pdf(row, fields, document)
        note = NOTES.get(permit_id)
        if note and note not in (row.get("raw_text") or ""):
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        applied += 1
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"applied {applied}/{len(CLAUDE_READ)} Claude-read record(s); wrote {path}")


if __name__ == "__main__":
    main()
