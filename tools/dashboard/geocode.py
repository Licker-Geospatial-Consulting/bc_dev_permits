"""Geocode North Van + Victoria permit addresses via OSM Nominatim (cached, 1.1s rate limit).

Writes tools/dashboard/geocoded.json = {slug: {permit_id: [lat, lon, approx_bool]}}.
Addresses that don't resolve fall back to the city centroid (approx=True).
"""
import json
import re
import time
from pathlib import Path

import requests

# slug -> (city, (centroid lat, lon), province). Most rows already carry map coordinates and are
# skipped; only the coordless rows (HTML-source municipalities, and Halton Hills' standalone major
# applications like 530 Guelph St) are geocoded here.
CITY = {
    "north_van": ("North Vancouver", (49.3200, -123.0724), "BC"),
    "victoria": ("Victoria", (48.4284, -123.3656), "BC"),
    "west_van": ("West Vancouver", (49.3286, -123.1608), "BC"),
    "halton_hills": ("Halton Hills", (43.6450, -79.9200), "ON"),
}

# Street-type suffixes used to pick the street phrase out of a West Van address.
_WV_TYPES = (r"(?:Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Way|Close|Street|St|Crescent|Cres|"
             r"Place|Pl|Court|Crt|Terrace|Gate|Boulevard|Blvd)")


def wv_query(addr):
    """Build 'civic street' from a West Van address, picking the first civic number.

    Handles the site's irregular forms: a unit-civic pair like "11-3085 Deer Ridge Close"
    (the 4-digit civic is the second number), a civic range "875-885 Jefferson Avenue" and a
    multi-lot list "2810, 2814 and 2818 Bellevue Avenue" (take the first civic), and a bare
    area name like "Rodgers Creek Lane" with no civic number (returns None -> city centroid).
    """
    addr = (addr or "").strip()
    nums = re.findall(r"\d+", addr)
    if not nums:
        return None
    civic = nums[0]
    # A "<unit>-<civic>" pair (first number has fewer digits than the second) is a unit prefix,
    # so the real civic number is the second one; a same-width pair is a civic range (keep first).
    dash = re.match(r"(\d+)\s*[-–]\s*(\d+)\b", addr)
    if dash and len(dash.group(1)) < len(dash.group(2)):
        civic = dash.group(2)
    street = re.search(rf"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+{_WV_TYPES}\b", addr)
    return f"{civic} {street.group(0)}" if street else None
UA = "bc-dev-permits-dashboard/0.1 (+contact: himalya@lgeo.co)"
CACHE = Path("tools/dashboard/geocache.json")
cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
session = requests.Session()
session.headers["User-Agent"] = UA


def query_for(row, slug):
    """Build a geocodable 'number street' string from the address or the URL slug."""
    addr = (row.get("address") or "").strip()
    if slug == "west_van":
        return wv_query(addr)
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


def geocode(q, city, province="BC"):
    key = f"{q}, {city}"  # city is unique per slug, so the key needs no province
    if key in cache:
        return cache[key]
    params = {"q": f"{q}, {city}, {province}, Canada", "format": "json", "limit": 1,
              "countrycodes": "ca"}
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
for slug, (city, centroid, province) in CITY.items():
    rows = json.loads(Path(f"data/processed/{slug}.json").read_text(encoding="utf-8"))
    out[slug] = {}
    hit = approx = 0
    for row in rows:
        pid = row.get("permit_id")
        if not pid:
            continue
        if row.get("latitude") is not None:
            continue  # already has map coordinates; only fill gaps
        q = query_for(row, slug)
        res = geocode(q, city, province) if q else None
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
