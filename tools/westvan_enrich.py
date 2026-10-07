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
