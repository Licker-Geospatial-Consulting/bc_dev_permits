"""Apply the in-session Claude-read gallery-image fields to the Chilliwack rows (enrichment pass).

Run after `python -m bc_dev_permits.dataset --municipality chilliwack --out json`. Build Chilliwack
keeps some projects' richer data - a "Unit Count Summary" / project-data block - only in a gallery
site-plan image, not in the page prose or the Project Details grid. The fields below were extracted
by Claude reading each flagged project's site-plan image in-session (download it with
tools/chilliwack_img_probe.py), per the Claude-read workflow in the pdf-parsing memory.

`pdf_enrich.apply_claude_pdf` reuses the shared merge / conflict / provenance code: a deterministic
HTML value is never overwritten and a real HTML-vs-image disagreement is logged to row['conflicts'].
CLASS_OVERRIDE / FIELD_OVERRIDE force a value AFTER the merge for a CONFIRMED mis-parse - kept tiny
and verified against the image.
"""
import json
from pathlib import Path

from bc_dev_permits import config
from bc_dev_permits.harvesters import pdf_enrich

UP = "https://buildchilliwack.com/wp-content/uploads/"

# permit_id (slug) -> (claude-read fields, source image) from reading each project's site-plan image.
CLAUDE_READ = {
    # 45857 Trethewey Ave (BC Housing supportive housing + shelter). The site plan's Unit Count
    # Summary gives the mix the page prose / Project Details omit: 50 supportive-housing studio units
    # (L2 15 + L3 15 + L4 20) and 11 shelter dorm-style units (L1 7 + L2 4) totalling 42 beds.
    "45857-tretheway-ave": (
        {"unit_mix": {"supportive-studio": 50, "shelter-dorm-unit": 11}},
        {"title": "Conceptual Site Plan (Unit Count Summary)",
         "url": UP + "2023/11/45857-Tretheway-Ave-location-conceptual-site-plan.png"},
    ),
}

# Confirmed mis-parses, forced after the merge; each verified against the gallery image.
CLASS_OVERRIDE: dict[str, str] = {}
FIELD_OVERRIDE: dict[str, dict] = {}

NOTES = {
    "45857-tretheway-ave": "Unit Count Summary (conceptual site plan): Supportive Housing 50 studio "
                           "units (Level 2: 15, Level 3: 15, Level 4: 20); Shelter Housing 11 "
                           "dorm-style units (Level 1: 7 = 24 men's + 4 accessible beds; Level 2: 4 = "
                           "12 women's + 2 couples/queen beds) totalling 42 beds. Plus a Fraser "
                           "Health outbuilding (~180 m2 footprint, program TBD).",
}


def main() -> None:
    """Apply the Claude-read gallery fields to data/processed/chilliwack.json in place."""
    path = Path(config.PROCESSED_DATA_DIR) / "chilliwack.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["permit_id"]: r for r in rows}
    for permit_id, (fields, document) in CLAUDE_READ.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (row not found: {permit_id})")
            continue
        filled = pdf_enrich.apply_claude_pdf(row, fields, document)
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
