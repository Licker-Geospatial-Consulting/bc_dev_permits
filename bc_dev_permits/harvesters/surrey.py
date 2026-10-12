"""City of Surrey harvester - PDF-first (council planning reports), Claude-read for the detail.

Surrey has no HTML detail pages: each application is a council **planning report PDF** listed on the
planning-reports page (a paginated Drupal view). So the attributes come from the PDF, not a web page.

Flow:  planning-reports list (permit id, address, status, dates, report PDF)  ->  dev_permit rows.

Every report PDF opens with a structured header - "Application No.", "PROPOSAL:" (a prose summary
plus bullet-point application types), "LOCATION:". That PROPOSAL summary carries the core program
("a 24-storey residential building with 230 rental units", "a 3,922 square metre industrial
building") and is parsed DETERMINISTICALLY with bc_dev_permits.features from the PDF's own text (no
model). The deeper pages (development data sheet: parking, unit mix, FSR) are read by **Claude
in-session, not Ollama** and applied with `apply_claude_pdf` (shared), which keeps the deterministic
values, stamps the PDF fields `pdf_claude`, and logs any disagreement to `row['conflicts']`.

Public API:
    get_report_rows(list_html, base)        -> list[dict]  (permit_id, address, status, dates, pdf)
    pdf_proposal(pdf_bytes)                 -> dict         (description, permit_types, zoning, text)
    parse_report(meta, proposal)            -> dict         (a dev_permit row)
    select_pdf_sources(row)                 -> list[dict]   (the report PDF, for the Claude read)
    apply_claude_pdf(row, fields, document) -> int
    harvest(limit=None, use_cache=True)     -> list[dict]
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import pdf_enrich

LIST_URL = config.SURREY_LIST_URL
BASE = "https://www.surrey.ca"
MUNICIPALITY = "surrey"
MIN_PROSE_CHARS = 20
MAX_PAGES = 30  # safety cap on list pagination
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Surrey application number, e.g. "7926-0234-00"; it is the PDF file name's stem (PLR_<id>.pdf).
_APP_NO = re.compile(r"\b\d{4}-\d{4}-\d{2}\b")
# Canonical application types Surrey lists as bullets under PROPOSAL.
_PERMIT_TYPES = (
    ("Rezoning", r"rezoning|land use contract"),
    ("OCP Amendment", r"official community plan|ocp amendment"),
    ("Development Permit", r"development permit"),
    ("Development Variance Permit", r"development variance permit"),
    ("Temporary Use Permit", r"temporary use permit"),
    ("Subdivision", r"subdivision"),
    ("Housing Agreement", r"housing agreement"),
)
apply_claude_pdf = pdf_enrich.apply_claude_pdf


# --------------------------------------------------------------------------- #
# 1. List scraping (permit id, address, status, dates, report PDF)
# --------------------------------------------------------------------------- #
def get_report_rows(list_html: str, base: str = LIST_URL) -> list[dict]:
    """Return one entry per planning report on a list page: id, address, status, dates, pdf url."""
    soup = BeautifulSoup(list_html, "lxml")
    out = []
    for row in soup.select("div.views-row"):
        link = row.select_one("a[href$='.pdf'], a[href*='.pdf?']")
        if not link:
            continue
        pdf_url = urljoin(base, link["href"])
        body = row.select_one(".listing-view__item-body") or row
        h4 = body.find(["h4", "h3", "h2"])
        app = _APP_NO.search(h4.get_text(" ", strip=True) if h4 else "") or _APP_NO.search(pdf_url)
        if not app:
            continue
        # The address and the application date are the bare text nodes between the <h4> and the
        # status <span>; the <time> is the council meeting date.
        texts = [t.strip() for t in (h4.find_next_siblings(string=True) if h4 else [])
                 if t.strip()] if h4 else []
        address = texts[0] if texts else None
        status = (body.select_one("span.tag") or body.select_one(".tag"))
        meeting = body.find("time")
        out.append({
            "permit_id": app.group(0),
            "address": _clean(address),
            "status": status.get_text(" ", strip=True) if status else None,
            "meeting_date": meeting.get("datetime", "")[:10] if meeting else None,
            "report_date": _clean(texts[1]) if len(texts) > 1 else None,
            "pdf_url": pdf_url,
        })
    return out


# --------------------------------------------------------------------------- #
# 2. PDF page-1 PROPOSAL (parsed deterministically from the report's own text)
# --------------------------------------------------------------------------- #
def pdf_proposal(pdf_bytes: bytes) -> dict:
    """Return {description, permit_types, zoning, text} from a report PDF's page-1 header."""
    import pymupdf  # optional dep, see pyproject [pdf]

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page1 = doc[0].get_text() if doc.page_count else ""
    m = re.search(r"PROPOSAL:\s*(.+?)\s*(?:LOCATION:|OWNER:|APPLICANT:|ZONING:)",
                  page1, re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    # Application types are the bullet labels; the report format varies (prose then bullet types, or
    # a bullet type then its prose), so the types are detected anywhere in the block and the whole
    # block - bullet markers removed - is the description that features.py then parses.
    permit_types = [label for label, kw in _PERMIT_TYPES if re.search(kw, raw, re.IGNORECASE)]
    # Detect a rezoning's "from <current> to <proposed>" per bullet, so the proposed zone stops
    # at the bullet boundary rather than running into the next application type.
    zoning = None
    for bullet in re.split(r"[•·▪]", raw):
        zm = re.search(r"from\s+(.+?)\s+to\s+([A-Z0-9][A-Z0-9()\-/ ]*?)\s*(?:$|[,.])",
                       bullet.strip(), re.IGNORECASE)
        if zm:
            zoning = f"{zm.group(1).strip()} -> {zm.group(2).strip()}"
            break
    description = re.sub(r"[•·▪]", " ", raw).strip()
    description = re.sub(r"\s{2,}", " ", description)
    return {"description": description, "permit_types": permit_types, "zoning": zoning,
            "text": page1}


# Development classes whose reports carry a parking table worth reading.
_PARKING_CLASSES = {"residential", "mixed", "commercial", "industrial", "institutional"}


def _xc(word) -> float:
    return (word[0] + word[2]) / 2


def _proposed_x(words) -> float | None:
    """The x-centre of the table's "Proposed" column header (paired with "Required" on its row)."""
    for req in (w for w in words if w[4].lower() == "required"):
        for prop in (w for w in words if w[4].lower() == "proposed"):
            if abs(req[1] - prop[1]) < 4 and _xc(prop) > _xc(req):
                return _xc(prop)
    return None


def _proposed_value(words, anchor, xprop: float) -> int | None:
    """The number on ``anchor``'s row that sits under the Proposed column."""
    cells = [(_xc(w), int(w[4].replace(",", "").replace("*", "")))
             for w in words if abs(w[1] - anchor[1]) < 5 and re.fullmatch(r"\d[\d,]*\*?", w[4])]
    return min(cells, key=lambda c: abs(c[0] - xprop))[1] if cells else None


def pdf_parking(pdf_bytes: bytes) -> tuple[int | None, int | None]:
    """Return (vehicle total, secure bicycle) from a report's "Number of Stalls" table.

    A high-PRECISION positional read of the Proposed column: the table's "Required"/"Proposed"
    header fixes the Proposed column's x, the "Total:" row's value under it is the vehicle total, and
    the "Residential Secure Parking" row's value under it is the long-term bicycle count. When the
    layout is ambiguous (no clean header pair, more than one "Total" row, nothing under the Proposed
    column) None is returned rather than a guessed number.
    """
    import pymupdf  # optional dep, see pyproject [pdf]

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    for i in range(min(doc.page_count, 25)):  # the parking table sits in the report body, never deep
        page = doc[i]
        if "number of stalls" not in page.get_text().lower():
            continue
        words = page.get_text("words")  # (x0, y0, x1, y1, word, ...)
        xprop = _proposed_x(words)
        if xprop is None:
            continue
        totals = [w for w in words if w[4].lower().rstrip(":") == "total"]
        vehicle = _proposed_value(words, totals[0], xprop) if len(totals) == 1 else None
        secure = next((w for w in words if w[4].lower() == "secure"), None)
        return vehicle, (_proposed_value(words, secure, xprop) if secure else None)
    return None, None


def parse_report(meta: dict, proposal: dict) -> dict:
    """Build a dev_permit row from a report's list metadata + its parsed PROPOSAL."""
    desc = proposal.get("description") or ""
    prose = features.extract_all(desc if len(desc) >= MIN_PROSE_CHARS else "")
    row = {
        "municipality": MUNICIPALITY,
        "permit_id": meta["permit_id"],
        "source_url": meta["pdf_url"],
        "development_name": None,
        "address": meta.get("address"),
        "permit_type": ", ".join(proposal.get("permit_types") or []) or None,
        "status": meta.get("status"),
        "raw_text": desc or None,
        "extraction_method": "pdf_text",
        "milestones": _milestones(meta),
        "documents": [{"url": meta["pdf_url"], "title": f"Planning Report {meta['permit_id']}",
                       "doc_role": "planning_report"}],
        "is_parsed": bool(desc),
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }
    if desc:
        row.update({k: v for k, v in prose.items() if k != "permit_type"})  # keep bullet permit_type
        if proposal.get("zoning") and not features.has_value(row.get("zoning_density")):
            row["zoning_density"] = proposal["zoning"]
        row["is_parsed"] = True
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True
    # The report's data-sheet pages (parking, unit mix, FSR) are a Claude-read candidate whenever the
    # summary is low-signal or missing those richer fields.
    row["needs_pdf_extraction"] = pdf_enrich.needs_pdf(row) or any(
        not features.has_value(row.get(f)) for f in ("parking_vehicle_stalls", "unit_mix"))
    return row


def select_pdf_sources(row: dict) -> list[dict]:
    """Return the report PDF as the one document to Claude-read for the deeper data sheet."""
    return list(row.get("documents") or [])


# --------------------------------------------------------------------------- #
# 3. Live harvest (list is plain HTTP; each report PDF's text is parsed deterministically)
# --------------------------------------------------------------------------- #
def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest the planning-reports list + each report PDF's PROPOSAL into dev_permit rows."""
    session = session or _session()
    metas: list[dict] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        html = _cached_get(session, f"{LIST_URL}?page={page}", use_cache)
        rows = [r for r in get_report_rows(html) if r["permit_id"] not in seen]
        if not rows:
            break
        seen.update(r["permit_id"] for r in rows)
        metas.extend(rows)
        if limit and len(metas) >= limit:
            break
    if limit:
        metas = metas[:limit]

    out = []
    for meta in metas:
        pdf_bytes = None
        try:
            pdf_bytes = _cached_get_bytes(session, meta["pdf_url"], use_cache)
            proposal = pdf_proposal(pdf_bytes)
        except (OSError, ValueError, RuntimeError):
            proposal = {"description": "", "permit_types": [], "zoning": None, "text": ""}
        row = parse_report(meta, proposal)
        # For a development report, read the parking table deterministically from the PDF (the
        # "Number of Stalls" Proposed column). It is high-precision: a value is set only when the
        # table is unambiguous, otherwise the row stays a parking Claude-read / review candidate.
        if pdf_bytes and (features.has_value(row.get("units_total"))
                          or row.get("development_class") in _PARKING_CLASSES):
            try:
                vehicle, bike = pdf_parking(pdf_bytes)
            except (OSError, ValueError, RuntimeError):
                vehicle = bike = None
            if vehicle is not None:
                row["parking_vehicle_stalls"] = vehicle
            if bike is not None:
                row["parking_bike_stalls"] = bike
            row["needs_pdf_extraction"] = pdf_enrich.needs_pdf(row) or not features.has_value(
                row.get("parking_vehicle_stalls"))
        out.append(row)
    consolidate.consolidate_by_address(out)
    return out


# --------------------------------------------------------------------------- #
# HTTP + disk cache
# --------------------------------------------------------------------------- #
def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _cache_path(url: str, ext: str) -> str:
    return os.path.join(CACHE_DIR, f"surrey_{hashlib.sha256(url.encode()).hexdigest()[:16]}.{ext}")


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    path = _cache_path(url, "html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def _cached_get_bytes(session, url: str, use_cache: bool = True) -> bytes:
    path = _cache_path(url, "pdf")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, "rb") as fh:
            return fh.read()
    content = session.get(url, timeout=config.HTTP_TIMEOUT).content
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return content


def _clean(value) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _milestones(meta: dict) -> list[dict]:
    out = []
    if meta.get("report_date"):
        out.append({"milestone": "Planning Report", "milestone_date": _iso_date(meta["report_date"])})
    if meta.get("meeting_date"):
        out.append({"milestone": "Council Meeting", "milestone_date": meta["meeting_date"]})
    return out


def _iso_date(s: str):
    from datetime import datetime
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
