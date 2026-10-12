"""Apply the in-session Claude-read parking fields to Surrey's multi-unit application rows.

Run after `python -m bc_dev_permits.dataset --municipality surrey --out json`. The page-1 PROPOSAL
is already parsed deterministically by the harvester (units / storeys / floor area / type); this
adds the vehicle and bicycle stall counts read by Claude in-session from each report's "Number of
Stalls" table (the parking table under "Development Considerations"; a table the text layer
fragments, so it is read as a rendered image). Applied with surrey.apply_claude_pdf (shared merge /
conflict / provenance): deterministic values are never overwritten and a disagreement is logged.

Coverage: the multi-unit development applications in the recent harvest whose report has a standard
parking table. A few (7925-0383-00, 7916-0221-00, 7926-0104-00, 7926-0081-00) state parking
differently and are left for a later read.
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

# Longer context a scalar can't hold, appended to raw_text.
NOTES = {
    "7926-0104-00": "Parking (Parking Study, prose): the student-housing building requires 61 "
                    "spaces; total on-site campus supply after the addition is 878 spaces "
                    "(a 25% reduction from Zoning Bylaw rates, pooled across uses).",
}

CLAUDE_READ = {
    aid: (
        {"parking_vehicle_stalls": veh, **({"parking_bike_stalls": bike} if bike else {})},
        {"title": f"Planning Report {aid}", "url": _PDF.format(aid)},
    )
    for aid, (veh, bike) in PARKING.items()
}


def main() -> None:
    """Apply the Claude-read parking fields to data/processed/surrey.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "surrey.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    applied = 0
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            continue
        filled = surrey.apply_claude_pdf(row, fields, document)
        note = NOTES.get(permit_id)
        if note:
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        applied += 1
        print(f"{permit_id}: parking {fields['parking_vehicle_stalls']} veh"
              f"{'/' + str(fields['parking_bike_stalls']) + ' bike' if 'parking_bike_stalls' in fields else ''}"
              f" | filled {filled} | method={row['extraction_method']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\napplied to {applied} multi-unit rows; wrote {path}")


if __name__ == "__main__":
    main()
