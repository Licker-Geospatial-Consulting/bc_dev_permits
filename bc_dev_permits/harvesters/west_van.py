"""District of West Vancouver HTML harvester (+ Claude-read PDF enrichment).

Flow:  Active Development Applications index  ->  per-application detail pages  ->  dev_permit rows.

Like North Vancouver and Victoria, everything visible in the HTML is parsed DETERMINISTICALLY
here (prose -> bc_dev_permits.features, address from the <h1>, a documents list, milestones).
What is different:

* **Cloudflare.** westvancouver.ca sits behind Cloudflare bot protection, so a plain HTTP GET is
  refused ("Just a moment..."). The harvester fetches through a real browser (Playwright, the
  [browser] extra) with a fresh context per page and a cool-down between pages, and caches each
  rendered page to disk so re-runs and the PDF pass do not re-fetch.
* **PDF enrichment is Claude-read, not Ollama.** When a page is low-signal, the application's PDFs
  are read by Claude in-session (not the local model): documents are tried in priority order -
  Staff Report, then Architectural Drawings / Plans, then Preliminary Drawings, then the rest -
  and the first one that yields project data wins. `select_pdf_sources` returns that ordered list;
  Claude reads the chosen PDF and the fields are applied with `apply_claude_pdf`, which reuses the
  shared merge / conflict / provenance code (deterministic HTML is never overwritten; a genuine
  HTML-vs-PDF disagreement is logged to `row['conflicts']`).

Public API:
    get_application_links(list_html, base_url) -> list[str]
    parse_detail(html, url)                    -> dict
    select_pdf_sources(detail_html, url)       -> list[dict]   (documents, best first)
    apply_claude_pdf(row, fields, document)    -> int          (Claude-read fields -> row)
    harvest(limit=None, use_cache=True)        -> list[dict]   (live; needs Playwright)
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
from bc_dev_permits.harvesters import pdf_enrich

LIST_URL = config.WEST_VAN_LIST_URL
MUNICIPALITY = "west_van"
BASE = "https://westvancouver.ca"
APP_PATH = "/business-development/development-applications/"
MIN_PROSE_CHARS = 120  # below this we treat the page as "no usable text"
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
# Non-application links that live under APP_PATH on the index page.
_NON_APPS = {"active-development-applications", "approved-development-applications",
             "archived-development-applications", "preliminary-development-projects",
             "development-applications-map", "development-applications"}
# West Van permit ids appear in prose/URLs for some files; slug is the stable fallback.
PERMIT_ID_RE = re.compile(r"\b(?:DP|DVP|REZ|EDP|PLN|BLD)[\s-]?\d{2,5}(?:-\d{2,5})?\b", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# 1. Link discovery
# --------------------------------------------------------------------------- #
def get_application_links(list_html: str, base_url: str = LIST_URL) -> list[str]:
    """Return absolute detail-page URLs from the Active Development Applications index."""
    soup = BeautifulSoup(list_html, "lxml")
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        absu = urljoin(base_url, a["href"]).split("#")[0].split("?")[0].rstrip("/")
        if APP_PATH not in absu:
            continue
        slug = absu.split(APP_PATH)[-1]
        if not slug or "/" in slug or slug in _NON_APPS or absu in seen:
            continue
        seen.add(absu)
        out.append(absu)
    return out


# --------------------------------------------------------------------------- #
# 2. Detail page parsing (deterministic HTML)
# --------------------------------------------------------------------------- #
def parse_detail(html: str, url: str) -> dict:
    """Parse one detail page into a dev_permit row (with milestones/documents)."""
    soup = BeautifulSoup(html, "lxml")
    main = _main_content(soup) or soup

    address = _address(soup, main)
    prose = _prose(main)
    documents = _documents(main, url)
    milestones = _milestones(main)
    permit_id = _permit_id(documents, prose) or _slug(url)

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": url,
        "development_name": None,
        "address": address,
        "status": milestones[-1]["milestone"] if milestones else None,
        "raw_text": prose or None,
        "extraction_method": "html",
        "milestones": milestones,
        "documents": documents,
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }

    if prose and len(prose) >= MIN_PROSE_CHARS:
        row.update(features.extract_all(prose))
        row["is_parsed"] = True
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True
    # Whatever the page gave us, a low-signal row is a PDF-enrichment candidate (pdf_enrich.needs_pdf).
    row["needs_pdf_extraction"] = bool(documents) and pdf_enrich.needs_pdf(row)
    return row


# --------------------------------------------------------------------------- #
# 3. PDF document selection (priority order for the Claude read)
# --------------------------------------------------------------------------- #
# Lower rank = read first. The Staff Report carries the planning summary + stats; architectural
# drawings / plans carry the project-data sheet; preliminary drawings and anything else are last.
_DOC_PRIORITY = (
    ("staff report", 0),
    ("architectural", 1), ("architecture", 1), ("plan set", 1), ("plans", 1),
    ("preliminary", 2), ("drawing", 2),
    ("report", 3), ("rationale", 3), ("letter", 3),
)


def _doc_rank(title: str | None) -> int:
    t = (title or "").lower()
    return next((rank for key, rank in _DOC_PRIORITY if key in t), 4)


def select_pdf_sources(detail_html: str, url: str) -> list[dict]:
    """Return the application's PDFs ordered best-first for the Claude read (see _DOC_PRIORITY)."""
    soup = BeautifulSoup(detail_html, "lxml")
    main = _main_content(soup) or soup
    docs = _documents(main, url)
    return sorted(docs, key=lambda d: _doc_rank(d.get("title")))


# --------------------------------------------------------------------------- #
# 4. Claude-read PDF enrichment (fields are read by Claude in-session)
# --------------------------------------------------------------------------- #
def apply_claude_pdf(row: dict, fields: dict, document: dict | None = None) -> int:
    """Merge Claude-read PDF fields into a row; record provenance + conflicts. Return count filled.

    `fields` is the schema-shaped dict Claude extracted from the PDF (only stated values). Each is
    stamped method ``pdf_claude`` (non-deterministic), deterministic HTML values are never
    overwritten, and a genuine HTML-vs-PDF disagreement is logged to ``row['conflicts']`` - all via
    the shared pdf_enrich code, the same way Ollama results are handled.
    """
    # A low-signal HTML row carries a placeholder development_class="unknown" / empty occupancy;
    # clear those so the PDF's concrete class fills the gap instead of logging a false conflict.
    if row.get("development_class") in (None, "unknown"):
        row["development_class"] = None
    if not row.get("occupancy_types"):
        row.pop("occupancy_types", None)

    payload = {k: v for k, v in (fields or {}).items()
               if k not in pdf_enrich.PDF_META and features.has_value(v)}
    payload["field_methods"] = dict.fromkeys(payload, features.METHOD_PDF_CLAUDE)
    payload["extraction_method"] = "claude_read"
    if document:
        payload["pdf_document"] = document
    if "occupancies" in (fields or {}):
        payload["occupancies"] = fields["occupancies"]
    filled = pdf_enrich.merge_pdf_fields(row, payload)
    if filled or row.get("conflicts"):
        pdf_enrich.record_pdf_provenance(row, payload)
        if payload.get("occupancies"):
            row["occupancies"] = payload["occupancies"]
    return filled


# --------------------------------------------------------------------------- #
# 5. Live harvest (needs Playwright; site is behind Cloudflare)
# --------------------------------------------------------------------------- #
def harvest(limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest the index + detail pages into dev_permit rows (live; needs Playwright)."""
    list_html = _fetch(LIST_URL, use_cache=use_cache)
    links = get_application_links(list_html)
    if limit:
        links = links[:limit]
    htmls = _fetch_many(links, use_cache=use_cache)
    return [parse_detail(htmls[link], link) for link in links if htmls.get(link)]


# --------------------------------------------------------------------------- #
# Browser fetch (Cloudflare-aware) + disk cache
# --------------------------------------------------------------------------- #
def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, "west_van_" + hashlib.sha256(url.encode()).hexdigest()[:16] + ".html")


def _read_cache(url: str, use_cache: bool) -> str | None:
    path = _cache_path(url)
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return None


def _write_cache(url: str, html: str) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(url), "w", encoding="utf-8") as fh:
        fh.write(html)


def _fetch(url: str, use_cache: bool = True) -> str:
    """Fetch one page (cache-first)."""
    return _fetch_many([url], use_cache=use_cache)[url]


def _fetch_many(urls: list[str], use_cache: bool = True) -> dict[str, str]:
    """Fetch pages cache-first; Playwright (paced) fills misses and caches them."""
    out: dict[str, str] = {}
    misses = []
    for url in urls:
        cached = _read_cache(url, use_cache)
        if cached is not None:
            out[url] = cached
        else:
            misses.append(url)
    if misses:
        for url, html in _browser_fetch(misses).items():
            _write_cache(url, html)
            out[url] = html
    return out


def _browser_fetch(urls: list[str]) -> dict[str, str]:
    """Render each URL through headless Chromium, pacing requests so Cloudflare does not block.

    Cloudflare rate-limits bursts, so each page gets a fresh context and there is a cool-down
    between pages; a blocked response is retried after a longer wait.
    """
    from playwright.sync_api import sync_playwright  # optional dep, see pyproject [browser]

    out: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for i, url in enumerate(urls):
                if i:
                    _cooldown(browser, 18_000)  # space requests out between pages
                html = _render(browser, url)
                if html:
                    out[url] = html
        finally:
            browser.close()
    return out


def _render(browser, url: str, attempts: int = 3) -> str | None:
    for _ in range(attempts):
        ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
        try:
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(9_000)  # let the Cloudflare JS challenge clear
            html = page.content()
            if _blocked(html):
                page.wait_for_timeout(9_000)
                html = page.content()
            if not _blocked(html) and len(html) > 50_000:
                return html
            page.wait_for_timeout(12_000)  # backoff before the next attempt
        finally:
            ctx.close()
    return None


def _cooldown(browser, ms: int) -> None:
    ctx = browser.new_context()
    try:
        ctx.new_page().wait_for_timeout(ms)
    finally:
        ctx.close()


def _blocked(html: str) -> bool:
    return "Just a moment" in html or "Attention Required" in html


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _main_content(soup: BeautifulSoup):
    """Best-effort main-content container, excluding site nav / header / footer."""
    for sel in ("main", "#main-content", "[role=main]", "article", "#content", ".content-main",
                ".main-content", "#page-content"):
        node = soup.select_one(sel)
        if node:
            return node
    return None


def _address(soup, main) -> str | None:
    """Address from the <h1>, with the trailing descriptor stripped.

    West Van titles tack a permit descriptor onto the civic address - "1337 Ottawa Avenue
    (Rezoning)", "2213 Lawson Avenue - Minor Development Variance Permit" - so the address is the
    part before the first " - " or " (".
    """
    h1 = (main.find("h1") if main else None) or soup.find("h1")
    text = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else "")
    if not text:
        return None
    text = re.split(r"\s+[-–]\s+|\s*\(", text, maxsplit=1)[0]  # noqa: RUF001
    return re.sub(r"\s+", " ", text).strip() or None


def _prose(main) -> str:
    """Concatenate the descriptive paragraphs from the main content region (prose, not layout)."""
    parts, seen = [], set()
    for el in main.find_all(["p", "div"]):
        if el.find_parent("table"):
            continue
        if el.name == "div" and el.find(["div", "table", "p", "ul", "ol"]):
            continue  # a container div, not a leaf carrying its own text
        txt = el.get_text(" ", strip=True)
        min_len = 40 if el.name == "p" else 60
        # Drop the staff contact block (name + phone + Email) that closes every page.
        if len(txt) > min_len and txt not in seen and not re.search(r"\bEmail\b.*\d{3}-\d{4}|\d{3}-\d{4}\s*Email", txt):
            seen.add(txt)
            parts.append(txt)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _milestones(main) -> list[dict]:
    """Parse any process/timeline table -> [{milestone, milestone_date}]."""
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
            date = _parse_date(cells[1].get_text(" ", strip=True))
            if milestone:
                out.append({"milestone": milestone, "milestone_date": date})
        break
    return out


def _documents(main, base_url) -> list[dict]:
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
    if "staff report" in t:
        return "staff_report"
    if "architect" in t or "drawing" in t or "plan" in t:
        return "drawings"
    if "report" in t:
        return "report"
    if "notice" in t:
        return "notice"
    return "other"


def _permit_id(documents, prose) -> str | None:
    for d in documents:
        m = PERMIT_ID_RE.search(d.get("title") or "") or PERMIT_ID_RE.search(d["url"])
        if m:
            return re.sub(r"\s+", "", m.group(0)).upper()
    m = PERMIT_ID_RE.search(prose or "")
    return re.sub(r"\s+", "", m.group(0)).upper() if m else None


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
