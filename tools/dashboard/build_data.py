"""Inspect processed permit JSON and build a trimmed combined dataset for the map dashboard."""
import json
import os
from pathlib import Path

FILES = {
    "coquitlam": "Coquitlam",
    "maple_ridge": "Maple Ridge",
    "north_van": "North Vancouver",
    "port_moody": "Port Moody",
    "victoria": "Victoria",
    "west_van": "West Vancouver",
}
PROC = Path("data/processed")

# Fields we surface in the detail panel (compact; skip giant raw_text).
DETAIL_FIELDS = [
    "permit_type", "development_class", "status", "number_of_stories", "units_total",
    "unit_mix", "height_m", "lot_area", "lot_area_unit", "floor_area", "floor_area_unit",
    "footprint_area", "parking_vehicle_stalls", "parking_bike_stalls", "parking_notes",
    "rental_or_strata", "rental_subtype", "zoning_density", "energy_info",
    "building_materials", "mechanical_system_info", "retrofit_info",
    "permit_type", "development_name", "extraction_method", "extraction_confidence", "needs_review",
    "enrichment_url", "related_permits",
]

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
            "ms": [m for m in (r.get("milestones") or []) if m.get("milestone_date")][:1],
            "docs": len(r.get("documents") or []),
        }
        if approx:
            rec["lat_approx"] = True
        for k in DETAIL_FIELDS:
            v = r.get(k)
            if v not in (None, "", [], {}):
                rec[k] = v
        out.append(rec)
    print(f"{name:16} rows={len(rows):<5} mapped={withxy}")

geo = [r for r in out if r["lat"] is not None]
print(f"\nTOTAL rows={len(out)} geocoded={len(geo)}")
Path("tools/dashboard/permits.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print("wrote tools/dashboard/permits.json", os.path.getsize("tools/dashboard/permits.json") // 1024, "KB")
