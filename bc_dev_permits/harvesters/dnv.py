"""District of North Vancouver harvester - public JSON API (Simplicity CMS / dnv_search).

The DNV "significant development applications" page is a React SPA, but it is backed by a public
JSON API, which the harvester reads directly (no SPA rendering, and the data survives even when a
project's HTML detail page has been retired):

  .../significant-development-application/all              -> every application, keyed by case number
                                                             (a lighter summary used for the map)
  .../significant-development-application?caseNumber=<id>  -> the FULL record for one application
                                                             (floor area, parking, milestones, ...)

So the harvest lists the case numbers from /all and fetches each full record. The record is already
structured, so there is no HTML/prose parsing: fields map straight onto the schema, development_class
is derived from the floor-area-by-use figures, number_of_stories from the per-building table, and
latitude/longitude come from the API (no geocoding needed).

NOT the City of North Vancouver (that is `north_van`); this is the District (`dnv`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

import requests

from bc_dev_permits import config, consolidate, features

MUNICIPALITY = "dnv"
BASE = "https://www.dnv.org"
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL

# Floor-area-by-use field -> development_class contribution (townhouse rolls up to residential).
_USE_FIELDS = {
    "plPropResFlrSPcSum": "residential", "plPropTownFlrSPcSum": "residential",
    "plPropComFlrSPcSum": "commercial", "plPropInsFlrSPcSum": "institutional",
    "plPropIndFlrSPcSum": "industrial",
}
# Application milestones in process order; the furthest-along one with a date becomes the status.
_MILESTONES = [
    ("milestonesApplicationReceivedD", "Application Received"),
    ("milestonesEarlyInputD", "Early Input"),
    ("milestonesPublicInfoMeetingD", "Public Information Meeting"),
    ("milestonesSummaryLetterD", "Summary Letter"),
    ("milestonesBylawIntroD", "Bylaw Introduction"),
    ("milestonesPublicHearingD", "Public Hearing"),
    ("milestonesSecondThirdReadingD", "Second/Third Reading"),
    ("milestonesCouncilConsidPermitIssD", "Council Consideration / Permit Issuance"),
    ("milestonesBylawAdoptionD", "Bylaw Adoption"),
]


def _num(v) -> float | None:
    """Return a positive number, or None for null / 0 / non-numeric ('0' sums mean 'no area')."""
    try:
        n = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return n or None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _title_address(addr: str | None) -> str | None:
    """Clean an API address (' 1120  HAROLD RD' -> '1120 Harold Rd')."""
    if not addr:
        return None
    text = re.sub(r"\s+", " ", addr).strip().title()
    # Keep common directionals / ordinals readable.
    text = re.sub(r"\bRd\b", "Rd", text)
    return text or None


def _max_storeys(record: dict) -> int | None:
    """Return the tallest building's storey count from the 'Pl Building Proposed' custom table."""
    storeys = []
    for cell in record.get("planCustomFieldTableCells") or []:
        if cell.get("tableName") == "Pl Building Proposed" and cell.get("columnName") == "Storeys":
            n = _int(cell.get("value"))
            if n:
                storeys.append(n)
    return max(storeys) if storeys else None


def _unit_mix(record: dict) -> dict | None:
    """Aggregate the 'Pl Unit Info Proposed' table (Count by Tenure) into a unit mix.

    This explains cases where ``plunitcountSUM`` exceeds the prose "N homes" figure: the total
    counts every dwelling (e.g. 46 Market Housing - Ownership + 10 Lock-Off Suite = 56), while the
    prose counts only the primary homes.
    """
    rows: dict = {}
    for cell in record.get("planCustomFieldTableCells") or []:
        if cell.get("tableName") == "Pl Unit Info Proposed":
            rows.setdefault(cell.get("rowNumber"), {})[cell.get("columnName")] = cell.get("value")
    mix: dict[str, int] = {}
    for row in rows.values():
        tenure, count = row.get("Tenure"), _int(row.get("Count"))
        if tenure and count:
            mix[tenure] = mix.get(tenure, 0) + count
    return mix or None


def _milestones(record: dict) -> list[dict]:
    out = []
    for key, label in _MILESTONES:
        val = record.get(key)
        if val:
            out.append({"milestone": label, "milestone_date": str(val)[:10]})
    return out


def _status(record: dict) -> str | None:
    """Return the furthest-along milestone that has a date (in process order)."""
    label = None
    for key, name in _MILESTONES:
        if record.get(key):
            label = name
    return label


def _occupancies_and_class(record: dict) -> tuple[list[dict], str, list[str]]:
    """Derive occupancies + development_class from the floor-area-by-use figures."""
    by_use: dict[str, float] = {}
    for field, cls in _USE_FIELDS.items():
        area = _num(record.get(field))
        if area:
            by_use[cls] = by_use.get(cls, 0.0) + area
    occupancies = [{"occupancy": cls, "floor_area": round(area, 1), "floor_area_unit": "m2"}
                   for cls, area in by_use.items()]
    classes = list(by_use)
    if len(classes) > 1:
        dev_class = "mixed"
    elif classes:
        dev_class = classes[0]
    else:
        dev_class = "unknown"
    return occupancies, dev_class, (["residential", "commercial"] if dev_class == "mixed"
                                    else [dev_class] if dev_class != "unknown" else [])


def _tenure(unit_mix: dict | None, description: str | None) -> str | None:
    """Derive strata/rental tenure from the unit-mix tenures, else the description."""
    text = " ".join(unit_mix or {}).lower() + " " + (description or "").lower()
    if "rental" in text:
        return "rental"
    if "ownership" in text or "strata" in text:
        return "strata"
    return None


def _raw_text(record: dict) -> str | None:
    parts = [record.get("plWebDescriptionM")]
    dev = record.get("developer") or {}
    if dev.get("company"):
        parts.append(f"Developer: {dev['company']}")
    if record.get("neighbourhood"):
        parts.append(f"Neighbourhood: {record['neighbourhood']}")
    ocp = record.get("ocpDesignationExistingP") or []
    if ocp:
        parts.append("OCP designation: " + "; ".join(ocp))
    if record.get("zoningExistingS"):
        parts.append(f"Existing zoning: {record['zoningExistingS']}")
    return " \n".join(p for p in parts if p) or None


def parse_record(record: dict) -> dict | None:
    """Map one application's full API record to a dev_permit row."""
    permit_id = record.get("caseNumber")
    if not permit_id:
        return None
    addresses = [_title_address(a.get("address")) for a in record.get("planCaseAddresses") or []]
    addresses = [a for a in addresses if a]
    main = next((_title_address(a.get("address")) for a in record.get("planCaseAddresses") or []
                 if a.get("isMain")), addresses[0] if addresses else None)

    occupancies, dev_class, occ_types = _occupancies_and_class(record)
    unit_mix = _unit_mix(record)
    floor_area = _num(record.get("plpropTotalFlrSpcN"))
    lot_area = _num(record.get("siteAreaN"))
    zoning_density = f"FSR {round(floor_area / lot_area, 2)}" if floor_area and lot_area else None

    parking_notes = None
    res_p, vis_p = _int(record.get("plpropResParkSUM")), _int(record.get("plpropVisParkSUM"))
    if res_p or vis_p:
        parking_notes = "Vehicle parking: " + ", ".join(
            f"{n} {label}" for n, label in ((res_p, "residential"), (vis_p, "visitor")) if n)

    slug = record.get("plurlSlugT") or ""
    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": (BASE + slug) if slug.startswith("/") else config.DNV_LIST_URL,
        "development_name": record.get("prjProjectNameS") or None,
        "address": main,
        "addresses": [a for a in addresses if a != main] or None,
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "permit_type": record.get("planWorkClass") or None,
        "development_class": dev_class,
        "occupancy_types": occ_types,
        "occupancies": occupancies,
        "status": _status(record),
        "number_of_stories": _max_storeys(record),
        "units_total": _int(record.get("plunitcountSUM")),
        "unit_mix": unit_mix,
        "rental_or_strata": _tenure(unit_mix, record.get("plWebDescriptionM")),
        "rental_subtype": None,
        "rental_mix": None,
        "parking_vehicle_stalls": _int(record.get("plpropTotalParkSpcN")),
        "parking_bike_stalls": None,
        "parking_notes": parking_notes,
        "floor_area": round(floor_area, 1) if floor_area else None,
        "floor_area_unit": "m2" if floor_area else None,
        "lot_area": round(lot_area, 1) if lot_area else None,
        "lot_area_unit": "m2" if lot_area else None,
        "zoning_density": zoning_density,
        "height_m": None,
        "raw_text": _raw_text(record),
        "extraction_method": "api",
        "milestones": _milestones(record),
        "documents": [],
        "is_parsed": True,
        "needs_pdf_extraction": False,
    }
    # Drop the empty optional keys that other harvesters omit when unset.
    if row["addresses"] is None:
        del row["addresses"]
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6 or dev_class == "unknown"
    return row


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every DNV significant development application from the JSON API.

    Lists case numbers from the /all endpoint, fetches each full record, and collapses duplicate
    civic addresses before returning.
    """
    session = session or _session()
    catalogue = json.loads(_cached_get(session, config.DNV_API_ALL, use_cache))
    case_numbers = list(catalogue)
    if limit:
        case_numbers = case_numbers[:limit]
    rows = []
    for case in case_numbers:
        try:
            record = json.loads(_cached_get(session, config.DNV_API_ONE + case, use_cache))
        except (OSError, ValueError, requests.RequestException):
            continue
        row = parse_record(record)
        if row:
            rows.append(row)
    consolidate.consolidate_by_address(rows)
    return rows


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    return s


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    """GET url as text (JSON), backed by the shared TTL file cache."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"dnv_{key}.json")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text
