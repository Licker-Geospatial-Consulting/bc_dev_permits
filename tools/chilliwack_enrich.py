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
    # Chilliwack SmartCentre: the page names no civic address (title is the mall name). Its reference
    # site plan shows the parcel fronting Luckakuck Way, and the prose places it along Vedder Road, so
    # the approximate address is the Vedder Road / Luckakuck Way corner. The exact map point still
    # comes from the marker coordinates; this only gives the row a human-readable reference address.
    "chilliwack-smartcentre": (
        {"address": "Vedder Road & Luckakuck Way, Chilliwack, BC"},
        {"title": "Reference site plan (location map)",
         "url": UP + "2023/10/site-plan.png"},
    ),
}

# Reference addresses for projects whose page, slug and image filenames name no civic address, read
# in-session from the project page / its site plan, or - for a sub-building of a larger project - taken
# from the parent development's civic address. These are approximate REFERENCE points only (the exact
# map placement always comes from the marker coordinates), so each is applied only when the
# deterministic harvest left `address` empty, and is noted as approximate in raw_text.
ADDRESS_REF = {
    # Sub-buildings / phases -> the parent development's civic address (same site, distinct buildings).
    "andmar-chilliwack-46183-thomas-road-andmar-1": "46183 Thomas Road",
    "andmar-chilliwack-46183-thomas-road-andmar-2": "46183 Thomas Road",
    "andmar-chilliwack-46183-thomas-road-building-c": "46183 Thomas Road",
    "45511-campus-drive-york-building-a-garrison-campus": "45511 Campus Drive",
    "45511-campus-drive-york-building-b-garrison-crossing": "45511 Campus Drive",
    "45511-campus-drive-york-building-c-garrison-crossing": "45511 Campus Drive",
    "chilliwack-smartcentre-chilliwack-smartcentre-building-b": "Vedder Road & Luckakuck Way, Chilliwack, BC",
    "chilliwack-smartcentre-chilliwack-smartcentres-sign": "Vedder Road & Luckakuck Way, Chilliwack, BC",
    "chilliwack-smartcentre-kelly-obryans-chilliwack": "Vedder Road & Luckakuck Way, Chilliwack, BC",
    "53003-bunker-road-phase-3-hotel-more-retail": "53003 Bunker Road",
    "53003-bunker-road-phase-2-retail-drive-thru-opportunity": "53003 Bunker Road",
    "53003-bunker-road-petro-canada-retail": "53003 Bunker Road",
    "45434-willowstream-rd-parkstone-flats": "45434 Willowstream Rd",
    "45434-willowstream-rd-lane-homes": "45434 Willowstream Rd",
    "45434-willowstream-rd-row-homes": "45434 Willowstream Rd",
    "45434-willowstream-rd-trail-homes": "45434 Willowstream Rd",
    "45434-willowstream-rd-townhomes": "45434 Willowstream Rd",
    "vantage-building-1": "45551 Knight Road",
    "vantage-building-2": "45551 Knight Road",
    "vantage-building-3": "45551 Knight Road",
    "45745-45751-alder-avenue-45740-45744-45754-webb-avenue-building-1": "45751 Alder Avenue",
    "45745-45751-alder-avenue-45740-45744-45754-webb-avenue-building-2": "45751 Alder Avenue",
    # Standalone projects -> the street or landmark named in the page prose (no civic number stated).
    "sardis-secondary-expansion": "Sardis Secondary School, Chilliwack, BC",
    "a-d-rundle-expansion": "A.D. Rundle Middle School, Chilliwack, BC",
    "bradley-centre": "Chilliwack General Hospital, Chilliwack, BC",  # prose: 2nd floor of CGH
    "hocking-avenue-centre": "Hocking Avenue, Chilliwack, BC",
    "nowell-4th": "Fourth Avenue & Nowell Street, Chilliwack, BC",   # prose: four properties at the corner
    "liqwel": "Thomas Road, Chilliwack, BC",                         # prose: at Base 10 and Thomas Road
    "base10": "Thomas Road, Chilliwack, BC",                         # Base10 community, beside Liqwel
    "the-yearling": "Iron Horse, Chilliwack, BC",                    # prose: within the Iron Horse community
    "district-1881": "Yale Road & Young Road, Chilliwack, BC",       # downtown Chilliwack, Young Rd/Yale Rd
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
    "chilliwack-smartcentre": "Address is approximate: the reference site plan shows the SmartCentres "
                              "parcel fronting Luckakuck Way and the prose places it along Vedder "
                              "Road, so the Vedder Road / Luckakuck Way corner is used as a reference "
                              "point (exact placement from the marker coordinates). The site plan "
                              "shows 10 buildings totalling 118,270 ft2, anchored by Safeway with "
                              "Winners, SportChek and an adjacent Canadian Tire.",
}


def apply_reference_addresses(by_id: dict) -> int:
    """Fill a reference address on rows the deterministic harvest left address-less. Return count."""
    filled = 0
    for permit_id, address in ADDRESS_REF.items():
        row = by_id.get(permit_id)
        if not row:
            print(f"  (addr row not found: {permit_id})")
            continue
        if row.get("address"):
            continue  # a deterministic address already won; never overwrite it
        pdf_enrich.apply_claude_pdf(row, {"address": address})
        note = f"Reference address (approximate): {address}. Exact map placement from the marker coordinates."
        if note not in (row.get("raw_text") or ""):
            row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), note) if p)
        row.pop("needs_pdf_extraction", None)
        filled += 1
    return filled


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

    print(f"reference addresses filled: {apply_reference_addresses(by_id)}")
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
