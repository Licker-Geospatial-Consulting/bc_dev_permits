"""Town of Halton Hills (Ontario) harvester - ArcGIS MapServer + HTML major-app cross-reference.

Halton Hills publishes its development applications two ways:

* A **map** (map.haltonhills.ca/HT5, a VertiGIS/Geocortex HTML5 viewer) over an ArcGIS MapServer.
  Layer **24 P_DevelopmentApplications** carries every application (polygon) with clean structured
  attributes - file number, type, location, land use, description, received date, status, next
  step. This is the comprehensive source, queried directly (see references/arcgis_webmaps.md).
* A public **HTML page** that lists the ~10 "major" applications, each linking to a subpage whose
  prose is far richer than the map's terse description (e.g. the map says "apartment buildings
  with 6 residential towers"; the subpage gives "6 towers 17-22 storeys, 1,481 units, 1,481
  parking"). Those subpages are cross-referenced to overlay the richer prose where an application
  matches by civic address.

Attribute -> schema mapping (layer 24):
  FILE_NO   -> permit_id (e.g. "D12SUB12.001")       LOCATION -> address
  FILE_TYP  -> permit_type (Subdivision / OPA / ...)  LAND_USE -> development_class hint
  APP_DESC  -> raw_text, parsed by features.py        REC_DATE -> "Application Received" milestone
  CUR_STAT  -> status                                 P_NX_STP -> next step (kept in raw_text)
  TWN_AREA  -> area of town (kept in raw_text)         geometry -> latitude/longitude (centroid)

P_CONTAC (the planning contact's name + phone extension) is a staff name; the schema has no
column for it and we do not surface such detail, so it is never requested or stored.
"""

from __future__ import annotations

import re

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "halton_hills"
SERVICE_URL = config.HALTON_HILLS_SERVICE_URL
APPLICATIONS_LAYER = f"{SERVICE_URL}/24"
LIST_URL = config.HALTON_HILLS_LIST_URL
MAP_URL = "https://map.haltonhills.ca/HT5/Index.html?viewer=p.HT5&run=WFSU&qS=1&qL=24&LayerTheme=5"
_OUT_FIELDS = "OBJECTID,MAP_ID,LOCATION,TWN_AREA,FILE_NO,FILE_TYP,LAND_USE,APP_DESC,REC_DATE,CUR_STAT,P_NX_STP"
MIN_PROSE_CHARS = 20

# Halton Hills' LAND_USE values map straight onto development_class; anything else falls back to
# whatever the description prose classifies as.
_CLASS_BY_USE = {
    "residential": "residential", "commercial": "commercial", "industrial": "industrial",
    "institutional": "institutional", "mixed use": "mixed", "mixed": "mixed",
}
_OCC_BY_CLASS = {
    "residential": ["residential"], "commercial": ["commercial"], "industrial": ["industrial"],
    "institutional": ["institutional"], "mixed": ["residential", "commercial"], "unknown": [],
}
# Street-type suffixes / directions dropped when picking an address's distinctive name token.
_STREET_TYPES = frozenset({
    "ST", "STREET", "RD", "ROAD", "DR", "DRIVE", "AVE", "AV", "AVENUE", "BLVD", "CRES", "CRESCENT",
    "CRT", "COURT", "CT", "LANE", "LN", "LINE", "WAY", "CIRCLE", "CIR", "PL", "PLACE",
    "SIDEROAD", "TRAIL", "GATE", "TERRACE", "N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST",
})
# Connectors joining the civic numbers of a multi-address title ("16 and 18 Mill Street"): dropped
# so the distinctive street token is "mill", not "and".
_ADDRESS_STOPWORDS = frozenset({"AND", "THE", "OF", "AT"})

# A Halton Hills planning file number, e.g. "D09OPA25.001" or "D12SUB24.001". The prefix after the
# two-digit stream code names the application type.
_FILE_NO = re.compile(r"D\d{2}([A-Z]{2,4})\d{2}\.\d{3}")
_TYPE_BY_FILE_PREFIX = {
    "OPA": "OPA", "ZBA": "ZBA", "SUB": "Subdivision", "SPA": "Site Plan", "CDM": "Condominium",
    "MV": "Minor Variance", "CV": "Consent", "PLS": "Part Lot Control",
}


def _clean(value) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _development_class(land_use: str | None, prose_class: str) -> str:
    cls = _CLASS_BY_USE.get((land_use or "").strip().lower())
    return cls or prose_class


def _raw_text(attrs: dict) -> str:
    """Return the description plus the structured context, so the row is re-extractable."""
    lines = [_clean(attrs.get("APP_DESC"))]
    for label, key in (("File type", "FILE_TYP"), ("Use", "LAND_USE"), ("Status", "CUR_STAT"),
                       ("Next step", "P_NX_STP"), ("Area", "TWN_AREA")):
        val = _clean(attrs.get(key))
        if val:
            lines.append(f"{label}: {val}")
    return "\n".join(line for line in lines if line)


def _row_from_feature(feature: dict) -> dict | None:
    """Map one application feature to a dev_permit row, parsing its description into fields."""
    attrs = feature.get("attributes") or {}
    file_no = _clean(attrs.get("FILE_NO"))
    if not file_no:
        return None
    lat, lon = ag.centroid(feature.get("geometry"))
    desc = _clean(attrs.get("APP_DESC")) or ""
    prose = features.extract_all(desc if len(desc) >= MIN_PROSE_CHARS else "")
    dev_class = _development_class(attrs.get("LAND_USE"), prose["development_class"])

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": file_no,
        "source_url": MAP_URL,
        "development_name": None,
        "address": _clean(attrs.get("LOCATION")),
        "latitude": lat,
        "longitude": lon,
        "permit_type": _clean(attrs.get("FILE_TYP")),
        "development_class": dev_class,
        "occupancy_types": _OCC_BY_CLASS.get(dev_class, []),
        "status": _clean(attrs.get("CUR_STAT")),
        "number_of_stories": prose["number_of_stories"],
        "units_total": prose["units_total"],
        "unit_mix": prose["unit_mix"],
        "rental_or_strata": prose["rental_or_strata"],
        "rental_subtype": prose["rental_subtype"],
        "rental_mix": prose["rental_mix"],
        "parking_vehicle_stalls": prose["parking_vehicle_stalls"],
        "parking_bike_stalls": prose["parking_bike_stalls"],
        "parking_notes": prose["parking_notes"],
        "floor_area": prose["floor_area"],
        "floor_area_unit": prose["floor_area_unit"],
        "zoning_density": prose["zoning_density"],
        "height_m": prose["height_m"],
        "raw_text": _raw_text(attrs) or None,
        "extraction_method": "arcgis",
        "milestones": _milestones(attrs),
        "documents": [],
        "is_parsed": bool(desc),
        "needs_pdf_extraction": False,
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6 or dev_class == "unknown"
    return row


def _milestones(attrs: dict) -> list[dict]:
    received = ag.epoch_ms_to_date(attrs.get("REC_DATE"))
    return [{"milestone": "Application Received", "milestone_date": received}] if received else []


def parse_features(features_list: list[dict]) -> list[dict]:
    """Map MapServer /query features into dev_permit rows (no network)."""
    rows = [_row_from_feature(f) for f in features_list]
    return [r for r in rows if r]


# --------------------------------------------------------------------------- #
# Major-application HTML cross-reference (richer prose than the map's APP_DESC)
# --------------------------------------------------------------------------- #
def _civic_key(address: str | None) -> tuple[int | None, str | None]:
    """Parse '(civic number, distinctive street token)' from an address, for matching."""
    if not address:
        return None, None
    tokens = re.sub(r"[.,&]", " ", address.upper()).split()
    civic = next((int(m.group()) for t in tokens if (m := re.search(r"\d+", t))), None)
    names = [t for t in tokens
             if t.isalpha() and t not in _STREET_TYPES and t not in _ADDRESS_STOPWORDS and len(t) >= 3]
    return civic, (names[0].lower() if names else None)


def _file_numbers(text: str | None) -> list[str]:
    """Return the Halton planning file numbers in a string, in order and de-duplicated."""
    return list(dict.fromkeys(m.group(0) for m in _FILE_NO.finditer(text or "")))


def _permit_type_from_files(file_nos: list[str]) -> str | None:
    """Derive a permit type from file-number prefixes, e.g. ['...OPA...','...ZBA...'] -> 'OPA, ZBA'."""
    seen: list[str] = []
    for fn in file_nos:
        m = _FILE_NO.search(fn)
        label = _TYPE_BY_FILE_PREFIX.get(m.group(1)) if m else None
        if label and label not in seen:
            seen.append(label)
    return ", ".join(seen) or None


def major_app_index(session, use_cache: bool = True) -> list[dict]:
    """Scrape the list page's major-application subpages into a list of parsed major applications.

    Each entry is {slug, title, url, civic_key, file_numbers, fields}, where ``fields`` is the
    combined field set from the subpage prose (plus any curated override for that slug).
    """
    from bs4 import BeautifulSoup

    base = "/work/planning-development/active-development-applications/"
    soup = BeautifulSoup(_cached_get(session, LIST_URL, use_cache), "lxml")
    links: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if base in href and href.rstrip("/") != base.rstrip("/"):
            slug = href.split(base)[-1].strip("/")
            if slug and "/" not in slug:
                links.setdefault(slug, a.get_text(" ", strip=True))

    majors: list[dict] = []
    for slug, title in links.items():
        url = f"{LIST_URL}/{slug}"
        sections = _subpage_sections(session, url, use_cache)
        if not sections:
            continue
        majors.append({
            "slug": slug, "title": title, "url": url,
            "civic_key": _civic_key(title),
            "file_numbers": _file_numbers(" ".join(sections)),
            "fields": _extract_sections(sections),
            "override": _MAJOR_OVERRIDES.get(slug, {}),
        })
    return majors


def _subpage_sections(session, url: str, use_cache: bool = True) -> list[str]:
    """Return the substantial description sections of a major-app subpage (Froala .fr-view blocks)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_cached_get(session, url, use_cache), "lxml")
    return [re.sub(r"\s+", " ", n.get_text(" ", strip=True)).strip()
            for n in soup.select("div.fr-view") if len(n.get_text(strip=True)) > 100]


# Count fields are combined by MAX across sections (sibling sections restate the same totals, so
# summing the concatenated text double-counts); the rest take the first section that states them.
_MAJOR_MAX_FIELDS = ("units_total", "number_of_stories", "parking_vehicle_stalls",
                     "parking_bike_stalls", "floor_area")
_MAJOR_FIRST_FIELDS = ("floor_area_unit", "zoning_density", "height_m", "rental_or_strata",
                       "rental_subtype", "parking_notes")
_MAJOR_OVERLAY_FIELDS = (*_MAJOR_MAX_FIELDS, *_MAJOR_FIRST_FIELDS, "unit_mix")

# A subpage's "Submissions" sections are just document lists ("Contact X to request a copy of the
# documents below ..."); they carry no project data and would pad raw_text, so they are dropped.
_DOC_LIST_MARKER = re.compile(r"request a copy of", re.IGNORECASE)

# Curated corrections for the handful of major applications whose prose does not parse
# deterministically: a revision that *reduces* the program (so the max-combine would keep the
# superseded original), or a parking note phrased per-unit. Keyed by subpage slug.
_MAJOR_OVERRIDES: dict[str, dict] = {
    # Revised May 2022 from a 6-storey/52-unit building down to two 4-storey buildings, 34 units.
    "16-18-mill-development-proposal": {
        "units_total": 34,
        "number_of_stories": 4,
        "note": "Revised May 2022: two 4-storey residential buildings, 34 units total "
                "(originally a single 6-storey, 52-unit apartment building).",
    },
    # Parking is stated per unit plus visitor spaces and parkettes, so the deterministic "4 ...
    # parking spaces" is per-unit, not a site total: carry it as a note and null the bad total.
    "159-confederation": {
        "parking_vehicle_stalls": None,
        "parking_notes": "4 parking spaces per unit (2 garage, 2 driveway); 26 visitor parking "
                         "spaces; 2 neighbourhood parkettes.",
    },
    # 12 stacked townhouse units (the prose also lists "four ... per block", which the parser must
    # not read as the type total).
    "37-king-street": {
        "unit_mix": {"townhouse": 12},
    },
}


def _extract_sections(sections: list[str]) -> dict:
    """Parse each description section and combine into one field set (max counts, first of the rest).

    A subpage splits into Description / Application Process / Project Status / Submissions sections
    that restate the project, so each is parsed on its own and the counts are taken as the maximum
    rather than summed - otherwise "82 total units" plus "81 townhouses + 1 detached" reads as 164.
    The document-list ("Submissions") sections carry no data and are dropped; the remaining prose is
    kept whole as ``description`` so program details like parking and amenities survive in raw_text.
    """
    prose = [s for s in sections if not _DOC_LIST_MARKER.search(s)] or sections[:1]
    out: dict = {"description": "\n\n".join(prose), "development_class": "unknown", "unit_mix": None}
    best_mix_len = 0
    for section in prose:
        f = features.extract_all(section)
        for field in _MAJOR_MAX_FIELDS:
            if features.has_value(f.get(field)):
                out[field] = max(out.get(field) or 0, f[field])
        for field in _MAJOR_FIRST_FIELDS:
            if field not in out and features.has_value(f.get(field)):
                out[field] = f[field]
        if f.get("unit_mix") and sum(f["unit_mix"].values()) > best_mix_len:
            out["unit_mix"], best_mix_len = f["unit_mix"], sum(f["unit_mix"].values())
        if out["development_class"] == "unknown" and f["development_class"] != "unknown":
            out["development_class"] = f["development_class"]
    return out


def _apply_major_fields(row: dict, fields: dict, override: dict | None = None) -> None:
    """Overlay a major application's parsed fields onto a row (richer prose wins; empty keeps).

    A curated ``override`` is applied last and is authoritative: its values win even over a parsed
    field and even when the value is ``None`` (used to clear a figure the prose states per unit, not
    as a site total). Its ``note`` is appended to raw_text rather than stored as a column.
    """
    override = override or {}
    for field in _MAJOR_OVERLAY_FIELDS:
        if features.has_value(fields.get(field)):
            row[field] = fields[field]
    if fields["development_class"] != "unknown":
        row["development_class"] = fields["development_class"]
        row["occupancy_types"] = _OCC_BY_CLASS.get(fields["development_class"], [])
    row.update({field: value for field, value in override.items() if field != "note"})
    extra = "\n\n".join(p for p in (fields.get("description"), override.get("note")) if p)
    row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), extra) if p)


def _standalone_row(major: dict) -> dict:
    """Build a dev_permit row for a major application with no matching map feature (e.g. 530 Guelph)."""
    fields = major["fields"]
    dev_class = fields["development_class"]
    row = {
        "municipality": MUNICIPALITY,
        "permit_id": ", ".join(major["file_numbers"]) or major["slug"],
        "source_url": MAP_URL,
        "enrichment_url": major["url"],
        "development_name": None,
        "address": major["title"].split(",")[0].strip(),
        "latitude": None,
        "longitude": None,
        "permit_type": _permit_type_from_files(major["file_numbers"]),
        "development_class": dev_class,
        "occupancy_types": _OCC_BY_CLASS.get(dev_class, []),
        "status": None,
        "number_of_stories": None, "units_total": None, "unit_mix": None,
        "rental_or_strata": None, "rental_subtype": None, "rental_mix": None,
        "parking_vehicle_stalls": None, "parking_bike_stalls": None, "parking_notes": None,
        "floor_area": None, "floor_area_unit": None, "zoning_density": None, "height_m": None,
        "raw_text": None,
        "extraction_method": "html",
        "milestones": [],
        "documents": [],
        "is_parsed": True,
        "needs_pdf_extraction": False,
    }
    _apply_major_fields(row, fields, major.get("override"))
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = True  # no map feature to corroborate, and no official status
    return row


def enrich_from_html(session, rows: list[dict], use_cache: bool = True) -> int:
    """Enrich major applications from their HTML subpages. Returns the count enriched or added.

    A subpage is matched to a map row by civic address first, then by a shared file number (some
    applications sit at a legal-description address like "Part of Lot 21, Concession 9" that no
    civic match can reach). A subpage that matches no map row at all is added as its own row, so a
    newly filed major application missing from the map layer is not lost.
    """
    majors = major_app_index(session, use_cache=use_cache)
    by_civic: dict[tuple, dict] = {}
    by_file: dict[str, dict] = {}
    for row in rows:
        ck = _civic_key(row.get("address"))
        if ck[0] is not None and ck[1]:
            by_civic.setdefault(ck, row)
        for fn in _file_numbers(row.get("permit_id")):
            by_file.setdefault(fn, row)

    enriched = 0
    for major in majors:
        target = by_civic.get(major["civic_key"]) if major["civic_key"][0] is not None else None
        if target is None:
            target = next((by_file[fn] for fn in major["file_numbers"] if fn in by_file), None)
        if target is not None:
            _apply_major_fields(target, major["fields"], major.get("override"))
            target["enrichment_url"] = major["url"]
            target["extraction_method"] = "arcgis+html"
            target["extraction_confidence"] = features.score_confidence(target)
            target["needs_review"] = target["extraction_confidence"] < 0.6
        else:
            rows.append(_standalone_row(major))
        enriched += 1
    return enriched


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    """GET url as text, backed by the shared TTL file cache."""
    import hashlib
    import os
    import time

    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(ag.CACHE_DIR, f"halton_hills_{key}.html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(ag.CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every Halton Hills application from the MapServer, enriching the major ones from HTML."""
    session = session or ag.build_session()
    feats = ag.query_all(session, APPLICATIONS_LAYER, "1=1", _OUT_FIELDS, "halton_hills",
                         return_geometry=True, use_cache=use_cache)
    rows = parse_features(feats)
    if limit:
        rows = rows[:limit]
    enrich_from_html(session, rows, use_cache=use_cache)
    # General rule: collapse duplicate records sharing one civic address (e.g. the Subdivision and
    # Site Plan both filed for "125 McDonald Blvd") into a single record before returning.
    consolidate.consolidate_by_address(rows)
    return rows
