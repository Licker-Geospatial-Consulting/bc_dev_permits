"""Apply the in-session Claude-read data-sheet fields to Surrey example rows.

Run after `python -m bc_dev_permits.dataset --municipality surrey --out json`. The page-1 PROPOSAL
is already parsed deterministically by the harvester (units / storeys / floor area); this adds the
parking / bicycle counts read by Claude in-session from the report's "Development Considerations"
parking table (a page the text layer fragments, so it is read as a rendered image). applied with
surrey.apply_claude_pdf (shared merge / conflict / provenance).
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import surrey

# permit_id -> (claude-read fields, source document) from reading each report's parking table.
CLAUDE_READ = {
    # 7925-0315-00 (24-storey, 230 rental units): parking table on report page 7 -
    # 96 resident + 24 visitor = 120 vehicle stalls (incl 5 accessible); 235 secure + 10 visitor
    # = 245 bicycle spaces.
    "7925-0315-00": (
        {"parking_vehicle_stalls": 120, "parking_bike_stalls": 245},
        {"title": "Planning Report 7925-0315-00",
         "url": "https://www.surrey.ca/sites/default/files/planning-reports/PLR_7925-0315-00.pdf"},
    ),
}

NOTES = {
    "7925-0315-00": "Parking (report data table): 96 resident + 24 visitor = 120 vehicle stalls "
                    "(incl 5 accessible); 235 secure + 10 visitor = 245 bicycle spaces.",
}


def main() -> None:
    """Apply the Claude-read fields to data/processed/surrey.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "surrey.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        filled = surrey.apply_claude_pdf(row, fields, document)
        note = NOTES.get(permit_id)
        if note:
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        print(f"{permit_id}: filled {filled} field(s) | method={row['extraction_method']} "
              f"| conflicts={len(row.get('conflicts') or [])} | needs_review={row['needs_review']}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
