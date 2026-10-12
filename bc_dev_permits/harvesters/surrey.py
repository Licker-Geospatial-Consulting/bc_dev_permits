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


def _proposed_value(words, anchor, xprop: float, band: float = 1e9) -> int | None:
    """Read the number on ``anchor``'s row that sits under the Proposed column.

    ``band`` caps how far the chosen cell may sit from the Proposed header x: a small band keeps a
    blank Proposed cell from picking up the Required column's number on the same row.
    """
    cells = [(_xc(w), int(w[4].replace(",", "").replace("*", "")))
             for w in words if abs(w[1] - anchor[1]) < 5 and re.fullmatch(r"\d[\d,]*\*?", w[4])]
    if not cells:
        return None
    x, v = min(cells, key=lambda c: abs(c[0] - xprop))
    return v if abs(x - xprop) <= band else None


def _component_sum(words, ny: float, ty: float, xprop: float) -> int | None:
    """Sum the Proposed component rows between the header and an untotalled "Total:" row.

    Some reports leave the "Total:" row blank and list the parts (commercial / residential /
    visitor) above it, so the vehicle total is their sum. Rows below "Total:" (tandem, accessible)
    are subsets and excluded. The column band keeps a part's Required number out of the sum.
    """
    row_ys: list[float] = []
    for y in sorted(w[1] for w in words if ny + 2 < w[1] < ty - 2):
        if not row_ys or y - row_ys[-1] > 5:
            row_ys.append(y)
    parts = [_proposed_value(words, (0, y, 0, y, ""), xprop, band=90) for y in row_ys]
    parts = [p for p in parts if p is not None]
    return sum(parts) if parts else None


def _proposed_columns(words, y0: float, y1: float) -> list[float]:
    """Return the x-centres of the distinct "Proposed" column headers between y0 and y1.

    A per-lot parking table repeats "Proposed" once per lot, so more than one centre means the
    single-lot figures are ambiguous and the caller should decline the read.
    """
    xs = sorted(_xc(p) for p in words if p[4].lower() == "proposed" and y0 < p[1] < y1)
    cols: list[float] = []
    for x in xs:
        if not cols or x - cols[-1] > 40:
            cols.append(x)
    return cols


def _parking_from_words(words) -> tuple[int | None, int | None]:
    """Read (vehicle, secure bicycle) from a page's words, scoped to the "Number of Stalls" table.

    A staff-report page often holds several Required/Proposed tables (density, setbacks, parking), so
    the read is anchored to the "Number of Stalls" row: the Proposed header just above it fixes the
    column, the single "Total" row between there and the "Bicycle" sub-header is the vehicle total,
    and the "Secure" row below is the long-term bicycle count. Ambiguity returns None, not a guess.
    """
    nos = next((w for w in words if w[4].lower() == "stalls"
                and any(o[4].lower() == "number" and abs(o[1] - w[1]) < 5 and o[0] < w[0]
                        for o in words)), None)
    if nos is None:
        return None, None
    ny = nos[1]
    # The Required/Proposed header sits just above "Number of Stalls"; a per-lot table repeats the
    # Proposed column, so require exactly one and that it be paired with a Required column.
    cols = _proposed_columns(words, ny - 45, ny)
    paired = [x for x in cols if any(r[4].lower() == "required" and _xc(r) < x for r in words
                                     if ny - 45 < r[1] < ny)]
    if len(paired) != 1:
        return None, None
    xprop = paired[0]
    bike_y = min((w[1] for w in words if w[4].lower() == "bicycle" and w[1] > ny), default=1e9)
    totals = [w for w in words if w[4].lower().rstrip(":") == "total" and ny < w[1] < bike_y]
    vehicle = None
    if len(totals) == 1:
        # The tabulated Total, or - when that row is left blank - the sum of the parts above it.
        vehicle = _proposed_value(words, totals[0], xprop) or _component_sum(words, ny, totals[0][1], xprop)
    secure = next((w for w in words if w[4].lower() == "secure" and w[1] > ny), None)
    return vehicle, (_proposed_value(words, secure, xprop) if secure else None)


def _part5_header_y(words) -> float | None:
    """Return the y of a "Parking (Part 5)" header row, or None."""
    for w in words:
        if w[4].lower() != "parking":
            continue
        row = [o for o in words if abs(o[1] - w[1]) < 5 and o[0] >= w[0]]
        joined = " ".join(o[4].lower() for o in row)
        if "part" in joined and "5)" in joined.replace(" ", ""):
            return w[1]
    return None


def _parking_part5(words) -> tuple[int | None, int | None]:
    """Read (vehicle, secure bicycle) from a "Parking (Part 5)" table that has no Number-of-Stalls row.

    This is the other Surrey layout: the parking rows hang directly off a "Parking (Part 5)" header
    inside the zoning-comparison table, with the Proposed figure in the right-hand column. The single
    "Total" row is the vehicle count and a "Secure"/"Bicycle" row the long-term bicycle count. As with
    the Number-of-Stalls read, anything ambiguous returns None rather than a guess.
    """
    ny = _part5_header_y(words)
    if ny is None:
        return None, None
    # The Proposed column header sits on, or just below, the Parking (Part 5) row (not the zoning
    # table's "Proposed CD Zone" far above it). A per-lot table has two Proposed columns, so its
    # single-lot Total is ambiguous: require exactly one Proposed column, else decline.
    cols = _proposed_columns(words, ny - 6, ny + 30)
    if len(cols) != 1:
        return None, None
    xprop = cols[0]
    bike_y = min((w[1] for w in words if w[4].lower() in ("bicycle", "bike") and w[1] > ny), default=1e9)
    totals = [w for w in words if w[4].lower().rstrip(":") == "total" and ny < w[1] < bike_y]
    vehicle = _proposed_value(words, totals[0], xprop, band=90) if len(totals) == 1 else None
    # Bicycle: a "Secure" row (two-column variant) or a single "Bicycle:" row carrying the count.
    bike_row = next((w for w in words if w[4].lower() == "secure" and w[1] > ny), None) \
        or next((w for w in words if w[4].lower().rstrip(":") == "bicycle" and w[1] >= bike_y), None)
    bike = _proposed_value(words, bike_row, xprop, band=90) if bike_row else None
    return vehicle, bike


def pdf_parking(pdf_bytes: bytes) -> tuple[int | None, int | None]:
    """Return (vehicle total, secure bicycle) from a report's parking table.

    A high-PRECISION positional read of the Proposed column: the "Number of Stalls" table first
    (see _parking_from_words), then the "Parking (Part 5)" zoning-table layout (_parking_part5). A
    value is set only when the table is unambiguous, otherwise None is returned rather than a guess.
    """
    import pymupdf  # optional dep, see pyproject [pdf]

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    for i in range(min(doc.page_count, 40)):  # the parking table is in the report body, not deep
        page = doc[i]
        text = page.get_text().lower()
        if "number of stalls" not in text and "parking (part 5)" not in text:
            continue
        words = page.get_text("words")
        vehicle, bike = _parking_from_words(words)
        if vehicle is None and bike is None:
            vehicle, bike = _parking_part5(words)
        if vehicle is not None or bike is not None:
            return vehicle, bike
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


def _apply_pdf_parking(row: dict, pdf_bytes: bytes) -> None:
    """Fill the parking stalls from a development report's "Number of Stalls" table, in place.

    Only development rows (those with a unit count or a residential/mixed class) are read, and the
    read is high-precision: a value is set only when the table is unambiguous, otherwise the row
    stays a parking Claude-read / review candidate.
    """
    if not (features.has_value(row.get("units_total"))
            or row.get("development_class") in _PARKING_CLASSES):
        return
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
        if pdf_bytes:
            _apply_pdf_parking(row, pdf_bytes)
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
