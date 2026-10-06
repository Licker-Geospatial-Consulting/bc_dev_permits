"""Geocode North Van + Victoria permit addresses via OSM Nominatim (cached, 1.1s rate limit).

Writes tools/dashboard/geocoded.json = {slug: {permit_id: [lat, lon, approx_bool]}}.
Addresses that don't resolve fall back to the city centroid (approx=True).
"""
import json
import re
import time
from pathlib import Path

import requests

CITY = {
    "north_van": ("North Vancouver", (49.3200, -123.0724)),
    "victoria": ("Victoria", (48.4284, -123.3656)),
}
UA = "bc-dev-permits-dashboard/0.1 (+contact: himalya@lgeo.co)"
CACHE = Path("tools/dashboard/geocache.json")
cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
session = requests.Session()
session.headers["User-Agent"] = UA


def query_for(row, slug):
    """Build a geocodable 'number street' string from the address or the URL slug."""
    addr = (row.get("address") or "").strip()
    # A usable street address starts with a house number; take the first of any range.
    if re.match(r"^\d", addr):
        m = re.match(r"(\d+)\s*[-–]\s*\d+\s+(.*)", addr)   # "216-232 East 3rd Street"
        return f"{m.group(1)} {m.group(2)}" if m else addr
    if slug == "north_van" and row.get("source_url"):
        seg = row["source_url"].rstrip("/").split("/")[-1].replace("-", " ").strip()
        if re.match(r"^\d", seg):
            m = re.match(r"(\d+)\s+\d+\s+(.*)", seg)       # "273 279 E 6th Street"
            return f"{m.group(1)} {m.group(2)}" if m else seg
    return None  # project name, not an address


def geocode(q, city):
    key = f"{q}, {city}"
    if key in cache:
        return cache[key]
    params = {"q": f"{q}, {city}, BC, Canada", "format": "json", "limit": 1, "countrycodes": "ca"}
    try:
        r = session.get("https://nominatim.openstreetmap.org/search", params=params, timeout=30)
        data = r.json()
        result = [float(data[0]["lat"]), float(data[0]["lon"])] if data else None
    except Exception as exc:  # noqa: BLE001
        print("  err", q, exc)
        result = None
    cache[key] = result
    time.sleep(1.1)
    return result


out = {}
for slug, (city, centroid) in CITY.items():
    rows = json.loads(Path(f"data/processed/{slug}.json").read_text(encoding="utf-8"))
    out[slug] = {}
    hit = approx = 0
    for row in rows:
        pid = row.get("permit_id")
        if not pid:
            continue
        q = query_for(row, slug)
        res = geocode(q, city) if q else None
        if res:
            out[slug][pid] = [res[0], res[1], False]
            hit += 1
        else:
            out[slug][pid] = [centroid[0], centroid[1], True]
            approx += 1
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")  # checkpoint
    print(f"{slug}: geocoded={hit} approx(centroid)={approx}")

Path("tools/dashboard/geocoded.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print("wrote tools/dashboard/geocoded.json")
