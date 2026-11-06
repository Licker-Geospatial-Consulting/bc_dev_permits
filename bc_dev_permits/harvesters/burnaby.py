"""City of Burnaby harvester - one "Major Development Projects" PDF (ruled table).

Burnaby publishes its major rezoning applications as a single multi-page PDF, one ruled table row
per application. Unlike Richmond the table has grid lines, so pymupdf's find_tables() recovers the
columns directly; no positional parsing is needed. Column -> field:

  Type                      -> permit_type (the development type, e.g. "Multiple-Family Residential")
  Rezoning                  -> permit_id   (e.g. "REZ #22-25")
  Developer / Architect     -> kept in raw_text (applicant, no schema column)
  Address                   -> address
  Development               -> raw_text (the description prose, parsed by features.py)
  Residential Strata Units  -> strata unit count / mix
  Residential Rental Units  -> rental unit count / mix
  Status                    -> status + the "<stage> YYYY.MM.DD" milestone

Every program attribute (units, storeys, class, unit mix) is parsed from the Development prose plus
the two unit columns; development_class is taken from the Type keyword first, then the prose.
"""

from __future__ import annotations

import hashlib
import os
import re
import time

import requests

from bc_dev_permits import config, consolidate, features

MUNICIPALITY = "burnaby"
PDF_URL = config.BURNABY_PDF_URL
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL

# Column indices in the recovered table (header: Plan Areas | | Type | Rezoning | Developer |
# Address | Development | Residential Strata Units | Residential Rental Units | Status).
_COLS = {0: "plan_area", 2: "type", 3: "rezoning", 4: "developer", 5: "address",
         6: "development", 7: "strata", 8: "rental", 9: "status"}
_PERMIT_ID = re.compile(r"\b(?:REZ|PRJ|DP|BLD)\s*#?\s*[\d-]+", re.IGNORECASE)
# A status date "2019.02.11" (and the stage text before it).
_STATUS_DATE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
_PLACEHOLDER = re.compile(r"^\s*(?:n/?a|not available(?:\s+yet)?|tbd|-+)?\s*$", re.IGNORECASE)
# One "<count> <descriptor>" segment of a strata / rental cell, the descriptor running up to the
# next count or the end: "5 2-Bedroom Twnhouse 70 3-Bedroom Twnhouse" -> (5, "2-Bedroom Twnhouse"),
# (70, "3-Bedroom Twnhouse"). A count must be followed by a space, so an inline "6-Storey" or
# "3+ Bedroom" inside a descriptor is not mistaken for a new count.
_MIX_SEG = re.compile(r"(\d+)\s+(.+?)(?=\s+\d+\s|$)")
# An explicit project total in the Development prose ("A total of 622 residential dwelling units").
_DESC_TOTAL = re.compile(r"\btotal(?:ling)?\s+of\s+(?:approximately\s+)?([\d,]+)\s+[\w\s-]*?units?\b",
                         re.IGNORECASE)

# Development-type keyword -> development_class, checked in order (first match wins). Mixed and the
# office/commercial cases are tested before the broad residential keywords so a "...Residential
# Tower With Commercial Podium" classifies as mixed, not residential.
_CLASS_BY_TYPE = (
    (r"mixed|podium|commercial\W+(?:and\W+)?residential|residential\W+(?:and\W+)?commercial", "mixed"),
    (r"industrial", "industrial"),
    (r"office", "commercial"),
    (r"institution|community centre|memorial park|\bschool\b|civic|place of worship|church", "institutional"),
    (r"commercial|retail", "commercial"),
    (r"residential|housing|rental|multiple-?family|multi-?family|apartment|townhouse|"
     r"senior|supportive|ground-?oriented", "residential"),
)
_OCC_BY_CLASS = {
    "residential": ["residential"], "commercial": ["commercial"], "industrial": ["industrial"],
    "institutional": ["institutional"], "mixed": ["residential", "commercial"], "unknown": [],
}


def _clean(value) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()
    return text or None


def _is_placeholder(value: str | None) -> bool:
    return value is None or bool(_PLACEHOLDER.match(value))


def _norm_mix_key(desc: str) -> str | None:
    """Normalize a unit-mix descriptor to a stable key ('2-Bedroom Twnhouse' -> '2-bed townhouse')."""
    d = f" {desc.lower()} "
    d = re.sub(r"\([^)]*\)", " ", d)                      # drop "(below CMHC)" etc.
    d = re.sub(r"\bmkt\b", "market", d)
    d = re.sub(r"non[\s-]*market", "non-market", d)
    d = re.sub(r"twn?h(?:ou)?se", "townhouse", d)
    d = re.sub(r"(\d\+?)\s*-?\s*bedroom", r"\1-bed", d)   # "2-Bedroom"/"3+ Bedroom" -> "2-bed"/"3+-bed"
    d = re.sub(r"\bbedroom\b", "bed", d)
    d = re.sub(r"\bunits?\b", " ", d)
    d = re.sub(r"\s+", " ", d).strip()
    return d or None


def _mix_from_cell(cell: str | None) -> dict[str, int]:
    """Parse a strata / rental cell into a {descriptor: count} unit-mix breakdown.

    Handles both bedroom breakdowns ("5 2-Bedroom Twnhouse 70 3-Bedroom Twnhouse") and tenure
    breakdowns ("213 units Market Rental 91 units Non-Mkt Rental"); a bare total ("267 Units
    Strata") becomes {"strata": 267}.
    """
    if _is_placeholder(cell):
        return {}
    out: dict[str, int] = {}
    for n, desc in _MIX_SEG.findall(cell):
        key = _norm_mix_key(desc)
        if key:
            out[key] = out.get(key, 0) + int(n)
    return out


def _cell_total(cell: str | None) -> int | None:
    """Total residential units stated in a strata or rental cell (sum of its breakdown)."""
    total = sum(_mix_from_cell(cell).values())
    return total or None


def _desc_total(development: str | None) -> int | None:
    """Return an explicit project total stated in the Development prose ('total of 622 ... units')."""
    m = _DESC_TOTAL.search(development or "")
    return int(m.group(1).replace(",", "")) if m else None


def _development_class(type_text: str | None, prose_class: str) -> str:
    low = (type_text or "").lower()
    for pattern, cls in _CLASS_BY_TYPE:
        if re.search(pattern, low):
            return cls
    return prose_class


def _milestones(status: str | None) -> list[dict]:
    if not status:
        return []
    m = _STATUS_DATE.search(status)
    if not m:
        return []
    stage = status[:m.start()].strip(" .-") or "Status"
    y, mo, da = m.groups()
    return [{"milestone": stage, "milestone_date": f"{y}-{mo}-{da}"}]


def _row_from_record(rec: dict) -> dict | None:  # noqa: C901
    """Map one table row to a dev_permit row, parsing the program from the prose + unit columns."""
    permit_id = None
    if rec.get("rezoning"):
        m = _PERMIT_ID.search(rec["rezoning"])
        permit_id = _clean(m.group(0)) if m else _clean(rec["rezoning"])
    if not permit_id:
        return None

    development = _clean(rec.get("development")) or ""
    strata = _clean(rec.get("strata"))
    rental = _clean(rec.get("rental"))
    # Combine the description with the two unit columns so features sees the whole program.
    prose = " ".join(p for p in (
        development,
        f"Strata: {strata}" if not _is_placeholder(strata) else "",
        f"Rental: {rental}" if not _is_placeholder(rental) else "",
    ) if p).strip()
    parsed = features.extract_all(prose)

    dev_class = _development_class(rec.get("type"), parsed["development_class"])
    # Unit mix is the breakdown stated in BOTH unit columns (strata bedroom/type mix plus the rental
    # tenure/bedroom mix), with distinct keys so neither masks the other.
    strata_mix = _mix_from_cell(strata)
    rental_mix = _mix_from_cell(rental)
    unit_mix: dict[str, int] = {}
    for part in (strata_mix, rental_mix):
        for key, val in part.items():
            unit_mix[key] = unit_mix.get(key, 0) + val
    unit_mix = unit_mix or parsed["unit_mix"]

    # Units total: a total stated in the Development prose wins; otherwise the strata + rental column
    # totals are summed for a mixed strata+rental project (they count different units), falling back
    # to whichever single column is populated.
    strata_total = _cell_total(strata)
    rental_total = _cell_total(rental)
    if strata_total and rental_total:
        column_total: int | None = strata_total + rental_total
    else:
        column_total = strata_total or rental_total
    units_total = _desc_total(development) or column_total or parsed["units_total"]

    tenure = parsed["rental_or_strata"]
    if strata_total and rental_total:
        tenure = tenure or "mixed"
    elif rental_total:
        tenure = tenure or "rental"
    elif strata_total is not None or (strata and not _is_placeholder(strata)):
        tenure = tenure or "strata"

    raw_parts = [development]
    if not _is_placeholder(strata):
        raw_parts.append(f"Residential strata units: {strata}")
    if not _is_placeholder(rental):
        raw_parts.append(f"Residential rental units: {rental}")
    if _clean(rec.get("developer")):
        raw_parts.append(f"Developer/Architect: {_clean(rec['developer'])}")
    if _clean(rec.get("plan_area")):
        raw_parts.append(f"Plan area: {_clean(rec['plan_area'])}")

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": PDF_URL,
        "development_name": None,
        "address": _clean(rec.get("address")),
        "latitude": None,
        "longitude": None,
        "permit_type": _clean(rec.get("type")),
        "development_class": dev_class,
        "occupancy_types": _OCC_BY_CLASS.get(dev_class, parsed["occupancy_types"]),
        "occupancies": parsed["occupancies"],
        "status": _status_label(rec.get("status")),
        "number_of_stories": parsed["number_of_stories"],
        "units_total": units_total,
        "unit_mix": unit_mix or None,
        "rental_or_strata": tenure,
        "rental_subtype": parsed["rental_subtype"],
        "rental_mix": parsed["rental_mix"],
        "parking_vehicle_stalls": parsed["parking_vehicle_stalls"],
        "parking_bike_stalls": parsed["parking_bike_stalls"],
        "parking_notes": parsed["parking_notes"],
        "floor_area": parsed["floor_area"],
        "floor_area_unit": parsed["floor_area_unit"],
        "zoning_density": parsed["zoning_density"],
        "height_m": parsed["height_m"],
        "raw_text": " ".join(raw_parts).strip() or None,
        "extraction_method": "pdf",
        "milestones": _milestones(_clean(rec.get("status"))),
        "documents": [],
        "is_parsed": bool(development),
        "needs_pdf_extraction": False,
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6 or dev_class == "unknown"
    return row


def _status_label(status) -> str | None:
    """Return the status stage without its trailing date ('Third Reading 2024.06.24' -> 'Third Reading')."""
    s = _clean(status)
    if not s:
        return None
    m = _STATUS_DATE.search(s)
    return (s[:m.start()].strip(" .-") or s) if m else s


def parse_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse every page's ruled table into dev_permit rows (no network)."""
    import pymupdf  # optional dep, see pyproject [pdf]

    rows: list[dict] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            for table in page.find_tables().tables:
                for raw in table.extract():
                    cells = [_clean(c) for c in raw]
                    if len(cells) < 10:
                        continue
                    joined = " ".join(c or "" for c in cells).lower()
                    # Skip the title banner and the repeated column-header row.
                    if "city of burnaby" in joined or (
                            (cells[3] or "").lower() == "rezoning"):
                        continue
                    rec = {f: cells[i] for i, f in _COLS.items()}
                    if not (rec["type"] or rec["rezoning"] or rec["address"]):
                        continue
                    row = _row_from_record(rec)
                    if row:
                        rows.append(row)
    return rows


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, f"burnaby_{hashlib.sha256(url.encode()).hexdigest()[:16]}.pdf")


def _cached_get_bytes(session, url: str, use_cache: bool = True) -> bytes:
    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, "rb") as fh:
            return fh.read()
    content = session.get(url, timeout=config.HTTP_TIMEOUT).content
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return content


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every Burnaby major-development application from the single PDF.

    One physical site can file several rezonings over time (e.g. 1030 Sperling Avenue), so rows are
    collapsed by civic address before returning, per the general de-duplication rule.
    """
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", config.USER_AGENT)
    rows = parse_pdf(_cached_get_bytes(session, PDF_URL, use_cache))
    if limit:
        rows = rows[:limit]
    consolidate.consolidate_by_address(rows)
    return _dedup_permit_ids(rows)


def _dedup_permit_ids(rows: list[dict]) -> list[dict]:
    """Collapse rows that share a REZ number (a rezoning id is unique to one application).

    The PDF occasionally lists one application twice with differently punctuated addresses
    ("Ptn 3405 Willingdon Ave" vs "Portion of 3405 Willingdon Avenue"), which the civic-address
    dedup misses. The richer record (higher confidence) is kept and the other address is preserved
    on it.
    """
    def rank(r: dict) -> tuple:
        # Prefer the record stating more units (a prose total beats a single-column count), then
        # the higher-confidence parse.
        return (r.get("units_total") or 0, r.get("extraction_confidence") or 0)

    best: dict[str, dict] = {}
    for row in rows:
        pid = row["permit_id"]
        cur = best.get(pid)
        if cur is None or rank(row) > rank(cur):
            if cur is not None:
                _keep_address(row, cur)
            best[pid] = row
        else:
            _keep_address(cur, row)
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        pid = row["permit_id"]
        if pid not in seen and best[pid] is row:
            seen.add(pid)
            out.append(row)
    return out


def _keep_address(keeper: dict, dropped: dict) -> None:
    """Record a dropped duplicate's distinct address on the keeper's ``addresses`` list."""
    addr = dropped.get("address")
    if addr and addr != keeper.get("address"):
        addrs = keeper.setdefault("addresses", [])
        if addr not in addrs:
            addrs.append(addr)
