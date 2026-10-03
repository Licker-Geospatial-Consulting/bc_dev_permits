"""City of Maple Ridge harvester - VertiGIS Studio Web over an ArcGIS MapServer.

The "Land Development Map Application" is a VertiGIS Studio Web app, which is a UI over one
ArcGIS MapServer (see references/vertigis_webmaps.md). We skip the map and query the service
directly:

  layer 0  DevelopmentApplications  - one polygon per application (the dev_permit rows)
  table 2  ApplicationDetails       - key/value rows (InfoDescription/InfoValue), the
                                       "Application details" section, joined by FolderRSN
  table 1  Applicants               - personal names: NOT fetched (PII, no schema column)

There is no declared relationship between the layer and the detail table, so the join is by
the shared FolderRSN key: we page the applications, then bulk-fetch the details for the
harvested FolderRSNs and group them.

Attribute -> schema mapping (layer 0):
  ReferenceFile       -> permit_id (e.g. "2019-255-RZ")
  FolderName          -> address (the "<id> - <address>" suffix after the id)
  FolderDesc          -> permit_type (Rezoning / Development Permit / Subdivision / ...)
  Status              -> status (e.g. "Final Reading", "Pending Review")
  InDate (epoch ms)   -> "Application Received" milestone
  First/Second/Third ReadingDate, PublicHearingDate (YYYYMMDDHHMMSS) -> milestones
  IssueDate (epoch ms)-> "Permit Issued" milestone
  geometry (polygon)  -> latitude/longitude (ring centroid)

Development fields (units, zoning, class, rental) come deterministically from the detail
key/values; free-text prose is thin here, so there is no PDF/model fallback.
"""

from __future__ import annotations

import html
import re

from bc_dev_permits import config, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "maple_ridge"
APP_URL = config.MAPLE_RIDGE_APP_URL
SERVICE_URL = config.MAPLE_RIDGE_SERVICE_URL
APPLICATIONS_LAYER = f"{SERVICE_URL}/0"
DETAILS_TABLE = f"{SERVICE_URL}/2"

_APP_FIELDS = ("FolderRSN,ReferenceFile,FolderName,FolderType,FolderDesc,Status,StatusType,"
               "StatusDescription,ApplicationDescription,WorkProposed,Description,NumberLots,"
               "InDate,IssueDate,FirstReadingDate,SecondReadingDate,ThirdReadingDate,PublicHearingDate")


# Deterministic development_class from Maple Ridge's type / zoning / OCP descriptors.
_RESIDENTIAL = re.compile(r"residential|\bfamily\b|dwelling|townhouse|apartment|duplex|suite"
                          r"|\bR[SMT]-|\bRES\b|\bRMED\b|\bRLOW\b|\bRHI\b", re.IGNORECASE)
_COMMERCIAL = re.compile(r"commercial|retail|\bshops?\b|\bC-\d|business|office", re.IGNORECASE)
_INDUSTRIAL = re.compile(r"industrial|warehouse|\bM-\d", re.IGNORECASE)
_INSTITUTIONAL = re.compile(r"institutional|civic|\bschool\b|church|place of worship|\bP-\d", re.IGNORECASE)
# Permit kinds that describe land actions, not a building use: leave class unknown unless the
# zoning/OCP text carries an explicit use.
_NON_BUILDING = {"Tree Cutting Permit", "Soil Deposit Permit", "Agricultural Land Reserve"}


def _clean(value) -> str | None:
    """Decode HTML entities and collapse whitespace; '' -> None."""
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", html.unescape(str(value))).strip()
    return text or None


def _int(value) -> int | None:
    """First integer in a value like '106' or '12 units', else None."""
    m = re.search(r"-?\d+", str(value or ""))
    return int(m.group()) if m else None


def _reading_date(value) -> str | None:
    """'YYYYMMDDHHMMSS' -> 'YYYY-MM-DD', else None."""
    s = str(value or "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{14}", s) else None


def _development_class(work_proposed, details: dict) -> str:
    """Classify from the human-readable type / zoning / OCP descriptors (no fabrication)."""
    hint = " ".join(filter(None, [
        work_proposed,
        details.get("Rezoning Type"), details.get("Development Permit Type"),
        details.get("Application Type"), details.get("Property Type"),
        details.get("Subdivision Type"), details.get("Single Family"),
        details.get("Current Zoning"), details.get("Proposed Zoning"),
        details.get("Current OCP"), details.get("Proposed OCP"),
    ]))
    res, com = bool(_RESIDENTIAL.search(hint)), bool(_COMMERCIAL.search(hint))
    ind, inst = bool(_INDUSTRIAL.search(hint)), bool(_INSTITUTIONAL.search(hint))
    if res and com:
        return "mixed"
    if res:
        return "residential"
    if com:
        return "commercial"
    if ind:
        return "industrial"
    if inst:
        return "institutional"
    return "unknown"


def _units_total(details: dict) -> int | None:
    """Proposed lots/units count from the detail fields (the city's stated count)."""
    # "CD Residential Unit" is an explicit dwelling count; "Approx. Number of Proposed
    # Lots/Units" is the city's proposed lot-or-unit count (one dwelling per lot for the
    # single-detached subdivisions that dominate this source).
    return _int(details.get("CD Residential Unit")) or _int(details.get("Approx. Number of Proposed Lots/Units"))


def _zoning(details: dict) -> str | None:
    """Return the '<current> -> <proposed>' zoning descriptor, or whichever side is present."""
    cz, pz = _clean(details.get("Current Zoning")), _clean(details.get("Proposed Zoning"))
    if cz and pz and cz != pz:
        return f"{cz} -> {pz}"
    return pz or cz


def _address(folder_name, reference_file) -> str | None:
    """FolderName is '<ReferenceFile> - <address>'; return the address part."""
    name = _clean(folder_name)
    if name and reference_file and name.startswith(reference_file):
        return _clean(name[len(reference_file):].lstrip(" -")) or None
    return name


def _milestones(attrs: dict) -> list[dict]:
    pairs = [
        ("Application Received", ag.epoch_ms_to_date(attrs.get("InDate"))),
        ("First Reading", _reading_date(attrs.get("FirstReadingDate"))),
        ("Second Reading", _reading_date(attrs.get("SecondReadingDate"))),
        ("Third Reading", _reading_date(attrs.get("ThirdReadingDate"))),
        ("Public Hearing", _reading_date(attrs.get("PublicHearingDate"))),
        ("Permit Issued", ag.epoch_ms_to_date(attrs.get("IssueDate"))),
    ]
    return [{"milestone": name, "milestone_date": date} for name, date in pairs if date]


def _raw_text(attrs: dict, details: dict) -> str:
    """Readable, re-extractable record: the type/work line plus every detail key/value."""
    lines = [f"{_clean(attrs.get('FolderDesc')) or ''}: {_clean(attrs.get('WorkProposed')) or ''}".strip(": ")]
    app_desc = _clean(attrs.get("ApplicationDescription"))
    if app_desc:
        lines.append(app_desc)
    lines += [f"{_clean(k)}: {_clean(v)}" for k, v in details.items() if _clean(v)]
    return "\n".join(line for line in lines if line)


_OCC_BY_CLASS = {
    "residential": ["residential"],
    "commercial": ["commercial"],
    "industrial": ["industrial"],
    "institutional": ["institutional"],
    "mixed": ["residential", "commercial"],
    "unknown": [],
}


def _row_from_feature(feature: dict, details: dict) -> dict | None:
    """Map one application feature (+ its detail key/values) to a dev_permit row."""
    attrs = feature.get("attributes") or {}
    ref = (attrs.get("ReferenceFile") or "").strip()
    if not ref:
        return None
    lat, lon = ag.centroid(feature.get("geometry"))

    # The ApplicationDescription is free-text ("a 35-storey apartment building with 252 ...
    # units"), so parse it for the fields the structured key/values do not carry (storeys,
    # unit mix, parking, tenure). Structured detail values take precedence over the prose.
    prose = features.extract_all(_clean(attrs.get("ApplicationDescription")) or "")

    # A land-action permit (tree cutting, soil deposit, ALR) only gets a building class when
    # its zoning/OCP names an explicit use, so its WorkProposed label is dropped from the hint.
    work = None if _clean(attrs.get("FolderDesc")) in _NON_BUILDING else _clean(attrs.get("WorkProposed"))
    dev_class = _development_class(work, details)
    if dev_class == "unknown":
        dev_class = prose["development_class"]

    units = _units_total(details) or prose["units_total"]
    rental_units = _int(details.get("Number of Proposed Rental Units"))
    affordable = _int(details.get("Number of Proposed Affordable Rental Units"))
    rental = "rental" if rental_units else prose["rental_or_strata"]

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": ref,
        "source_url": APP_URL,
        "development_name": None,
        "address": _address(attrs.get("FolderName"), ref),
        "latitude": lat,
        "longitude": lon,
        "permit_type": _clean(attrs.get("FolderDesc")),
        "development_class": dev_class,
        "occupancy_types": _OCC_BY_CLASS[dev_class],
        "status": _clean(attrs.get("Status")),
        "number_of_stories": prose["number_of_stories"],
        "units_total": units,
        "unit_mix": prose["unit_mix"],
        "rental_or_strata": rental,
        "rental_subtype": prose["rental_subtype"],
        "rental_mix": {"affordable-rental": affordable} if affordable else prose["rental_mix"],
        "parking_vehicle_stalls": prose["parking_vehicle_stalls"],
        "parking_bike_stalls": prose["parking_bike_stalls"],
        "parking_notes": prose["parking_notes"],
        "floor_area": prose["floor_area"],
        "floor_area_unit": prose["floor_area_unit"],
        "zoning_density": _zoning(details) or prose["zoning_density"],
        "raw_text": _raw_text(attrs, details) or None,
        "extraction_method": "arcgis",
        "milestones": _milestones(attrs),
        "documents": [],
        "is_parsed": True,
        "needs_pdf_extraction": False,
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6 or dev_class == "unknown"
    return row


def group_details(detail_features: list[dict]) -> dict[int, dict]:
    """Group ApplicationDetails rows into {FolderRSN: {InfoDescription: InfoValue}}."""
    out: dict[int, dict] = {}
    for f in detail_features:
        a = f.get("attributes") or {}
        rsn = a.get("FolderRSN")
        if rsn is None:
            continue
        label = a.get("InfoDescription")
        if label:
            out.setdefault(rsn, {})[label] = a.get("InfoValue")
    return out


def parse_applications(app_features: list[dict], details_by_rsn: dict[int, dict]) -> list[dict]:
    """Map application features + grouped details into dev_permit rows (no network)."""
    rows = [_row_from_feature(f, details_by_rsn.get((f.get("attributes") or {}).get("FolderRSN"), {}))
            for f in app_features]
    return [r for r in rows if r]


def _fetch_details(session, rsns: list[int], use_cache: bool) -> dict[int, dict]:
    """Bulk-fetch ApplicationDetails for the given FolderRSNs, in IN-clause chunks."""
    feats: list[dict] = []
    # Keep each IN-clause small so the GET URL stays well under server length limits.
    chunk_size = 100
    for i in range(0, len(rsns), chunk_size):
        chunk = ",".join(str(r) for r in rsns[i:i + chunk_size])
        feats += ag.query_all(session, DETAILS_TABLE, f"FolderRSN IN ({chunk})",
                              "FolderRSN,InfoDescription,InfoValue", "maple_ridge_details",
                              use_cache=use_cache)
    return group_details(feats)


def harvest(session=None, limit: int | None = None, use_cache: bool = True, status: str = "active") -> list[dict]:
    """Harvest Maple Ridge development applications (default: active; status='all' for every one)."""
    session = session or ag.build_session()
    where = "StatusType='Active'" if status == "active" else "1=1"
    apps = ag.query_all(session, APPLICATIONS_LAYER, where, _APP_FIELDS, "maple_ridge_apps",
                        return_geometry=True, use_cache=use_cache)
    if limit:
        apps = apps[:limit]
    rsns = [a["attributes"]["FolderRSN"] for a in apps if (a.get("attributes") or {}).get("FolderRSN") is not None]
    details_by_rsn = _fetch_details(session, rsns, use_cache)
    return parse_applications(apps, details_by_rsn)
