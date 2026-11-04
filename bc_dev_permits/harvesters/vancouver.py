"""City of Vancouver HTML harvester (+ Claude-read PDF enrichment).

Vancouver has no single machine-readable application index. CityHallWatch publishes a periodic
snapshot post that lists every application under three headings - New, Active, and Approved - each
line linking to the city's ShapeYourCity (EngagementHQ) consultation page:

    868 E Hastings St - DP-2026-00743 - posted October 8, 2026   ->  shapeyourcity.ca/868-e-hastings-st

Flow:  CityHallWatch snapshot (list)  ->  ShapeYourCity detail pages  ->  dev_permit rows.

The snapshot gives the address, application number and posted/approval date deterministically. Each
ShapeYourCity page is server-rendered (plain HTTP, no Cloudflare) and carries the project prose
(parsed by features.py) plus an Application Documents list. The richer project-data - floor area,
building height, site area, vehicle + bicycle parking - lives only in the application PDFs (a site
plan or elevation sheet's statistics table), so those are read by **Claude in-session** (not Ollama;
see the pdf-parsing memory) in priority order, and applied with `apply_claude_pdf` (shared with West
Van / UBC): deterministic HTML is never overwritten and a genuine HTML-vs-PDF disagreement is logged
to `row['conflicts']` (the Victoria conflict mechanism).

Streams: harvest New + Active by default; Approved (a far larger backlog) is left for later
iterations by passing ``streams``.

Public API mirrors ubc / west_van:
    parse_list(list_html, streams)          -> list[dict]  (cards: url, permit_id, address, ...)
    parse_detail(html, url, card=None)      -> dict
    select_pdf_sources(detail_html, url)    -> list[dict]  (documents, best first)
    download_pdf(session, document)         -> bytes        (for the in-session Claude read)
    apply_claude_pdf(row, fields, document) -> int
    harvest(limit=None, use_cache=True)     -> list[dict]
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import pdf_enrich

LIST_URL = config.VANCOUVER_LIST_URL
MUNICIPALITY = "vancouver"
MIN_PROSE_CHARS = 80
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
DEFAULT_STREAMS = ("new", "active")

# A Vancouver application number: two/three-letter prefix, year, sequence (DP-2026-00743,
# RZ-2025-00012, DE-2024-00456).
_APP_RE = re.compile(r"\b([A-Z]{2,3})-(\d{4})-(\d{4,6})\b")
# A posted/approval date in the list tail ("posted October 8, 2026", "permit issued May 14, 2026").
_DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")
_VERB_RE = re.compile(
    r"\b(permit issued|approved by Council|approved by the Development Permit Board|approved|posted)\b",
    re.IGNORECASE)

# Vancouver application-number prefixes -> permit type.
_TYPE_BY_PREFIX = {
    "DP": "Development Permit", "DE": "Development Permit", "RZ": "Rezoning",
    "DB": "Development Permit (Board)", "CD": "Conditional Use", "BP": "Building Permit",
}
# How the three snapshot headings map to a row status.
_STREAM_STATUS = {"new": "New", "active": "Active", "approved": "Approved"}
# The milestone a list verb records.
_VERB_MILESTONE = {
    "posted": "Application Posted", "approved": "Approved",
    "approved by council": "Approved by Council",
    "approved by the development permit board": "Approved by Development Permit Board",
    "permit issued": "Permit Issued",
}

# Shared Claude-read PDF enrichment (see pdf_enrich).
apply_claude_pdf = pdf_enrich.apply_claude_pdf


# --------------------------------------------------------------------------- #
# 1. Snapshot list parsing
# --------------------------------------------------------------------------- #
def parse_list(list_html: str, streams=DEFAULT_STREAMS) -> list[dict]:
    """Return one card per application in the requested snapshot streams.

    Each card is {url, permit_id, address, permit_type, stream, status, posted_date, verb}. The
    stream (new / active / approved) is set by the most recent <h2>/<h3> heading walked.
    """
    soup = BeautifulSoup(list_html, "lxml")
    content = soup.select_one(".entry-content") or soup.select_one("article") or soup
    wanted = {s.lower() for s in streams}

    cards: dict[str, dict] = {}
    stream = None
    for el in content.descendants:
        name = getattr(el, "name", None)
        if name in ("h1", "h2", "h3", "h4"):
            heading = el.get_text(" ", strip=True).lower()
            for key in ("new", "active", "approved"):
                if f"{key} development application" in heading:
                    stream = key
                    break
        elif name == "li" and stream in wanted:
            card = _card_from_li(el, stream)
            if card and card["url"] not in cards:
                cards[card["url"]] = card
    return list(cards.values())


def _card_from_li(li, stream: str) -> dict | None:
    """Build a card from one list item, or None if it is not an application line."""
    anchor = li.find("a", href=re.compile(r"shapeyourcity\.ca", re.IGNORECASE))
    if not anchor:
        return None
    url = anchor["href"].split("#")[0].split("?")[0].rstrip("/")
    anchor_text = anchor.get_text(" ", strip=True)
    li_text = li.get_text(" ", strip=True)

    m = _APP_RE.search(anchor_text) or _APP_RE.search(li_text)
    permit_id = m.group(0) if m else None
    prefix = m.group(1) if m else None
    # The anchor text is "<address> - <app number>" (sometimes "<address> - <note> - <app number>"),
    # joined by an en/em-dash surrounded by spaces. The address is the first such segment; splitting
    # on a bare hyphen would instead break a civic-number range ("2120-2150 W 10th Ave").
    address = re.split("\\s[\u2013\u2014]\\s", anchor_text, maxsplit=1)[0].strip() or None

    date_m = _DATE_RE.search(li_text)
    verb_m = _VERB_RE.search(li_text)
    return {
        "url": url,
        "permit_id": permit_id,
        "address": address or None,
        "permit_type": _TYPE_BY_PREFIX.get(prefix, "Development Permit" if prefix else None),
        "stream": stream,
        "status": _STREAM_STATUS.get(stream),
        "posted_date": _parse_date(date_m.group(1)) if date_m else None,
        "verb": verb_m.group(1).lower() if verb_m else None,
    }


# --------------------------------------------------------------------------- #
# 2. Detail-page parsing (ShapeYourCity)
# --------------------------------------------------------------------------- #
def parse_detail(html: str, url: str, card: dict | None = None,
                 documents: list[dict] | None = None) -> dict:
    """Parse one ShapeYourCity page into a dev_permit row (prose + documents).

    ``documents`` lets the caller supply the FULL document list (fetched via fetch_documents, past
    the page's "more" button); when omitted, only the documents inline in ``html`` are used.
    """
    soup = BeautifulSoup(html, "lxml")
    card = card or {}
    prose = _prose(soup)
    if documents is None:
        documents = _documents(soup, url)

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": card.get("permit_id") or _slug(url),
        "source_url": url,
        "development_name": None,
        "address": card.get("address"),
        "latitude": None,
        "longitude": None,
        "permit_type": card.get("permit_type") or "Development Permit",
        "status": card.get("status"),
        "raw_text": prose or None,
        "extraction_method": "html",
        "milestones": _milestones(card),
        "documents": documents,
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }

    if prose and len(prose) >= MIN_PROSE_CHARS:
        row.update(features.extract_all(prose))
        row["is_parsed"] = True
        row["permit_type"] = card.get("permit_type") or row["permit_type"]  # keep the list's type
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True

    # The application PDFs (site plan / elevation statistics table) carry the floor area, height,
    # site area and parking the HTML omits, so a project with documents is a PDF-read candidate
    # whenever it is low-signal OR missing any of those richer fields.
    row["needs_pdf_extraction"] = bool(documents) and (
        pdf_enrich.needs_pdf(row)
        or any(not features.has_value(row.get(f))
               for f in ("parking_vehicle_stalls", "floor_area", "height_m")))
    return row


# --------------------------------------------------------------------------- #
# 3. PDF document selection (priority order for the Claude read)
# --------------------------------------------------------------------------- #
# Lower rank = read first. The project-data / statistics / site plan / design-rationale sheets are
# the primary parsing documents - they carry the floor area, units, unit mix, height, site area and
# parking table; an elevation adds height; architectural / floor plans next; survey / landscape /
# context after; narrative reports then; an operational letter (which sometimes carries the ONLY
# parking or area figure, e.g. 438 W King Edward, 500 Dunsmuir) before the administrative postcard.
# _doc_rank returns the FIRST matching key, so a more specific key must be listed before a broader
# one ("rationale" before "design"; "site plan" before "elevation" for "Site plan and elevations").
_DOC_PRIORITY = (
    ("project statistic", 0), ("project stat", 0), ("project data", 0), ("project detail", 0),
    ("project info", 0), ("statistic", 0), ("stats", 0), ("site plan", 0),
    ("design rationale", 1), ("rationale", 1), ("elevation", 1),
    ("architectur", 2), ("floor plan", 2), ("plans", 2), ("drawing", 2), ("design", 2),
    ("survey", 3), ("landscape", 3), ("context", 3), ("shadow", 3), ("render", 3), ("3d", 3),
    ("report", 4), ("study", 4), ("memo", 4),
    ("operational letter", 5), ("letter", 5),
    ("postcard", 6), ("notification", 6),
)


def _doc_rank(title: str | None) -> int:
    t = (title or "").lower()
    return next((rank for key, rank in _DOC_PRIORITY if key in t), 4)


def order_documents(documents: list[dict]) -> list[dict]:
    """Return a copy of the documents ordered best-first for the Claude read (see _DOC_PRIORITY)."""
    return sorted(documents, key=lambda d: _doc_rank(d.get("title")))


def select_pdf_sources(detail_html: str, url: str) -> list[dict]:
    """Return the application's PDFs ordered best-first for the Claude read (see _DOC_PRIORITY).

    Note: this reads only the documents in the given HTML. The live harvest populates each row with
    the FULL document set (see fetch_documents / the 'more' button), so prefer ranking
    ``row['documents']`` with ``order_documents`` over calling this on a stale page.
    """
    soup = BeautifulSoup(detail_html, "lxml")
    return order_documents(_documents(soup, url))


# --------------------------------------------------------------------------- #
# 4. Live harvest (plain HTTP; PDFs are read by Claude in-session afterwards)
# --------------------------------------------------------------------------- #
def harvest(session=None, limit: int | None = None, use_cache: bool = True,
            streams=DEFAULT_STREAMS) -> list[dict]:
    """Harvest the snapshot list + ShapeYourCity detail pages into rows (PDF pass is Claude-read).

    Defaults to the New + Active streams; pass ``streams=("new", "active", "approved")`` to include
    the approved backlog. One physical site can file several applications, so rows are collapsed by
    civic address before returning.
    """
    session = session or _session()
    cards = parse_list(_cached_get(session, LIST_URL, use_cache), streams)
    if limit:
        cards = cards[:limit]
    rows = []
    for card in cards:
        html = _cached_get(session, card["url"], use_cache)
        if html:
            # Fetch the FULL document list (the page only renders the first few behind a "more"
            # button); fall back to the page's own links if the widget endpoint is unavailable.
            documents = fetch_documents(session, html, card["url"], use_cache)
            rows.append(parse_detail(html, card["url"], card, documents=documents))
    consolidate.consolidate_by_address(rows)
    return rows


def _doc_widget_endpoints(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Return the distinct '.../widgets/<id>/documents' endpoints backing the page's document links.

    ShapeYourCity renders only the first handful of documents inline; the full set is served by the
    document-library widget's own endpoint (the "more" button's data source).
    """
    eps: list[str] = []
    for a in soup.select("a.document-library-widget-link[href]"):
        href = urljoin(base_url, a["href"]).split("#")[0]
        m = re.match(r"(.*/widgets/\d+/documents)(?:/\d+)?$", href)
        if m and m.group(1) not in eps:
            eps.append(m.group(1))
    return eps


def fetch_documents(session, detail_html: str, url: str, use_cache: bool = True) -> list[dict]:
    """Return the application's FULL document list (following the document widget's 'more' endpoint).

    Each document-library widget on the page is fetched in full and its links merged (deduped by
    url). Falls back to the inline links when no widget endpoint is found or a fetch fails.
    """
    soup = BeautifulSoup(detail_html, "lxml")
    endpoints = _doc_widget_endpoints(soup, url)
    if not endpoints:
        return _documents(soup, url)
    docs, seen = [], set()
    for endpoint in endpoints:
        try:
            fragment = _cached_get(session, endpoint, use_cache)
        except (OSError, ValueError, RuntimeError):
            continue
        for d in _documents(BeautifulSoup(fragment, "lxml"), url):
            if d["url"] not in seen:
                seen.add(d["url"])
                docs.append(d)
    return docs or _documents(soup, url)


# --------------------------------------------------------------------------- #
# PDF download (follows the /documents/<id>/download redirect to signed S3)
# --------------------------------------------------------------------------- #
def download_pdf(session, document: dict, use_cache: bool = True) -> bytes:
    """Fetch a ShapeYourCity document's PDF bytes for an in-session Claude read.

    The viewer URL (.../documents/<id>) 302-redirects at .../documents/<id>/download to a signed,
    short-lived S3 URL, which requests follows. Bytes are cached on disk by document id so re-reads
    are offline; the signed URL itself is never cached (it expires).
    """
    doc_id = _slug(document["url"])
    path = os.path.join(CACHE_DIR, f"vancouver_doc_{doc_id}.pdf")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, "rb") as fh:
            return fh.read()
    download_url = document["url"].rstrip("/") + "/download"
    content = session.get(download_url, timeout=config.HTTP_TIMEOUT, allow_redirects=True).content
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return content


# --------------------------------------------------------------------------- #
# HTTP (plain requests) + disk cache
# --------------------------------------------------------------------------- #
def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    """GET url as text, backed by the shared TTL file cache."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"vancouver_{key}.html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# HTML helpers
# --------------------------------------------------------------------------- #
# Froala / EngagementHQ share + boilerplate that precedes the real description text.
_SHARE_RE = re.compile(r"\bShare\b.*?\blink\b", re.IGNORECASE | re.DOTALL)


def _prose(soup: BeautifulSoup) -> str:
    """Return the project description text (the EngagementHQ full-description block)."""
    node = (soup.select_one("div.full-description")
            or soup.select_one("div.truncated-description")
            or soup.select_one("div.description.parent-description")
            or soup.select_one("div.fr-view"))
    if node:
        text = node.get_text(" ", strip=True)
    else:
        md = soup.find("meta", attrs={"name": "description"})
        text = md.get("content", "") if md else ""
    # Strip the leading "Share ... on Facebook ... link" social block when it bleeds in.
    text = _SHARE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _documents(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Return the Application Documents list: {url, title, doc_role} per ShapeYourCity link."""
    docs, seen = [], set()
    for a in soup.select("a.document-library-widget-link[href]"):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if "/documents/" not in href or href in seen:
            continue
        seen.add(href)
        title = re.sub(r"\s*\((?:[\d.]+\s*[KMG]B|pdf)\)\s*", " ",
                       a.get_text(" ", strip=True), flags=re.IGNORECASE).strip() or None
        docs.append({"url": href, "title": title, "doc_role": _doc_role(title)})
    return docs


def _doc_role(title: str | None) -> str:
    t = (title or "").lower()
    if any(k in t for k in ("site plan", "project detail", "statistic", "project data")):
        return "site_plan"
    if "elevation" in t:
        return "elevation"
    if any(k in t for k in ("architect", "drawing", "plan", "design")):
        return "drawings"
    if any(k in t for k in ("report", "rationale", "study")):
        return "report"
    return "other"


def _slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _milestones(card: dict) -> list[dict]:
    """One milestone from the snapshot's posted/approval date, named for the list verb."""
    date = card.get("posted_date")
    if not date:
        return []
    milestone = _VERB_MILESTONE.get(card.get("verb") or "", "Application Posted")
    return [{"milestone": milestone, "milestone_date": date}]


def _parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
