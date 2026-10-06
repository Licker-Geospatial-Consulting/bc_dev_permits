"""Fetch + simplify municipal boundary polygons (OSM Nominatim) for map context."""
import json
import time
from pathlib import Path

import requests
from shapely.geometry import mapping, shape

QUERIES = {
    "Coquitlam": "Coquitlam, British Columbia, Canada",
    "Maple Ridge": "Maple Ridge, British Columbia, Canada",
    "North Vancouver": "City of North Vancouver, British Columbia, Canada",
    "Port Moody": "Port Moody, British Columbia, Canada",
    "Victoria": "Victoria, British Columbia, Canada",
}
UA = "bc-dev-permits-dashboard/0.1 (+contact: himalya@lgeo.co)"
s = requests.Session()
s.headers["User-Agent"] = UA

feats = []
for name, q in QUERIES.items():
    params = {"q": q, "format": "json", "limit": 1, "polygon_geojson": 1, "countrycodes": "ca"}
    try:
        r = s.get("https://nominatim.openstreetmap.org/search", params=params, timeout=40).json()
        geom = shape(r[0]["geojson"])
        simp = geom.simplify(0.0008, preserve_topology=True)  # ~80 m tolerance
        feats.append({"type": "Feature", "properties": {"muni": name}, "geometry": mapping(simp)})
        print(f"{name:16} type={geom.geom_type} -> simplified")
    except Exception as exc:  # noqa: BLE001
        print(f"{name:16} FAILED {exc}")
    time.sleep(1.2)

fc = {"type": "FeatureCollection", "features": feats}
out = Path("tools/dashboard/boundaries.json")
out.write_text(json.dumps(fc), encoding="utf-8")
print("wrote", out, round(out.stat().st_size / 1024, 1), "KB,", len(feats), "features")
