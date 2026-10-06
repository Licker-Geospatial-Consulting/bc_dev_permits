"""Fetch the street network (OSM Overpass) within each municipality's permit extent and
embed it as simplified GeoJSON lines, so the dashboard has a real street basemap without
external tiles (which the artifact CSP blocks)."""
import json
import time
from collections import defaultdict
from pathlib import Path

import requests
from shapely.geometry import LineString, mapping

MAJOR = {"motorway", "trunk", "primary", "secondary",
         "motorway_link", "trunk_link", "primary_link", "secondary_link"}
MINOR = {"tertiary", "tertiary_link", "residential", "unclassified", "living_street"}
WANT = MAJOR | MINOR
UA = "bc-dev-permits-dashboard/0.1 (+contact: himalya@lgeo.co)"
# Several Overpass mirrors; the main endpoint rate-limits bursts, so we rotate + retry.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def fetch_overpass(session, query):
    """POST an Overpass query, retrying across mirrors with backoff until one returns JSON."""
    last = None
    for attempt in range(6):
        url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            resp = session.post(url, data={"data": query}, timeout=180)
            if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                return resp.json()
            last = f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(5 * (attempt + 1))  # 5s, 10s, 15s ... back off on rate limits
    raise RuntimeError(f"all Overpass mirrors failed: {last}")

permits = json.loads(Path("tools/dashboard/permits.json").read_text(encoding="utf-8"))
boxes = defaultdict(lambda: [90, 180, -90, -180])  # s,w,n,e
for p in permits:
    if p.get("lat") is None:
        continue
    b = boxes[p["muni"]]
    b[0] = min(b[0], p["lat"]); b[1] = min(b[1], p["lon"])
    b[2] = max(b[2], p["lat"]); b[3] = max(b[3], p["lon"])

# Per-municipality cache so Overpass rate-limiting never loses already-fetched munis: a muni
# that succeeded stays cached, so a re-run only retries the ones still missing.
CACHE = Path("tools/dashboard/roads_cache")
CACHE.mkdir(exist_ok=True)

s = requests.Session(); s.headers["User-Agent"] = UA
for muni, (so, we, no, ea) in boxes.items():
    cache_file = CACHE / f"{muni.replace(' ', '_')}.json"
    if cache_file.exists() and json.loads(cache_file.read_text(encoding="utf-8")):
        print(f"{muni:16} cached ({len(json.loads(cache_file.read_text(encoding='utf-8')))} ways)")
        continue
    pad = 0.006
    bbox = f"{so-pad},{we-pad},{no+pad},{ea+pad}"
    q = (f"[out:json][timeout:90];way[\"highway\"~\"^({'|'.join(WANT)})$\"]({bbox});out geom;")
    try:
        data = fetch_overpass(s, q)
    except Exception as exc:  # noqa: BLE001
        print(muni, "FAILED", exc); continue
    muni_feats = []
    for el in data.get("elements", []):
        g = el.get("geometry")
        if not g or len(g) < 2:
            continue
        line = LineString([(pt["lon"], pt["lat"]) for pt in g]).simplify(0.00012, preserve_topology=False)
        if line.is_empty or len(line.coords) < 2:
            continue
        muni_feats.append({"type": "Feature",
                           "properties": {"maj": 1 if el.get("tags", {}).get("highway") in MAJOR else 0},
                           "geometry": mapping(line)})
    cache_file.write_text(json.dumps(muni_feats, separators=(",", ":")), encoding="utf-8")
    print(f"{muni:16} bbox={bbox} ways={len(muni_feats)}")
    time.sleep(1.5)

# Assemble roads.json from every cached municipality.
feats = []
for cache_file in sorted(CACHE.glob("*.json")):
    feats.extend(json.loads(cache_file.read_text(encoding="utf-8")))
missing = [m for m in boxes if not (CACHE / f"{m.replace(' ', '_')}.json").exists()]
fc = {"type": "FeatureCollection", "features": feats}
out = Path("tools/dashboard/roads.json")
out.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
print("wrote", out, round(out.stat().st_size / 1024), "KB,", len(feats), "ways",
      f"| MISSING: {missing}" if missing else "| all munis present")
