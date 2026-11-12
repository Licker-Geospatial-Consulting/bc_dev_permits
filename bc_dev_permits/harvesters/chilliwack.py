"""City of Chilliwack harvester - Build Chilliwack (WordPress) project map.

buildchilliwack.com is a WordPress site whose /map/ page plots every project as a Mapbox marker. The
markers are embedded as a ``markersData`` JSON array (title, permalink, coordinates, a prose summary,
and units/storeys hints), so the harvester reads that array for the project list + lat/long rather
than driving the map. Each project's own page (/project/<slug>/) carries the full prose and a
"Project Details" block (a label -> value grid: Storeys, Units, Height, Parking, ...), a Project
Status and a Project Type, and a photo gallery whose site-plan image sometimes holds the unit mix.

Attribute mapping:
  marker title       -> development_name (and address when the title is a civic address)
  marker coordinates -> latitude / longitude (the array is [lon, lat])
  marker permalink   -> source_url; its slug -> permit_id (Build Chilliwack has no permit number)
  Project Details    -> number_of_stories / units_total / height_m / parking_vehicle_stalls /
                        floor_area (the curated, authoritative figures)
  Project Type       -> development_class (Condominium / Townhouse / Apartment / Affordable / Supportive
                        -> residential, Commercial / Retail, Industrial, Daycare -> institutional;
                        several distinct types -> mixed)
  Project Status     -> status (Proposed / Rezoning / Approved / Under Construction / Complete / Defeated)
  prose              -> raw_text, parsed by features.extract_all for anything Project Details omits
  gallery images     -> documents; a site-plan image is read with Claude for the unit mix (enrichment)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

from bc_dev_permits import config, consolidate, features

MUNICIPALITY = "chilliwack"
MAP_URL = config.CHILLIWACK_MAP_URL
BASE = config.CHILLIWACK_BASE
CACHE_DIR = str(config.HTTP_CACHE_DIR)

# Project Type keyword -> development_class (checked against each comma-separated type).
_TYPE_CLASS = (
    ("residential", ("condominium", "townhouse", "apartment", "residential", "affordable",
                     "supportive", "rental", "single family", "duplex", "housing",
                     "independent living", "senior")),
    ("commercial", ("commercial", "retail", "office", "hotel", "opportunity", "mixed",
                    "restaurant", "signage", "service", "gas")),
    ("industrial", ("industrial", "warehouse", "logistics", "processing", "agricultural",
                    "manufacturing")),
    ("institutional", ("daycare", "school", "institutional", "civic", "recreation", "church",
                       "care", "health", "education")),
)
_OCC_BY_CLASS = {
    "residential": ["residential"], "commercial": ["commercial"], "industrial": ["industrial"],
    "institutional": ["institutional"], "mixed": ["residential", "commercial"], "unknown": [],
}
_SQFT_TO_M2 = 0.09290304
# Gallery filename fragments that mark a renderng / map (not a data-bearing plan) - de-prioritized.
_IMG_SKIP = re.compile(r"render|location|aerial|photo|street|context|logo|exterior", re.IGNORECASE)


def _clean(value) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _int(text) -> int | None:
    m = re.search(r"\d[\d,]*", str(text or ""))
    return int(m.group().replace(",", "")) if m else None


# --------------------------------------------------------------------------- #
# HTTP (shared TTL file cache)
# --------------------------------------------------------------------------- #
def _session():
    import requests
    s = requests.Session()
    s.headers.setdefault("User-Agent", config.USER_AGENT)
    return s


def _cached_get(session, url: str, cache_prefix: str, use_cache: bool = True) -> str:
    """GET url as HTML text, backed by the shared TTL file cache."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"{cache_prefix}_{key}.html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    time.sleep(0.3)  # politeness: 108 project pages come off one WordPress host
    return text


# --------------------------------------------------------------------------- #
# Map markers (the project list)
# --------------------------------------------------------------------------- #
def parse_markers(map_html: str) -> list[dict]:
    """Return the project markers from the map page's embedded `markersData` array."""
    m = re.search(r"let\s+markersData\s*=\s*(\[.*?\]);", map_html, re.DOTALL)
    if not m:
        return []
    markers = json.loads(m.group(1))
    return [mk for mk in markers if mk.get("permalink")]


# --------------------------------------------------------------------------- #
# Project page
# --------------------------------------------------------------------------- #
def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml")


def project_details(soup) -> dict:
    """Return the Project Details block as {label: value} (the .detail-title/.detail-content grid)."""
    grid = soup.find("div", class_="details-grid")
    out: dict[str, str] = {}
    if not grid:
        return out
    for cell in grid.find_all("div", recursive=False):
        title = cell.find("div", class_="detail-title")
        content = cell.find("div", class_="detail-content")
        if title and content:
            label = _clean(title.get_text(" ", strip=True))
            value = _clean(content.get_text(" ", strip=True))
            if label and value:
                out[label] = value
    return out


def labeled_value(soup, label: str) -> str | None:
    """Return the value rendered after a bare '<label>' text block (Project Status / Project Type)."""
    for el in soup.find_all(string=re.compile(r"^\s*" + re.escape(label) + r"\s*$")):
        for sib in el.parent.next_siblings:
            if getattr(sib, "get_text", None):
                value = _clean(sib.get_text(" ", strip=True))
                if value:
                    return value
    return None


def prose_text(soup) -> str | None:
    """Concatenate the project's descriptive paragraphs (the body prose)."""
    parts = []
    for p in soup.find_all("p"):
        text = _clean(p.get_text(" ", strip=True))
        if text and len(text) >= 40:
            parts.append(text)
    return "\n\n".join(parts) or None


def gallery_images(soup) -> list[str]:
    """Full-size gallery image URLs, de-duplicated and with WordPress size suffixes stripped."""
    seen, out = set(), []
    for img in soup.select("[class*=gallery] img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or "/uploads/" not in src:
            continue
        # Drop the "-438x292" style size suffix WordPress adds, to get the original.
        full = re.sub(r"-\d+x\d+(?=\.\w+$)", "", src)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


_TYPE_SPLIT = re.compile(r"\s*,\s*")


def class_from_types(types: str | None, prose: str | None) -> tuple[str, list[str]]:
    """Map the comma-separated Project Type (+ prose) to development_class + occupancy_types."""
    classes = []
    for part in _TYPE_SPLIT.split((types or "").lower()):
        for cls, keys in _TYPE_CLASS:
            if any(k in part for k in keys):
                if cls not in classes:
                    classes.append(cls)
                break
    if (prose and re.search(r"mixed[\s-]?use", prose, re.IGNORECASE)) or len(classes) > 1:
        return "mixed", ["residential", "commercial"]
    if classes:
        return classes[0], _OCC_BY_CLASS[classes[0]]
    return "unknown", []


_ADDR_RE = re.compile(
    r"\b(\d{3,6}(?:\s*[-&]\s*\d{3,6})?\s+[A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*){0,3}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Way|Crescent|Cres|Place|Pl|Boulevard|Blvd|"
    r"Lane|Ln|Court|Crt|Court|Trail|Terrace))\b")
# Same pattern, case-insensitive, for lowercased slugs / image filenames.
_ADDR_RE_I = re.compile(_ADDR_RE.pattern, re.IGNORECASE)


def find_address(text: str | None) -> str | None:
    """Return the first civic address in the text ('45857 Tretheway Ave', '45044 Commercial Court').

    Applied to the marker title first, then the body prose. Some projects name no address in the
    title ('Petro-Canada and Starbucks') but state one in the description ('... at 45044 Commercial
    Court in Chilliwack, BC'), so the prose fallback gives those rows a reference point to geocode.
    """
    if not text:
        return None
    m = _ADDR_RE.search(text)
    return m.group(1) if m else None


def address_from_token(token: str | None) -> str | None:
    """Return a civic address from a lowercase slug or image filename, title-cased.

    Build Chilliwack's permalinks and plan-image filenames frequently encode the address
    ('9299-main-street', '9298-young-road-ortho', '45922-Thunderbird-Ln'), so when the title and
    prose name none this recovers it deterministically. The number must be a 3-6 digit civic number
    before a street-type word, so a slug like 'building-2' or 'andmar-1' yields nothing.
    """
    if not token:
        return None
    m = _ADDR_RE_I.search(re.sub(r"[-_]+", " ", token))
    return m.group(1).title() if m else None


def _image_address(images: list[str]) -> str | None:
    """First civic address recoverable from a gallery image filename (location / site-plan tiles)."""
    for url in images:
        stem = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        addr = address_from_token(stem)
        if addr:
            return addr
    return None


# Project Details label -> schema field. A label is matched by keyword (first match wins); the value
# is reduced to its number. Labels that have no scalar home (Trees, Buildings, Completion Date,
# Loading, amenity, divisible area) are left for raw_text.
def map_details(details: dict) -> dict:
    """Pull the schema scalars out of the Project Details label/value block."""
    out: dict = {}
    for label, value in details.items():
        low = label.lower()
        if "storey" in low and "number_of_stories" not in out:
            out["number_of_stories"] = _int(value)
        elif "height" in low and "clear" not in low and "height_m" not in out:
            m = re.search(r"(\d+(?:\.\d+)?)", value)
            out["height_m"] = float(m.group(1)) if m else None
        elif "parking" in low and "parking_vehicle_stalls" not in out:
            out["parking_vehicle_stalls"] = _int(value)
            out["parking_notes"] = f"{label}: {value}"
        elif ("floor area" in low or "gross floor" in low or low == "size") and "floor_area" not in out:
            # The site renders a superscript 2 (ft^2) that collapses to "ft 2" / "ft2" / "ft²" on
            # extraction, so accept an optional space and either glyph after the unit.
            m = re.search(r"([\d,]+)\s*(?:sq\.?\s*ft|ft|sf)\s*[²²2]?", value, re.IGNORECASE)
            if m:
                out["floor_area"] = round(int(m.group(1).replace(",", "")) * _SQFT_TO_M2, 1)
                out["floor_area_unit"] = "m2"
        elif (low in ("units", "# of units") or "residential unit" in low
              or "micro-unit" in low or "apartment unit" in low or "dwelling unit" in low) \
                and "units_total" not in out:
            out["units_total"] = _int(value)
    return out


def _raw_text(prose: str | None, details: dict, status: str | None, ptype: str | None) -> str | None:
    """Keep the prose plus the full Project Details / status / type for re-extraction."""
    parts = []
    if status:
        parts.append(f"Project Status: {status}")
    if ptype:
        parts.append(f"Project Type: {ptype}")
    if details:
        parts.append("Project Details: " + "; ".join(f"{k}: {v}" for k, v in details.items()))
    if prose:
        parts.append(prose)
    return "\n\n".join(parts) or None


# Valuable fields that, when missing, make a project a candidate for the Claude-read gallery pass.
_IMG_WORTHY = ("units_total", "unit_mix", "floor_area")


def _permit_id(permalink: str) -> str | None:
    """Derive a unique permit_id from the permalink's path after /project/.

    A single-segment permalink gives that slug ('9299-main-street'); a nested
    /project/<parent>/<child>/ permalink joins the whole path ('vantage/building-1' ->
    'vantage-building-1') so two projects reusing a generic child slug ('building-1') never collide.
    """
    if "/project/" in permalink:
        path = permalink.split("/project/", 1)[-1].strip("/")
    else:
        path = permalink.rstrip("/").rsplit("/", 1)[-1]
    return path.replace("/", "-") or None


def _child_slug(permalink: str) -> str:
    """Return the permalink's last path segment (the project's own slug, not its parent's)."""
    return permalink.rstrip("/").rsplit("/", 1)[-1]


def parse_project(html: str, marker: dict) -> dict | None:
    """Map one project page (+ its map marker) to a dev_permit row."""
    permalink = marker.get("permalink") or ""
    slug = _permit_id(permalink)
    if not slug:
        return None
    soup = _soup(html)
    title = _clean(marker.get("title")) or _clean(_page_title(soup))
    details = project_details(soup)
    prose = prose_text(soup)
    status = labeled_value(soup, "Project Status")
    ptype = labeled_value(soup, "Project Type")

    parsed = features.extract_all(prose or "")
    dev_class, occ = class_from_types(ptype, prose)
    mapped = map_details(details)

    coords = marker.get("coordinates") or [None, None]
    lon, lat = [*coords, None, None][:2]
    images = gallery_images(soup)

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": slug,
        "source_url": permalink,
        "development_name": title,
        # Title / prose first (cased, most reliable), then the child slug and plan-image filenames,
        # which on Build Chilliwack usually encode the civic address ('9299-main-street',
        # '9298-young-road'). Only the child segment is used, never the full nested path: a nested
        # path carries the PARENT's address ('.../46183-thomas-road/building-c'), which would wrongly
        # give every sub-building the parent address and let consolidate fold them away. Each
        # sub-building keeps its own coordinates and gets its reference address from the enrichment
        # pass (tools/chilliwack_enrich.py), which runs after consolidation.
        "address": (find_address(title) or find_address(prose)
                    or address_from_token(_child_slug(permalink)) or _image_address(images)),
        "latitude": round(lat, 6) if isinstance(lat, (int, float)) else None,
        "longitude": round(lon, 6) if isinstance(lon, (int, float)) else None,
        "permit_type": ptype,
        "development_class": dev_class,
        "occupancy_types": occ,
        "status": status,
        # Project Details are the curated figures; fall back to the deterministic prose parse.
        "number_of_stories": mapped.get("number_of_stories") or parsed["number_of_stories"],
        "units_total": mapped.get("units_total") or parsed["units_total"],
        "unit_mix": parsed["unit_mix"],
        "rental_or_strata": parsed["rental_or_strata"],
        "rental_subtype": parsed["rental_subtype"],
        "rental_mix": parsed["rental_mix"],
        "parking_vehicle_stalls": mapped.get("parking_vehicle_stalls") or parsed["parking_vehicle_stalls"],
        "parking_bike_stalls": parsed["parking_bike_stalls"],
        "parking_notes": mapped.get("parking_notes") or parsed["parking_notes"],
        "floor_area": mapped.get("floor_area") or parsed["floor_area"],
        "floor_area_unit": mapped.get("floor_area_unit") or parsed["floor_area_unit"],
        "height_m": mapped.get("height_m") or parsed["height_m"],
        "zoning_density": parsed["zoning_density"],
        "raw_text": _raw_text(prose, details, status, ptype),
        "extraction_method": "html",
        "milestones": [],
        "documents": [{"url": u, "title": None, "doc_role": "image"} for u in images],
        "is_parsed": bool(prose or details),
    }
    # The unit mix (and sometimes floor area) lives only in a gallery site-plan image; flag the row
    # for the Claude-read gallery pass when a valuable field is missing and a plan image exists.
    plan_images = [u for u in images if not _IMG_SKIP.search(u)]
    row["needs_pdf_extraction"] = bool(plan_images) and any(
        not features.has_value(row.get(f)) for f in _IMG_WORTHY)
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = (row["extraction_confidence"] < 0.6
                           or row["development_class"] in (None, "unknown"))
    return row


def _page_title(soup) -> str | None:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"]
    return soup.title.get_text(strip=True) if soup.title else None


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every Build Chilliwack project from the map markers + each project page."""
    session = session or _session()
    markers = parse_markers(_cached_get(session, MAP_URL, "chilliwack_map", use_cache))
    if limit:
        markers = markers[:limit]
    rows = []
    for marker in markers:
        try:
            html = _cached_get(session, marker["permalink"], "chilliwack_project", use_cache)
        except OSError:
            continue
        row = parse_project(html, marker)
        if row:
            rows.append(row)
    consolidate.consolidate_by_address(rows)
    return rows
