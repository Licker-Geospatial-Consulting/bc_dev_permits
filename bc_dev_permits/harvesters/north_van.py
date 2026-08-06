"""North Vancouver (CNV) HTML harvester.

Flow:  Active-Applications index  ->  per-application detail pages  ->  dev_permit rows.

Rule (updated): everything visible in the HTML is parsed DETERMINISTICALLY here.
Ollama is NOT used on page prose — it is reserved for PDFs on the fallback path,
i.e. when a detail page carries no usable text and only links documents.

Public API:
    get_application_links(list_html, base_url) -> list[str]
    parse_detail(html, url)                    -> dict   (a dev_permit row + children)
    harvest(session=None, limit=None, use_cache=True) -> list[dict]  (live; needs network)

Shared settings live in bc_dev_permits.config; deterministic field parsing in
bc_dev_permits.features; the PDF fallback in bc_dev_permits.modeling.predict. The
`make data` CLI that saves JSON / upserts Postgres is bc_dev_permits.dataset.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bc_dev_permits import config, features

LIST_URL = config.NORTH_VAN_LIST_URL
MUNICIPALITY = "north_van"
PERMIT_ID_RE = re.compile(r"PLN\d{4}-\d{5}", re.IGNORECASE)
MIN_PROSE_CHARS = 120  # below this we treat the page as "no usable text"
USER_AGENT = config.USER_AGENT
TIMEOUT = config.HTTP_TIMEOUT  # cnv.org streams the 500KB index slowly when cold
RETRIES = config.HTTP_RETRIES  # transient read/connection timeouts are common here
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL  # reuse a fetched page so test re-runs are instant


# --------------------------------------------------------------------------- #
# 1. Link discovery
# --------------------------------------------------------------------------- #
def get_application_links(list_html: str, base_url: str = LIST_URL) -> list[str]:
    """Return absolute detail-page URLs from the Active-Applications index.

    Grabs anchors that live *under* the Active-Applications path and are deeper than
    the index page itself (so we skip the index and the huge global nav that repeats
    the same handful of section links).
    """
    soup = BeautifulSoup(list_html, "lxml")
    root = _main_content(soup) or soup
    base_path = "/Active-Applications/"
    seen, out = set(), []
    for a in root.find_all("a", href=True):
        href = a["href"]
        absu = urljoin(base_url, href)
        if base_path in absu and not absu.rstrip("/").endswith("Active-Applications"):
            key = absu.split("#")[0].rstrip("/")
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


# --------------------------------------------------------------------------- #
# 2. Detail page parsing
# --------------------------------------------------------------------------- #
def parse_detail(html: str, url: str) -> dict:
    """Parse one detail page into a dev_permit row (with milestones/documents)."""
    soup = BeautifulSoup(html, "lxml")
    main = _main_content(soup) or soup

    address = _address(soup, main)
    prose = _prose(main)
    milestones = _milestones(main)
    documents = _documents(main, url)
    permit_id = _permit_id(documents, prose) or _slug(url)

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": url,
        "development_name": _development_name(soup, address),
        "address": address,
        "status": milestones[-1]["milestone"] if milestones else None,
        "raw_text": prose or None,
        "extraction_method": "html",
        "milestones": milestones,
        "documents": documents,
        # defaults, possibly overwritten below
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }

    if prose and len(prose) >= MIN_PROSE_CHARS:
        # Deterministic HTML parse — no LLM.
        row.update(features.extract_all(prose))
        row["is_parsed"] = True
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        # Fallback: no usable text. Keep the document links; a later Ollama PDF pass
        # (bc_dev_permits.modeling.predict) fills the fields from the PDFs.
        row["needs_pdf_extraction"] = bool(documents)
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True

    return row


# --------------------------------------------------------------------------- #
# 3. Live harvest (needs network — run where cnv.org is reachable)
# --------------------------------------------------------------------------- #
def _build_session():
    """Return a requests Session that retries transient timeouts / 5xx with backoff."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    retry = Retry(
        total=RETRIES,
        connect=RETRIES,
        read=RETRIES,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    """GET url as text, backed by a TTL file cache so test re-runs don't refetch."""
    path = os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest()[:16] + ".html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest the index + detail pages into dev_permit rows (live; needs network)."""
    session = session or _build_session()

    list_html = _cached_get(session, LIST_URL, use_cache)
    links = get_application_links(list_html)
    if limit:
        links = links[:limit]
    rows = []
    for link in links:
        html = _cached_get(session, link, use_cache)
        rows.append(parse_detail(html, link))
    return rows


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _main_content(soup: BeautifulSoup):
    """Best-effort main-content container, excluding the site nav/header/footer.

    CNV renders the application content inside ``#page-content`` (and, nested within it,
    ``.content-section`` / ``.infoPanel``). Pinning these first keeps the site chrome —
    crucially the City Hall address in the footer — out of scope; without it we fell back
    to the whole document and that footer address leaked into the parsed prose. The
    generic selectors remain as a fallback for other page templates.
    """
    for sel in (
        "#page-content",
        ".content-section",
        "main",
        "article",
        "#content",
        ".content-main",
        ".main-content",
        "#main",
        "[role=main]",
    ):
        node = soup.select_one(sel)
        if node:
            return node
    return None


def _address(soup, main) -> str | None:
    h1 = (main.find("h1") if main else None) or soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)
    return None


def _development_name(soup, address) -> str | None:
    # CNV titles sometimes carry a project name in parentheses.
    src = (soup.title.get_text() if soup.title else "") or (address or "")
    m = re.search(r"\(([^)]+)\)", src)
    return m.group(1).strip() if m else None


def _prose(main) -> str:
    """Concatenate the descriptive text from the main content region.

    CNV puts the application blurb in a ``<p>`` on most pages but in a bare ``<div>`` on
    others (e.g. 215 West Keith Road). Collect both, in document order, so a page whose
    blurb is a ``<div>`` is not skipped just because some unrelated ``<p>`` exists — the
    previous "divs only if no <p> at all" rule let the footer address win on such pages.
    Tables (milestones) and container ``<div>``s that only hold other blocks are ignored
    so we keep prose, not layout scaffolding.
    """
    parts, seen = [], set()
    for el in main.find_all(["p", "div"]):
        if el.find_parent("table"):
            continue
        if el.name == "div" and el.find(["div", "table", "p", "ul", "ol"]):
            continue  # a container div, not a leaf carrying its own text
        txt = el.get_text(" ", strip=True)
        min_len = 40 if el.name == "p" else 60
        if len(txt) > min_len and txt not in seen:
            seen.add(txt)
            parts.append(txt)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _milestones(main) -> list[dict]:
    """Parse the 'Application Process & Information' table -> [{milestone, milestone_date}]."""
    out = []
    for table in main.find_all("table"):
        header = " ".join(
            th.get_text(" ", strip=True).lower() for th in table.find_all(["th", "td"])[:4]
        )
        if "milestone" not in header and "date" not in header:
            continue
        rows = table.find_all("tr")
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            milestone = cells[0].get_text(" ", strip=True)
            date = _parse_date(cells[1].get_text(" ", strip=True))
            if milestone:
                out.append({"milestone": milestone, "milestone_date": date})
        break
    return out


def _documents(main, base_url) -> list[dict]:
    docs, seen = [], set()
    for a in main.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if not re.search(r"\.pdf(\?|$)", href, re.IGNORECASE):
            continue
        if href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True) or None
        docs.append({"url": href, "title": title, "doc_role": _doc_role(title)})
    return docs


def _doc_role(title: str | None) -> str:
    t = (title or "").lower()
    if "drawing" in t or "plan" in t or "architect" in t or "landscape" in t:
        return "drawings"
    if "staff report" in t or "report" in t:
        return "staff_report"
    if "notice" in t:
        return "notice"
    if "application" in t:
        return "application"
    return "other"


def _permit_id(documents, prose) -> str | None:
    for d in documents:
        m = PERMIT_ID_RE.search(d["url"])
        if m:
            return m.group(0).upper()
    m = PERMIT_ID_RE.search(prose or "")
    return m.group(0).upper() if m else None


def _slug(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _parse_date(s: str):
    s = s.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None
