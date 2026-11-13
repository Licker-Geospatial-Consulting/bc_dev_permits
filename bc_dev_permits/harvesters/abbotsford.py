"""City of Abbotsford harvester - SSRS "Instream Development Applications" report.

``services.abbotsford.ca/OnlineReporting/InstreamDevelopmentApplications.aspx`` is a SQL Server
Reporting Services (SSRS) ReportViewer. The visible table paginates over 9 pages, but the report
*executes on page load* and SSRS publishes the whole result set through its export endpoint, so the
harvester reads one XML export instead of scraping / paging the HTML (no Playwright needed).

Flow:
  GET the .aspx (sets a fresh ReportSession cookie) -> read ``ExportUrlBase`` out of the page ->
  GET ``<host><ExportUrlBase>XML`` with the same session -> one ``<Detail>`` element per application.

Columns -> fields (the report's own column set):
  PRJ_NUMBER1                              -> permit_id (e.g. PRJ24-024)
  PROPERTIES                               -> address ("NULL CIVIC N/A" -> no address)
  DESCRIPTION                              -> raw_text; permit_type + storeys / zoning via features.py
  APP_RECEIVED_DATE                        -> "Application Received" milestone
  KEY_DATES                                -> milestones (one "Label: Mon DD, YYYY" per line)
  COMMERCIAL / INDUSTRIAL / INSTITUTIONAL  -> per-use floor area (m2) -> occupancies + floor_area + class
  NEWSFLOTS / TOWNHOUSE / APARTMENT        -> residential counts -> unit_mix + units_total + class
  PEOPLE_NAME1                             -> applicant, kept in raw_text only

PEOPLE_NAME1 is the applicant (usually a firm); EMAIL and PHONE1 are personal contact details and are
deliberately NOT stored. The report carries no per-row status column, so ``status`` is left NULL.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
import time
from xml.etree import ElementTree as ET

from bc_dev_permits import config, consolidate, features

MUNICIPALITY = "abbotsford"
HOST = config.ABBOTSFORD_HOST
REPORT_URL = config.ABBOTSFORD_REPORT_URL
CACHE_DIR = str(config.HTTP_CACHE_DIR)

# Area columns (m2) and residential-count columns -> canonical use / unit-type label.
_AREA_USES = (("COMMERCIAL", "commercial"), ("INDUSTRIAL", "industrial"),
              ("INSTITUTIONAL", "institutional"))
_COUNT_USES = (("NEWSFLOTS", "single-family-lot"), ("TOWNHOUSE", "townhouse"),
               ("APARTMENT", "apartment"))

# Application types named in the DESCRIPTION prose, best-first; joined into permit_type.
_PERMIT_MARKERS = (
    (re.compile(r"\brezon", re.IGNORECASE), "Rezoning"),
    (re.compile(r"official community plan|\bocp\b", re.IGNORECASE), "OCP Amendment"),
    (re.compile(r"development variance permit|\bdvp\b", re.IGNORECASE), "Development Variance Permit"),
    (re.compile(r"development permit|\bdp\b", re.IGNORECASE), "Development Permit"),
    (re.compile(r"\bsubdivi", re.IGNORECASE), "Subdivision"),
    (re.compile(r"\bform p\b", re.IGNORECASE), "Form P"),
    (re.compile(r"temporary use permit|\btup\b", re.IGNORECASE), "Temporary Use Permit"),
)
# Address placeholders the report uses for "no civic address".
_ADDR_PLACEHOLDER = {"", "null civic n/a", "n/a", "null", "tbd", "-"}
# "Label: Mon DD, YYYY" line inside KEY_DATES.
_KEY_DATE_LINE = re.compile(r"^(.*?):\s*([A-Za-z]{3}\.? \d{1,2},? \d{4})\s*$")


# --------------------------------------------------------------------------- #
# Small value helpers
# --------------------------------------------------------------------------- #
def _clean(value) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"[ \t]+", " ", str(value)).strip()
    return text or None


def _num(value) -> float | None:
    """Parse a report area value ('450.10', '934.43921499') to a rounded float, 0 / blank -> None."""
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return round(n, 2) if n else None


def _int(value) -> int | None:
    """Parse a report count value ('14') to an int, 0 / blank -> None."""
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return n or None


def _address(value) -> str | None:
    text = _clean(value)
    if not text or text.lower() in _ADDR_PLACEHOLDER:
        return None
    return text.title()


def _permit_types(desc: str | None) -> str | None:
    """Join the application types named in the description ('Rezoning, Subdivision')."""
    found: list[str] = []
    for rx, label in _PERMIT_MARKERS:
        if rx.search(desc or "") and label not in found:
            found.append(label)
    return ", ".join(found) or None


def _parse_date(value: str, fmt: str) -> str | None:
    try:
        return datetime.strptime(value.strip(), fmt).date().isoformat()
    except (TypeError, ValueError):
        return None


def _milestones(app_received: str | None, key_dates: str | None) -> list[dict]:
    """Build milestone rows from the Application Received date + the KEY_DATES block."""
    out = []
    received = _parse_date(app_received or "", "%m/%d/%y")
    if received:
        out.append({"milestone": "Application Received", "milestone_date": received})
    for line in re.split(r"[\r\n]+", key_dates or ""):
        m = _KEY_DATE_LINE.match(line.strip())
        if not m:
            continue
        date = _parse_date(m.group(2).replace(".", ""), "%b %d, %Y")
        if date:
            out.append({"milestone": _clean(m.group(1)), "milestone_date": date})
    return out


# --------------------------------------------------------------------------- #
# Structured metric columns -> schema fields
# --------------------------------------------------------------------------- #
def _metrics(attrib: dict) -> tuple[dict, dict]:
    """Return (nonzero per-use areas in m2, nonzero residential counts) from the report columns."""
    areas = {use: _num(attrib.get(col)) for col, use in _AREA_USES}
    counts = {label: _int(attrib.get(col)) for col, label in _COUNT_USES}
    return {u: v for u, v in areas.items() if v}, {k: v for k, v in counts.items() if v}


def _units_summary(counts: dict) -> str:
    """Human summary of the residential counts for the occupancy detail / raw_text."""
    label = {"single-family-lot": "new single-family lots", "townhouse": "townhouse units",
             "apartment": "apartment units"}
    return "; ".join(f"{v} {label[k]}" for k, v in counts.items())


def _structured(attrib: dict) -> dict:
    """Map the six quantitative columns to class / occupancies / units / floor_area."""
    areas, counts = _metrics(attrib)
    occ_types = list(areas)
    if counts:
        occ_types.append("residential")
    occupancies = [{"occupancy": u, "floor_area": v, "floor_area_unit": "m2"} for u, v in areas.items()]
    if counts:
        occupancies.append({"occupancy": "residential", "detail": _units_summary(counts)})
    distinct = set(occ_types)
    dev_class = "mixed" if len(distinct) > 1 else (next(iter(distinct)) if distinct else None)
    # units_total = built dwelling units (townhouse + apartment); subdivision LOTS are not dwellings,
    # so they stay in unit_mix only.
    units = (counts.get("townhouse", 0) + counts.get("apartment", 0)) or None
    return {
        "development_class": dev_class,
        "occupancy_types": occ_types,
        "occupancies": occupancies,
        "units_total": units,
        "unit_mix": counts or None,
        "floor_area": round(sum(areas.values()), 2) if areas else None,
        "floor_area_unit": "m2" if areas else None,
        "has_metrics": bool(occ_types),
        "counts": counts,
    }


# --------------------------------------------------------------------------- #
# Row builder
# --------------------------------------------------------------------------- #
def parse_detail(attrib: dict) -> dict | None:
    """Map one SSRS <Detail> element's attributes to a dev_permit row."""
    permit_id = _clean(attrib.get("PRJ_NUMBER1"))
    if not permit_id:
        return None
    desc = _clean(attrib.get("DESCRIPTION"))
    feats = features.extract_all(desc or "")
    s = _structured(attrib)

    # Class / occupancies: the structured columns are authoritative; fall back to the description
    # only when the row states no quantitative metrics at all (rezoning-only, Form P, ...).
    dev_class = s["development_class"]
    if not dev_class:
        dev_class = feats["development_class"] if feats["development_class"] != "unknown" else "unknown"
    occ_types = s["occupancy_types"] or feats["occupancy_types"]
    occupancies = s["occupancies"] or feats["occupancies"]

    applicant = _clean(attrib.get("PEOPLE_NAME1"))
    raw = _raw_text(desc, applicant, s["counts"], _metrics(attrib)[0])

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": REPORT_URL,
        "development_name": None,
        "address": _address(attrib.get("PROPERTIES")),
        "permit_type": _permit_types(desc) or feats["permit_type"],
        "development_class": dev_class,
        "occupancy_types": occ_types,
        "occupancies": occupancies,
        "status": None,  # the report carries no per-row status column
        # Residential counts come only from the structured columns (never the prose, so "into 14 lots"
        # is not misread as 14 dwellings); storeys / height / zoning are description signals.
        "units_total": s["units_total"],
        "unit_mix": s["unit_mix"],
        "number_of_stories": feats["number_of_stories"],
        "height_m": feats["height_m"],
        "floor_area": s["floor_area"],
        "floor_area_unit": s["floor_area_unit"],
        "zoning_density": feats["zoning_density"],
        "parking_vehicle_stalls": feats["parking_vehicle_stalls"],
        "parking_notes": feats["parking_notes"],
        "raw_text": raw,
        "milestones": _milestones(attrib.get("APP_RECEIVED_DATE"), attrib.get("KEY_DATES")),
        "documents": [],
        "extraction_method": "html",
        "is_parsed": bool(desc or s["has_metrics"]),
        "needs_pdf_extraction": False,
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = (row["extraction_confidence"] < 0.6
                           or row["development_class"] in (None, "unknown"))
    return row


def _raw_text(desc: str | None, applicant: str | None, counts: dict, areas: dict) -> str | None:
    """Keep the description, applicant, and a metric summary for re-extraction / provenance."""
    parts = []
    if desc:
        parts.append(desc)
    if applicant:
        parts.append(f"Applicant: {applicant}")
    metrics = []
    for use, area in areas.items():
        metrics.append(f"{use} floor area {area:g} m2")
    if counts:
        metrics.append(_units_summary(counts))
    if metrics:
        parts.append("Development: " + "; ".join(metrics))
    return "\n\n".join(parts) or None


# --------------------------------------------------------------------------- #
# Fetch (SSRS export) + harvest
# --------------------------------------------------------------------------- #
def _session():
    import requests
    s = requests.Session()
    s.headers.setdefault("User-Agent", config.USER_AGENT)
    return s


def _fetch_xml(session, use_cache: bool = True) -> str:
    """Return the report's XML export, backed by the shared TTL file cache.

    The export URL carries a per-request ReportSession, so we read ``ExportUrlBase`` from a fresh
    page load and fetch the export over the same session, then cache the resulting XML by TTL.
    """
    key = hashlib.sha256(REPORT_URL.encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"abbotsford_{key}.xml")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    page = session.get(REPORT_URL, timeout=config.HTTP_TIMEOUT).text
    m = re.search(r'"ExportUrlBase":"([^"]+)"', page)
    if not m:
        raise OSError("Abbotsford: ExportUrlBase not found (report page layout changed)")
    export = m.group(1).replace("\\/", "/").replace("\\u0026", "&")
    xml = session.get(HOST + export + "XML", timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    time.sleep(0.3)  # politeness
    return xml


def parse_export(xml: str) -> list[dict]:
    """Parse every <Detail> element in the SSRS XML export into dev_permit rows."""
    # Source is the City of Abbotsford's own SSRS endpoint (a fixed government host), not user input;
    # stdlib ElementTree is sufficient and no extra dependency (defusedxml) is pulled in.
    root = ET.fromstring(xml)  # noqa: S314
    ns_match = re.match(r"\{(.+)\}", root.tag)
    detail = f"{{{ns_match.group(1)}}}Detail" if ns_match else "Detail"
    rows = []
    for el in root.iter(detail):
        row = parse_detail(el.attrib)
        if row:
            rows.append(row)
    return rows


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every in-stream development application from the Abbotsford SSRS XML export."""
    session = session or _session()
    rows = parse_export(_fetch_xml(session, use_cache))
    if limit:
        rows = rows[:limit]
    consolidate.consolidate_by_address(rows)
    return rows
