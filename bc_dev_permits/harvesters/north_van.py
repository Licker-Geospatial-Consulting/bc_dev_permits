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

import contextlib
from datetime import datetime
import hashlib
import os
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bc_dev_permits import config, features
from bc_dev_permits.harvesters import pdf_enrich

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
        # Finalize the confidence over the FULL row so harvester-provided fields (address)
        # are credited. Every municipality harvester should score the assembled row this
        # same way, via the shared features.score_confidence, to keep scores comparable.
        row["extraction_confidence"] = features.score_confidence(row)
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
# 4. PDF enrichment: fill low-signal rows from their documents (needs Ollama)
# --------------------------------------------------------------------------- #
# CNV publishes the application materials in the "Application Process & Information" table:
# each milestone row has a Documents column, and the substantive project data lives in the
# "Application Accepted" milestone's documents (the original submission's architectural
# drawings - a plan set the vision model reads). Some pages use an older template with no such
# table; there the materials are inline links (an OCP amendment / application package the text
# model reads), which we fall back to. Merge / provenance is shared (pdf_enrich).
_DOC_PRIORITY = (
    ("architect", 0),                                   # architectural drawings - the plan set
    ("drawing", 1), ("plan", 1),
    ("application package", 0), ("amendment", 0), ("booklet", 0), ("package", 1),
    ("report", 2), ("summary", 2),
)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _process_table(main):
    """Return the 'Application Process & Information' table (milestone + documents), or None."""
    for table in main.find_all("table"):
        head = [c.get_text(" ", strip=True).lower() for c in table.find_all(["th", "td"])[:6]]
        if any("milestone" in h for h in head) and any("document" in h for h in head):
            return table
    return None


def _table_documents(main, base_url: str) -> list[dict]:
    """PDFs from the process table's Documents column, each tagged with its milestone."""
    table = _process_table(main)
    if not table:
        return []
    header = next((row for row in table.find_all("tr")
                   if any("milestone" in c.get_text(strip=True).lower()
                          for c in row.find_all(["td", "th"]))), None)
    header_texts = [c.get_text(" ", strip=True).lower() for c in header.find_all(["td", "th"])] if header else []
    doc_col = next((i for i, t in enumerate(header_texts) if "document" in t), 2)
    out, seen = [], set()
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) <= doc_col or any("milestone" in c.get_text(strip=True).lower() for c in cells):
            continue  # too short, or the header row itself
        milestone = cells[0].get_text(" ", strip=True)
        for a in cells[doc_col].find_all("a", href=True):
            href = urljoin(base_url, a["href"])
            if not re.search(r"\.pdf(\?|$)", href, re.IGNORECASE) or href in seen:
                continue
            seen.add(href)
            out.append({"milestone": milestone, "title": a.get_text(" ", strip=True) or None, "url": href})
    return out


def _doc_rank(title: str | None) -> int:
    """Lower is preferred: drawings / application packages first, then plans, reports, rest."""
    t = (title or "").lower()
    return next((rank for key, rank in _DOC_PRIORITY if key in t), 3)


def _title_recency(title: str | None) -> tuple[int, int]:
    """(year, month) parsed from a document title, for 'newest wins' among equal-rank docs."""
    t = title or ""
    year = re.search(r"\b(20\d{2})\b", t)
    month = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s*\d{0,2},?\s*20\d{2}", t)
    return (int(year.group(1)) if year else 0, _MONTHS.get(month.group(1).lower(), 0) if month else 0)


def _pick_document(docs: list[dict]) -> dict | None:
    """Prefer the 'Application Accepted' milestone's documents; best-ranked, newest first."""
    if not docs:
        return None
    accepted = [d for d in docs if "application accepted" in (d.get("milestone") or "").lower()]
    pool = sorted(accepted or docs, key=lambda d: _title_recency(d.get("title")), reverse=True)
    return min(pool, key=lambda d: _doc_rank(d.get("title")))  # stable: newest among best rank


def select_pdf_source(detail_html: str, url: str) -> dict | None:
    """Choose the PDF to extract for a low-signal row: {source_url, document{title,url}}.

    Primary source is the application-process table's Documents column; pages that use the older
    template without that table fall back to their inline application documents.
    """
    soup = BeautifulSoup(detail_html, "lxml")
    main = _main_content(soup) or soup
    doc = _pick_document(_table_documents(main, url)) or _pick_document(_documents(main, url))
    return {"source_url": url, "document": doc} if doc else None


def extract_via_pdf(detail_html: str, url: str, session=None) -> dict | None:
    """Download the selected PDF and run the Ollama extractor; return permit fields or None.

    Needs the [pdf] extra and a running Ollama server (see references/ollama_pdf_extraction.md);
    source selection is unit-tested separately via select_pdf_source.
    """
    import tempfile

    from bc_dev_permits.modeling import predict

    session = session or _build_session()
    src = select_pdf_source(detail_html, url)
    if not src:
        return None
    doc = src["document"]
    pdf_bytes = session.get(doc["url"], timeout=TIMEOUT).content
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)  # noqa: SIM115
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        fields = predict.extract_from_pdf(tmp.name)
    finally:
        # Best-effort cleanup: never let a temp-file delete failure discard a good result.
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)
    fields["extraction_method"] = "ollama_pdf"
    fields["pdf_source_url"] = src["source_url"]
    fields["pdf_document"] = doc
    return fields


def enrich_via_pdf(rows, session=None, use_cache: bool = True, limit: int | None = None) -> int:
    """Fill each low-signal row from its best application PDF via Ollama (in place).

    North Van selects the document from the application-process table (extract_via_pdf); the
    shared pdf_enrich loop handles the queue, gap-filling, conflicts, and provenance.
    """
    session = session or _build_session()
    return pdf_enrich.enrich(
        rows,
        fetch_html=lambda url, cache: _cached_get(session, url, cache),
        extract=lambda html, url, s: extract_via_pdf(html, url, session=s),
        session=session, use_cache=use_cache, limit=limit, label="north_van",
    )


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
