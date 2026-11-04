"""City of Richmond harvester - one multi-page "current development applications" PDF.

Richmond does not publish HTML detail pages or a map service. Its whole active list is a single
PDF (RICHMOND_PDF_URL), 18+ pages, one table row per application. The table has no ruling lines,
so find_tables() returns nothing and the rows are parsed positionally instead.

Column layout (PDF points, page width 792):
  Folder Number   x~52    -> permit_id (e.g. "26-023785")
  Site Address    x~112   -> address
  Folder Type     x~190   -> permit_type (Development Permit / Rezoning / Subdivision / ...)
  Status          x~257   -> status (In Circulation / Review / Staff Report / ...)
  APPLICATION_    x~301   -> "Application Accepted" milestone (date portion of "dd/mm/yyyy h:mm AM")
    ACCEPTANCE
  Description     x~398   -> raw_text, parsed into every program field by features.py
  City Staff      x~674   -> a staff member's name; the schema has no column for it and we do not
    Contact                  surface staff detail, so it is read only to validate row banding,
                             never stored (same decision as Halton Hills' P_CONTAC).

Row banding (the one subtlety): a permit's folder number sits on the BOTTOM line of its block, and
the Folder Type and Description wrap UPWARD above it. So a block spans ``(previous anchor, this
anchor]`` - every line from just below the previous folder number down to and including this one.
Folder-shaped tokens inside the Description prose ("RZ 21-930951") are excluded as anchors by
requiring an anchor to sit in the folder column (x < 90); they stay in the Description text.

Every attribute (units, storeys, class, parking, floor area) comes from the Description prose via
features.extract_all; the table columns supply only id / address / type / status / date.
"""

from __future__ import annotations

import hashlib
import os
import re
import time

import requests

from bc_dev_permits import config, consolidate, features

MUNICIPALITY = "richmond"
PDF_URL = config.RICHMOND_PDF_URL
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL

# Column left edges in PDF points. A word at x belongs to the last column whose edge it clears.
_COLS = (("folder", 52), ("address", 112), ("type", 190), ("status", 257),
         ("date", 301), ("desc", 398), ("contact", 674))
# A Richmond folder number: two-digit year, hyphen, six digits (e.g. "26-023785").
_FOLDER = re.compile(r"^\d{2}-\d{6}$")
# "28/09/2026 12:00 AM" -> capture the dd/mm/yyyy date; the midnight "12:00 AM" time is a
# date-only placeholder, so only the date is kept.
_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")

_OCC_BY_CLASS = {
    "residential": ["residential"], "commercial": ["commercial"], "industrial": ["industrial"],
    "institutional": ["institutional"], "mixed": ["residential", "commercial"], "unknown": [],
}


def _col_of(x: float) -> str:
    """Return the column name a word at horizontal position ``x`` falls in."""
    name = _COLS[0][0]
    for col_name, left in _COLS:
        if x >= left - 3:
            name = col_name
    return name


def _clean(value) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def parse_page(page) -> list[dict]:
    """Parse one PDF page's table into raw column dicts (folder/address/type/status/date/desc).

    ``page`` is a pymupdf Page. Each returned dict has the six stored columns as joined strings
    (plus ``contact``, read only to validate banding). No network, no field extraction.
    """
    words = page.get_text("words")  # (x0, y0, x1, y1, text, block, line, word)
    header_y = min((w[1] for w in words if w[4] == "Folder"), default=0.0)
    # Folder numbers anchor each row and sit on the block's bottom line; real anchors are in the
    # folder column, so a folder-shaped token in the Description prose is not mistaken for one.
    anchors = sorted((w for w in words if w[0] < 90 and _FOLDER.match(w[4])), key=lambda w: w[1])

    rows: list[dict] = []
    for i, anchor in enumerate(anchors):
        lo = anchors[i - 1][1] + 1 if i > 0 else header_y + 5
        hi = anchor[1] + 3
        cells: dict[str, list] = {name: [] for name, _ in _COLS}
        for w in words:
            if lo < w[1] <= hi:
                cells[_col_of(w[0])].append(w)
        record = {}
        for name, bucket in cells.items():
            ordered = sorted(bucket, key=lambda w: (round(w[1]), w[0]))
            record[name] = _clean(" ".join(w[4] for w in ordered))
        rows.append(record)
    return rows


def _milestones(record: dict) -> list[dict]:
    """Return the APPLICATION_ACCEPTANCE date as a single 'Application Accepted' milestone."""
    m = _DATE.search(record.get("date") or "")
    if not m:
        return []
    day, month, year = m.groups()
    return [{"milestone": "Application Accepted", "milestone_date": f"{year}-{month}-{day}"}]


def _row_from_record(record: dict) -> dict | None:
    """Map one parsed table row to a dev_permit row, parsing the Description into program fields."""
    permit_id = _clean(record.get("folder"))
    if not permit_id:
        return None
    desc = _clean(record.get("desc")) or ""
    prose = features.extract_all(desc)
    dev_class = prose["development_class"]

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": PDF_URL,
        "development_name": None,
        "address": _clean(record.get("address")),
        "latitude": None,
        "longitude": None,
        "permit_type": _clean(record.get("type")),
        "development_class": dev_class,
        "occupancy_types": _OCC_BY_CLASS.get(dev_class, prose["occupancy_types"]),
        "occupancies": prose["occupancies"],
        "status": _clean(record.get("status")),
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
        "raw_text": desc or None,
        "extraction_method": "pdf",
        "milestones": _milestones(record),
        "documents": [],
        "is_parsed": bool(desc),
        "needs_pdf_extraction": False,
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6 or dev_class == "unknown"
    return row


def parse_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse every page of the current-development PDF into dev_permit rows (no network)."""
    import pymupdf  # optional dep, see pyproject [pdf]

    rows: list[dict] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            for record in parse_page(page):
                row = _row_from_record(record)
                if row:
                    rows.append(row)
    return rows


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, f"richmond_{hashlib.sha256(url.encode()).hexdigest()[:16]}.pdf")


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
    """Harvest every Richmond application from the current-development PDF.

    One physical site often files several applications (a Rezoning and a Development Permit at
    5300 No 3 Rd; a Subdivision and a Servicing Agreement at 8220 Gilbert Rd), so the rows are
    collapsed by civic address before returning, per the general de-duplication rule.
    """
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", config.USER_AGENT)
    pdf_bytes = _cached_get_bytes(session, PDF_URL, use_cache)
    rows = parse_pdf(pdf_bytes)
    if limit:
        rows = rows[:limit]
    consolidate.consolidate_by_address(rows)
    return rows
