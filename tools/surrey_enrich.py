"""Apply the in-session Claude-read fields to Surrey application rows the harvester cannot parse.

Run after `python -m bc_dev_permits.dataset --municipality surrey --out json`. The page-1 PROPOSAL
and the development data-sheet tables are already parsed deterministically by the harvester; this
adds two things that the text parser cannot reach, read by Claude in-session from the rendered PDF:

* PARKING - the vehicle / bicycle stall counts from each report's "Number of Stalls" table, for the
  multi-unit applications whose table the text layer fragments or states unusually.
* ARCHITECTURAL - the project data for the handful of applications whose only data is an architect's
  "PROJECT DATA" titleblock on a drawing sheet (scattered / letter-spaced / image text the parser
  cannot read). See the Format-C investigation: these are rare (essentially 7922-0306 text-based and
  7926-0180 image-only), so they are read once by hand rather than parsed.

Applied with surrey.apply_claude_pdf (shared merge / conflict / provenance): deterministic values
are never overwritten and a disagreement is logged.
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import surrey

_PDF = "https://www.surrey.ca/sites/default/files/planning-reports/PLR_{}.pdf"

# permit_id -> (vehicle stalls proposed, bicycle stalls proposed or None) from the report's
# "Number of Stalls" table (Required / Proposed).
PARKING = {
    "7925-0315-00": (120, 245),   # 24-storey, 230 units: 96 resident + 24 visitor; 235 + 10 bike
    "7924-0219-00": (165, 168),   # 31-storey mixed, 358 units: 112 + 36 + 17 commercial
    "7926-0144-00": (473, 6),     # townhouse, 213 units: 426 + 47 visitor
    "7925-0381-00": (297, 191),   # 179 units: 261 + 36 visitor; 179 + 12 bike
    "7924-0338-00": (143, 46),    # 84 units: 86 + 57 visitor
    "7924-0234-00": (88, None),   # 40 units: 8 + 80 visitor
    "7924-0277-00": (214, None),  # 97 units (3 lots): 194 + 20 visitor
    "7926-0083-00": (225, 53),    # mixed, 133 units: 15 commercial + 17 day care + 180 + 13 visitor
    "7924-0163-00": (83, 60),     # mixed, 54 units: 16 commercial + 61 + 6 visitor; 54 + 6 bike
    "7925-0240-00": (108, None),  # 49 units: 98 + 10 visitor
    "7919-0351-00": (54, 74),     # mixed, 79 units: 10 commercial + 35 + 9 visitor; 68 + 6 bike
    "7919-0369-00": (212, None),  # 96 units: 192 + 20 visitor
    "7925-0259-00": (55, None),   # 25 units: 50 + 5 visitor
    "7915-0187-00": (24, None),   # 11 units townhouse: 22 (garages) + 2 visitor
    # The reports whose parking is stated outside the standard table (prose / a different layout):
    "7925-0383-00": (392, 213),   # 452 units mixed: 32 commercial + 25 childcare + 288 + 47 visitor
    "7926-0081-00": (44, None),   # 20 units townhouse (prose): 40 garage + 4 visitor
    "7926-0104-00": (61, None),   # 201-unit campus student housing: building requires 61 (pooled)
    # 7916-0221-00 (166-unit back-to-back townhouse, 2016) tabulates no parking count - in-unit
    # garages, no stalls table - so no figure is wired rather than fabricate one.
}

# permit_id -> the project data read by hand from an architect's "PROJECT DATA" titleblock, for the
# applications whose report carries no parseable staff-report table (Format C - see module docstring).
ARCHITECTURAL = {
    # Aspen Early Learning Society (child care), 2507 128 St: C-4 -> C-5 rezoning. The report's only
    # data sheet is the architect's titleblock; its text is in drawing order so the parser skips it.
    "7922-0306-00": {
        "development_class": "commercial",
        "occupancy_types": ["commercial"],
        "floor_area": 381.37, "floor_area_unit": "m2",
        "floor_area_ratio": 0.39,
        "number_of_stories": 3,
        "height_m": 5.0,
        "lot_area": 974.77, "lot_area_unit": "m2",
        "parking_vehicle_stalls": 9,
        "occupancies": [{"occupancy": "commercial", "floor_area": 381.37, "floor_area_unit": "m2"}],
    },
    # Somerset Gardens renovation + deep energy retrofit, 1821/1851/1881 Southmere Cr E. The PDF is a
    # cover memo + architectural drawings; the titleblock is image-only, so it is read from the image.
    "7926-0180-00": {
        "development_class": "residential",
        "occupancy_types": ["residential"],
        "number_of_stories": 4,
        "units_total": 166,
        "parking_vehicle_stalls": 234,
    },
}

# Longer context a scalar can't hold, appended to raw_text.
NOTES = {
    "7926-0104-00": "Parking (Parking Study, prose): the student-housing building requires 61 "
                    "spaces; total on-site campus supply after the addition is 878 spaces "
                    "(a 25% reduction from Zoning Bylaw rates, pooled across uses).",
    "7922-0306-00": "Architect PROJECT DATA (2507 128 St, Aspen Early Learning Society child care): "
                    "lot 974.77 m2, gross floor area 381.37 m2, FAR 0.39 (permitted 0.50), lot "
                    "coverage 29% (max 50%), 3 storeys, height 5.0 m; parking 9 off-street "
                    "(8 + 1 accessible) plus 9 street stalls, 17 required.",
    "7926-0180-00": "Architect PROJECT DATA (Somerset Gardens renovation + deep energy retrofit): an "
                    "existing 166-unit, 4-storey low-rise complex of 3 buildings (A/B/C; footprints "
                    "1,935.44 / 2,079.82 / 2,083.44 m2), 234 existing parking spaces (166 units x "
                    "1.4), MR5 Low-Rise Apartment Zone. No new units.",
}

CLAUDE_READ = {
    **{aid: {"parking_vehicle_stalls": veh, **({"parking_bike_stalls": bike} if bike else {})}
       for aid, (veh, bike) in PARKING.items()},
    **ARCHITECTURAL,
}


def main() -> None:
    """Apply the Claude-read parking + architectural fields to data/processed/surrey.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "surrey.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    applied = 0
    for permit_id, fields in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            continue
        document = {"title": f"Planning Report {permit_id}", "url": _PDF.format(permit_id)}
        filled = surrey.apply_claude_pdf(row, fields, document)
        # A memo-cover report that only this hand-read gave a development class / program is now parsed.
        if filled and row.get("development_class") not in (None, "unknown"):
            row["is_parsed"] = True
        note = NOTES.get(permit_id)
        if note:
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        applied += 1
        summary = ", ".join(f"{k}={v}" for k, v in fields.items() if k != "occupancies")
        print(f"{permit_id}: {summary} | filled {filled} | method={row['extraction_method']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\napplied to {applied} rows; wrote {path}")


if __name__ == "__main__":
    main()
