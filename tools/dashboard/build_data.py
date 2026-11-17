"""Inspect processed permit JSON and build a trimmed combined dataset for the map dashboard."""
import json
import os
from pathlib import Path

FILES = {
    "abbotsford": "Abbotsford",
    "burnaby": "Burnaby",
    "chilliwack": "Chilliwack",
    "coquitlam": "Coquitlam",
    "delta": "Delta",
    "halton_hills": "Halton Hills",
    "langley": "Langley",
    "maple_ridge": "Maple Ridge",
    "new_west": "New Westminster",
    "north_van": "City of North Vancouver",
    "dnv": "District of North Vancouver",
    "port_moody": "Port Moody",
    "richmond": "Richmond",
    "surrey": "Surrey",
    "ubc": "UBC",
    "vancouver": "Vancouver",
    "victoria": "Victoria",
    "west_van": "West Vancouver",
    "white_rock": "White Rock",
}
PROC = Path("data/processed")

# Every attribute carried onto each record: shown in the detail panel and written to the Excel
# export (the export flattens the object/array fields). raw_text is included so the export is a
# complete record of each application.
DETAIL_FIELDS = [
    "permit_type", "development_class", "development_name", "status",
    "number_of_stories", "units_total", "unit_mix",
    "height_m", "lot_area", "lot_area_unit", "floor_area", "floor_area_unit", "footprint_area",
    "parking_vehicle_stalls", "parking_bike_stalls", "parking_notes",
    "rental_or_strata", "rental_subtype", "rental_mix", "zoning_density", "occupancy_types", "occupancies",
    "energy_info", "building_materials", "mechanical_system_info", "retrofit_info",
    "extraction_method", "extraction_confidence", "needs_review", "needs_pdf_extraction",
    "source_url", "enrichment_url", "related_permits", "raw_text",
]

# The value-bearing fields the detail panel can tag as PDF-derived. For a row read entirely from a
# PDF (Surrey memos, Burnaby / Richmond list PDFs), every one of these that is present is tagged;
# for a mixed HTML + document-read row, only the fields field_methods marks are tagged.
BADGE_FIELDS = (
    "address", "development_class", "development_name", "units_total", "unit_mix",
    "number_of_stories", "height_m", "floor_area", "lot_area", "footprint_area",
    "parking_vehicle_stalls", "parking_bike_stalls", "zoning_density", "rental_or_strata",
    "parking_notes", "building_materials", "energy_info", "mechanical_system_info", "retrofit_info",
)
# Base extraction methods that mean the whole row was read from a PDF (vs an HTML page / map / API).
_PURE_PDF_BASES = ("pdf", "ollama")

# Geocoded coords for the HTML-source municipalities (North Van, Victoria).
gc_path = Path("tools/dashboard/geocoded.json")
GEO = json.loads(gc_path.read_text(encoding="utf-8")) if gc_path.exists() else {}

out = []
for slug, name in FILES.items():
    f = PROC / f"{slug}.json"
    if not f.exists():
        print("MISSING", f)
        continue
    rows = json.loads(f.read_text(encoding="utf-8"))
    gslug = GEO.get(slug, {})
    withxy = 0
    for r in rows:
        lat, lon, approx = r.get("latitude"), r.get("longitude"), False
        if lat is None and r.get("permit_id") in gslug:
            lat, lon, approx = gslug[r["permit_id"]]
        if lat is not None:
            withxy += 1
        rec = {
            "muni": name,
            "slug": slug,
            "id": r.get("permit_id"),
            "addr": r.get("address"),
            "class": r.get("development_class") or "unknown",
            "lat": round(lat, 6) if lat is not None else None,
            "lon": round(lon, 6) if lon is not None else None,
            "ms": [m for m in (r.get("milestones") or []) if m.get("milestone_date")],
            "docs": len(r.get("documents") or []),
        }
        # A row whose base extraction method is a PDF read (Surrey / Burnaby / Richmond) came wholly
        # from a PDF; a mixed "html+doc_read" row only has some fields from a document.
        base_method = (r.get("extraction_method") or "").split("+")[0]
        pure_pdf = any(base_method.startswith(b) for b in _PURE_PDF_BASES)
        # Source documents the fields were read from. The enrichment flags the chosen PDF with
        # `extracted`; a pure-PDF row's own memo is its source even when unflagged. (Burnaby / Richmond
        # carry no per-row document, but their source_url IS the list PDF, which the panel links.)
        docs = r.get("documents") or []
        src_docs = [{"url": d["url"], "title": d.get("title"), "role": d.get("doc_role")}
                    for d in docs if d.get("extracted") and d.get("url")]
        if not src_docs and pure_pdf and docs and docs[0].get("url"):
            d = docs[0]
            src_docs = [{"url": d["url"], "title": d.get("title"), "role": d.get("doc_role")}]
        if src_docs:
            rec["source_docs"] = src_docs
        # Fields to tag as PDF-derived: all present value fields for a pure-PDF row, else the fields
        # field_methods marks as document-read. A reviewer then knows what to check against the PDF.
        fm = r.get("field_methods") or {}
        if pure_pdf:
            pdf_fields = [f for f in BADGE_FIELDS if r.get(f) not in (None, "", [], {})]
        else:
            pdf_fields = [k for k, m in fm.items()
                          if any(t in str(m) for t in ("pdf", "claude", "ollama", "letter"))]
        if pdf_fields:
            rec["pdf_fields"] = pdf_fields
        if approx:
            rec["lat_approx"] = True
        for k in DETAIL_FIELDS:
            v = r.get(k)
            if v not in (None, "", [], {}):
                rec[k] = v
        # The extraction method names the in-session document read ("claude_read"); surface it
        # neutrally in the public dashboard as a generic document read.
        if rec.get("extraction_method"):
            rec["extraction_method"] = rec["extraction_method"].replace("claude_read", "doc_read")
        out.append(rec)
    print(f"{name:16} rows={len(rows):<5} mapped={withxy}")

geo = [r for r in out if r["lat"] is not None]
print(f"\nTOTAL rows={len(out)} geocoded={len(geo)}")
Path("tools/dashboard/permits.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print("wrote tools/dashboard/permits.json", os.path.getsize("tools/dashboard/permits.json") // 1024, "KB")
