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
    "surrey": ("Surrey", (49.1913, -122.8490), "BC"),
    "ubc": ("Vancouver", (49.2606, -123.2460), "BC"),
    "langley": ("Langley", (49.1009, -122.6610), "BC"),
    "richmond": ("Richmond", (49.1666, -123.1336), "BC"),
    "vancouver": ("Vancouver", (49.2497, -123.1193), "BC"),
}

# Street-type suffixes used to pick a civic street phrase out of a UBC project address.
_UBC_TYPES = (r"(?:Avenue|Ave|Road|Rd|Drive|Dr|Mall|Boulevard|Blvd|Way|Crescent|Cres|"
             r"Street|St|Place|Pl|Lane|Ln)")


def ubc_query(addr):
    """Build 'civic street' from a UBC project address, else None -> campus centroid.

    UBC addresses vary: a clean civic address ("6303 NW Marine Drive"), a project label with an
    embedded address ("... - 5868 & 5828 Binning Ave, 3758 Wesbrook Mall"), or a bare project /
    location name ("Douglas T. Kenny Seismic Upgrades"). Take the first civic-number + street phrase
    anywhere in the string (1-3 capitalized words before a street type); a name with none returns
    None so the row plots at the campus centroid.
    """
    addr = (addr or "").strip()
    m = re.search(rf"\b(\d{{3,5}})\s+((?:[A-Z][A-Za-z.]*\s+){{1,3}}{_UBC_TYPES})\b", addr)
    return f"{m.group(1)} {m.group(2)}" if m else None


def surrey_query(addr):
    """Build 'civic street' from a Surrey address: "12042 - 100A Avenue" -> "12042 100A Avenue".

    Surrey writes "<civic> - <street>" (the street itself often starts with a number, e.g. 100A
    Avenue / 184 Street), so the " - " is dropped rather than read as a civic range; a non-numeric
    lead (a project name) returns None -> city centroid.
    """
    addr = (addr or "").strip()
    if not re.match(r"^\d", addr):
        return None
    return re.sub(r"^(\d+)\s*[-–]\s*", r"\1 ", addr)  # noqa: RUF001

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
    if slug == "surrey":
        return surrey_query(addr)
    if slug == "ubc":
        return ubc_query(addr)
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


def geocode(q, city, province="BC", viewbox=None):
    """Geocode 'q, city' via Nominatim.

    With a viewbox (minLon,maxLat,maxLon,minLat), restrict the search to that box (bounded=1) so
    campus streets that extend off-site are not matched elsewhere.
    """
    key = f"{q}, {city}" + (" [bbox]" if viewbox else "")  # bounded results cache separately
    if key in cache:
        return cache[key]
    params = {"q": f"{q}, {city}, {province}, Canada", "format": "json", "limit": 1,
              "countrycodes": "ca"}
    if viewbox:
        params["viewbox"] = viewbox
        params["bounded"] = 1
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


# UBC campus addresses geocode poorly with a plain "Vancouver" query because NW Marine Drive,
# University Boulevard and Wesbrook Mall all extend east off campus, so Nominatim matches the wrong
# segment. For UBC we restrict the search to the campus bounding box and reject any point that lands
# outside the (slightly buffered) campus polygon, falling back to the centroid.
def _ubc_bounds():
    bpath = Path("tools/dashboard/boundaries.json")
    if not bpath.exists():
        return None, None
    from shapely.geometry import shape  # noqa: PLC0415 (optional, only needed for UBC)
    fc = json.loads(bpath.read_text(encoding="utf-8"))
    feat = next((f for f in fc["features"] if f["properties"].get("muni") == "UBC"), None)
    if not feat:
        return None, None
    poly = shape(feat["geometry"])
    minx, miny, maxx, maxy = poly.bounds
    return f"{minx},{maxy},{maxx},{miny}", poly.buffer(0.002)


out = {}
for slug, (city, centroid, province) in CITY.items():
    rows = json.loads(Path(f"data/processed/{slug}.json").read_text(encoding="utf-8"))
    out[slug] = {}
    hit = approx = 0
    # UBC: constrain to the campus bbox and validate results fall inside the campus polygon.
    viewbox, campus = _ubc_bounds() if slug == "ubc" else (None, None)
    for row in rows:
        pid = row.get("permit_id")
        if not pid:
            continue
        if row.get("latitude") is not None:
            continue  # already has map coordinates; only fill gaps
        q = query_for(row, slug)
        res = geocode(q, city, province, viewbox) if q else None
        if res and campus is not None:
            from shapely.geometry import Point  # noqa: PLC0415
            if not campus.contains(Point(res[1], res[0])):
                res = None  # matched off-campus; use the centroid instead
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
