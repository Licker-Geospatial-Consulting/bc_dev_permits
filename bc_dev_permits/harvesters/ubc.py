"""UBC (Campus + Community Planning) HTML harvester (+ Claude-read PDF enrichment).

Flow:  development-projects list  ->  per-project detail pages  ->  dev_permit rows.

Like Victoria, North Vancouver and West Vancouver, everything in the HTML is parsed
DETERMINISTICALLY here (project prose -> bc_dev_permits.features, name + permit id from the <h1>,
the Location field, a documents list). Unlike West Vancouver the site is NOT behind Cloudflare, so a
plain HTTP GET works.

PDF enrichment is **Claude-read, not Ollama** (see the pdf-parsing memory). When the page is
low-signal, the project's PDFs are read by Claude in-session in priority order - Development Permit
Board report, then architectural / parking / civil drawings, then the rest - and the first that
yields project data wins. `select_pdf_sources` returns that ordered list; the fields are applied
with `apply_claude_pdf` (shared with West Van), which reuses the merge / conflict / provenance code:
deterministic HTML is never overwritten, and a genuine HTML-vs-PDF disagreement is logged to
`row['conflicts']` (the Victoria conflict mechanism).

Public API mirrors west_van:
    get_project_cards(list_html, base)      -> list[dict]  ({url, permit_id, status})
    parse_detail(html, url, card=None)      -> dict
    select_pdf_sources(detail_html, url)    -> list[dict]  (documents, best first)
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

LIST_URL = config.UBC_LIST_URL
BASE = "https://planning.ubc.ca"
MUNICIPALITY = "ubc"
MIN_PROSE_CHARS = 120
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# A UBC development-permit number ("DP24018", "DP25022T", "DP24027"). A project card on the list
# page always carries one; it is also in the detail page's <h1>.
_DP_RE = re.compile(r"\bDP\d{4,5}[A-Za-z]?\b")
_STATUS_RE = re.compile(
    r"\b(Permit Approved|Under Construction|Application|Approved|Completed|Permit)\b")
# Shared Claude-read PDF enrichment (see pdf_enrich).
apply_claude_pdf = pdf_enrich.apply_claude_pdf


# --------------------------------------------------------------------------- #
# 1. Link discovery
# --------------------------------------------------------------------------- #
def get_project_cards(list_html: str, base: str = LIST_URL) -> list[dict]:
    """Return one card per development project from the list page: {url, permit_id, status}.

    Project cards are the links whose text carries a "DP#####" permit number (navigation links do
    not); the status badge ("Application" / "Permit Approved" / "Under Construction") is read from
    the same card text.
    """
    soup = BeautifulSoup(list_html, "lxml")
    view = soup.select_one(".view-project-view-page") or soup
    cards: dict[str, dict] = {}
    for a in view.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        dp = _DP_RE.search(text)
        if not dp:
            continue
        url = urljoin(base, a["href"]).split("#")[0].split("?")[0].rstrip("/")
        if "/planning-development/" in url or "/sites/" in url:
            continue
        status = _STATUS_RE.search(text)
        cards.setdefault(url, {
            "url": url,
            "permit_id": dp.group(0).upper(),
            "status": status.group(1) if status else None,
        })
    return list(cards.values())


# --------------------------------------------------------------------------- #
# 2. Detail-page parsing
# --------------------------------------------------------------------------- #
def parse_detail(html: str, url: str, card: dict | None = None) -> dict:
    """Parse one project detail page into a dev_permit row (with documents)."""
    soup = BeautifulSoup(html, "lxml")
    main = _main_content(soup) or soup
    card = card or {}

    name, permit_from_title = _split_title(_title(soup, main))
    permit_id = permit_from_title or card.get("permit_id") or _slug(url)
    main_text = re.sub(r"\s+", " ", main.get_text(" ", strip=True))
    prose = _prose(main)
    documents = _documents(main, url)

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": url,
        "development_name": name,
        "address": _location(main_text) or name,
        "permit_type": "Development Permit",
        "status": card.get("status"),
        "raw_text": prose or None,
        "extraction_method": "html",
        "milestones": _milestones(main),
        "documents": documents,
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }

    if prose and len(prose) >= MIN_PROSE_CHARS:
        row.update(features.extract_all(prose))
        row["is_parsed"] = True
        row["permit_type"] = "Development Permit"  # keep over the prose's permit_type guess
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True
    # The application PDFs (DP Board report, architectural / parking drawings) routinely carry
    # parking, unit mix and floor area the HTML omits, so a project with documents is a PDF-read
    # candidate whenever it is low-signal OR missing any of those richer fields.
    row["needs_pdf_extraction"] = bool(documents) and (
        pdf_enrich.needs_pdf(row)
        or any(not features.has_value(row.get(f))
               for f in ("parking_vehicle_stalls", "unit_mix", "floor_area")))
    return row


# --------------------------------------------------------------------------- #
# 3. PDF document selection (priority order for the Claude read)
# --------------------------------------------------------------------------- #
# Lower rank = read first. The Development Permit Board report carries the planning summary + full
# project stats; architectural / parking drawings carry the project-data and parking sheets; civil
# and other drawings next; narrative reports last.
_DOC_PRIORITY = (
    ("development permit board", 0), ("dp board", 0), ("board report", 0), ("staff report", 0),
    ("architectural", 1), ("architecture", 1), ("parking", 1),
    ("civil", 2), ("landscape", 2), ("plan", 2), ("drawing", 2), ("design", 2),
    ("report", 3), ("rationale", 3), ("memo", 3), ("study", 3),
)


def _doc_rank(title: str | None) -> int:
    t = (title or "").lower()
    return next((rank for key, rank in _DOC_PRIORITY if key in t), 4)


def select_pdf_sources(detail_html: str, url: str) -> list[dict]:
    """Return the project's PDFs ordered best-first for the Claude read (see _DOC_PRIORITY)."""
    soup = BeautifulSoup(detail_html, "lxml")
    main = _main_content(soup) or soup
    return sorted(_documents(main, url), key=lambda d: _doc_rank(d.get("title")))


# --------------------------------------------------------------------------- #
# 4. Live harvest (plain HTTP; PDFs are read by Claude in-session afterwards)
# --------------------------------------------------------------------------- #
def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest the list + detail pages into dev_permit rows (HTML only; PDF pass is Claude-read)."""
    session = session or _session()
    cards = get_project_cards(_cached_get(session, LIST_URL, use_cache))
    if limit:
        cards = cards[:limit]
    rows = []
    for card in cards:
        html = _cached_get(session, card["url"], use_cache)
        if html:
            rows.append(parse_detail(html, card["url"], card))
    consolidate.consolidate_by_address(rows)
    return rows


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
    path = os.path.join(CACHE_DIR, f"ubc_{key}.html")
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
def _main_content(soup: BeautifulSoup):
    for sel in ("main", "#main-content", "[role=main]", "article", "#content", ".region-content"):
        node = soup.select_one(sel)
        if node:
            return node
    return None


def _title(soup, main) -> str:
    h1 = (main.find("h1") if main else None) or soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")


def _split_title(title: str) -> tuple[str | None, str | None]:
    """Split "<Name> - DP#####" into (name, permit_id)."""
    if not title:
        return None, None
    dp = _DP_RE.search(title)
    permit_id = dp.group(0).upper() if dp else None
    name = re.split(r"\s*[-–]\s*DP\d", title, maxsplit=1)[0]  # noqa: RUF001
    return re.sub(r"\s+", " ", name).strip() or None, permit_id


def _location(main_text: str) -> str | None:
    """Pull the project's Location field value out of the page text (stopping before the contact)."""
    m = re.search(
        r"\bLocation\b\s+(.+?)(?:\s+Proponent|\s+Neighbourhood|\s+Status\b|\s+Development Permit Board"
        r"|\s+Application Documents|\s+Contact\b|$)", main_text)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:180] if m else None


# Boilerplate that appears on every UBC page: the "campus consists of 994 acres" about-UBC blurb
# (whose acreage otherwise masquerades as a project area) and the staff contact block (a name +
# phone + email, which is PII and never a project detail).
_BOILERPLATE = re.compile(
    r"campus consists of|acres \(402 hectares\)|Manager, Development Services"
    r"|Contact\b[^.]{0,40}\(\d{3}\)|Email:\s*\S+@", re.IGNORECASE)


def _prose(main) -> str:
    """Concatenate the descriptive paragraphs from the main content region (prose, not layout)."""
    parts, seen = [], set()
    for el in main.find_all(["p", "div", "li"]):
        if el.find_parent("table"):
            continue
        if el.name in ("div", "li") and el.find(["div", "table", "p", "ul", "ol"]):
            continue
        txt = el.get_text(" ", strip=True)
        min_len = 40 if el.name == "p" else 60
        if len(txt) > min_len and txt not in seen and not _BOILERPLATE.search(txt):
            seen.add(txt)
            parts.append(txt)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _milestones(main) -> list[dict]:
    out = []
    for table in main.find_all("table"):
        header = " ".join(th.get_text(" ", strip=True).lower() for th in table.find_all(["th", "td"])[:4])
        if "date" not in header and "milestone" not in header and "status" not in header:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            milestone = cells[0].get_text(" ", strip=True)
            if milestone:
                out.append({"milestone": milestone, "milestone_date": _parse_date(cells[1].get_text(" ", strip=True))})
        break
    return out


def _documents(main, base_url) -> list[dict]:
    """PDF documents on the page, with their link text as the title."""
    docs, seen = [], set()
    for a in main.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if not re.search(r"\.pdf(\?|$)", href, re.IGNORECASE) or href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True) or None
        docs.append({"url": href, "title": title, "doc_role": _doc_role(title)})
    return docs


def _doc_role(title: str | None) -> str:
    t = (title or "").lower()
    if "board report" in t or "staff report" in t:
        return "board_report"
    if "architect" in t or "parking" in t or "drawing" in t or "plan" in t:
        return "drawings"
    if "report" in t or "study" in t:
        return "report"
    return "other"


def _slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _parse_date(s: str):
    s = s.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None
