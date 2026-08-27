"""City of Victoria harvester — Prospero "OurCity" permit tracker.

Flow:  search.aspx ACTIVE list (paged)  ->  per-folder Details.aspx  ->  dev_permit rows.

Unlike the CNV/West Van HTML pages, Victoria's list is an ASP.NET grid: the default GET
is already filtered to ACTIVE, each result row carries the full application data
(address, folder number, type, and the complete purpose sentence), and later pages come
from `__doPostBack` form posts. So the CORE fields are parsed straight from the list
rows; the detail page only ENRICHES a row with its full address list and Task Progress
milestones (a single application often spans several addresses). See
references/prospero_tracker.md.

Rule (shared): everything visible in the HTML is parsed DETERMINISTICALLY here via
bc_dev_permits.features. Ollama stays on the PDF-fallback path only.

Public API:
    parse_list(list_html, base_url)  -> list[dict]   (one dev_permit row per ACTIVE result)
    parse_detail(detail_html, url)   -> dict          (address list + milestones + status)
    harvest(session=None, limit=None, use_cache=True) -> list[dict]   (live; needs network)
"""

from __future__ import annotations

import contextlib
from datetime import datetime
import hashlib
import os
import re
import sys
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bc_dev_permits import config, features

LIST_URL = config.VICTORIA_LIST_URL
DETAIL_BASE = config.VICTORIA_DETAIL_BASE
MUNICIPALITY = "victoria"
# Folder numbers double as permit ids: DPV (dev permit w/ variance), DP, DVP, REZ, HAP...
FOLDER_RE = re.compile(r"folderNumber=([A-Za-z]{2,4}\d{3,6})", re.IGNORECASE)
MIN_PROSE_CHARS = 40  # the purpose sentence is short but always meaningful
MAX_PAGES = 50  # safety cap for the postback pager walk
USER_AGENT = config.USER_AGENT
TIMEOUT = config.HTTP_TIMEOUT
RETRIES = config.HTTP_RETRIES
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL


# --------------------------------------------------------------------------- #
# 1. List parsing (core fields come from here)
# --------------------------------------------------------------------------- #
def parse_list(list_html: str, base_url: str = DETAIL_BASE) -> list[dict]:
    """Parse every ACTIVE result row into a dev_permit row (core fields + parsed prose)."""
    soup = BeautifulSoup(list_html, "lxml")
    rows = []
    for card in soup.select("div.form-result"):
        row = _row_from_card(card, base_url)
        if row:
            rows.append(row)
    return rows


def _row_from_card(card, base_url: str) -> dict | None:
    folder = _card_folder(card)
    if not folder:
        return None
    purpose = _card_text(card, "search_purpose")
    address = _card_text(card, "search_address")
    permit_type = _card_text(card, "search_type") or None
    app_date = _find_date(card.get_text(" ", strip=True))
    url = f"{base_url}?folderNumber={folder}"

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": folder,
        "source_url": url,
        "development_name": None,
        "address": _norm_address(address),
        "status": "ACTIVE",  # the list is ACTIVE-filtered; detail confirms/overrides
        "raw_text": purpose or None,
        "extraction_method": "html",
        "milestones": [{"milestone": "Application Received", "milestone_date": app_date}]
        if app_date
        else [],
        "documents": [],
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }

    if purpose and len(purpose) >= MIN_PROSE_CHARS:
        row.update(features.extract_all(purpose))
        # The tracker states the type explicitly; prefer it over the prose-regex guess.
        if permit_type:
            row["permit_type"] = permit_type
        row["is_parsed"] = True
        # When the DP defers to a concurrent rezoning, its own materials live elsewhere.
        if re.search(r"refer to (the )?rezoning", purpose, re.IGNORECASE):
            row["needs_pdf_extraction"] = True
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True
    return row


# --------------------------------------------------------------------------- #
# 2. Detail-page enrichment (full address list + milestones + status)
# --------------------------------------------------------------------------- #
def parse_detail(detail_html: str, url: str) -> dict:
    """Return {addresses, milestones, documents, status} for the PRIMARY application.

    Related permits/applications (the concurrent REZ blocks) are excluded so their
    addresses, dates, and statuses never bleed into this permit.
    """
    soup = BeautifulSoup(detail_html, "lxml")
    _drop_related(soup)
    return {
        "addresses": _addresses(soup),
        "milestones": _milestones(soup),
        "documents": _documents(soup, url),
        "status": _status(soup),
    }


def _drop_related(soup) -> None:
    """Remove the 'Related Permits and Applications' blocks from the tree in place."""
    for node in soup.select(".related-project, [class*='related-permit'], [id*='Related']"):
        node.decompose()


def _addresses(soup) -> list[str]:
    label = soup.select_one("span[id$='AddressesLabel'], [id*='Addresses']")
    if not label:
        return []
    # Addresses are <br/>-separated inside the label; normalize the runs of whitespace.
    raw = label.get_text("\n", strip=True)
    out, seen = [], set()
    for line in raw.split("\n"):
        addr = _norm_address(line)
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _milestones(soup) -> list[dict]:
    out, seen = [], set()
    for tile in soup.select("div[class*='-task']"):
        name_el = tile.select_one(".task-type")
        if not name_el:
            continue
        name = name_el.get_text(" ", strip=True)
        date = None
        for tx in tile.select(".task-text"):
            date = _find_date(tx.get_text(" ", strip=True))
            if date:
                break
        key = (name, date)
        if name and key not in seen:
            seen.add(key)
            out.append({"milestone": name, "milestone_date": date})
    return out


def _documents(soup, base_url) -> list[dict]:
    docs, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if not re.search(r"\.pdf(\?|$)", href, re.IGNORECASE) or href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True) or None
        docs.append({"url": href, "title": title, "doc_role": "other"})
    return docs


def _status(soup) -> str | None:
    for div in soup.find_all("div"):
        txt = div.get_text(" ", strip=True)
        m = re.match(r"Status:\s*([A-Za-z]+)", txt)
        if m:
            return m.group(1).upper()
    return None


# --------------------------------------------------------------------------- #
# 3. Live harvest (needs network)
# --------------------------------------------------------------------------- #
def harvest(
    session=None,
    limit: int | None = None,
    use_cache: bool = True,
    use_browser: bool = True,
) -> list[dict]:
    """Harvest ACTIVE applications across all list pages, enriched from detail pages.

    The list pager is a stateful ASP.NET AJAX UpdatePanel that rejects scripted postbacks,
    so it is walked with a headless browser (Playwright, the ``[browser]`` extra). Detail
    pages are plain GETs fetched with requests. If Playwright is unavailable we fall back
    to the first page only and warn (see references/prospero_tracker.md).
    """
    session = session or _build_session()

    rows, seen = [], set()
    for html in _list_pages(session, limit, use_browser):
        for row in parse_list(html):
            if row["permit_id"] in seen:
                continue
            seen.add(row["permit_id"])
            rows.append(row)
        if limit and len(rows) >= limit:
            break
    if limit:
        rows = rows[:limit]

    for row in rows:
        detail_html = _cached_get(session, row["source_url"], use_cache)
        _merge_detail(row, parse_detail(detail_html, row["source_url"]))
    return rows


def _list_pages(session, limit: int | None, use_browser: bool) -> list[str]:
    """Return the HTML of each ACTIVE list page (all pages via browser, else page 1)."""
    if use_browser:
        try:
            return _browser_list_pages(limit)
        except ImportError:
            print(
                "victoria: Playwright not installed; falling back to the first list page "
                "only. Install the [browser] extra and run 'playwright install chromium' "
                "for full pagination.",
                file=sys.stderr,
            )
    html = _cached_get(session, LIST_URL, use_cache=True)
    _warn_incomplete(html, 1)
    return [html]


# Prospero's pager JS calls __doPostBack, but the page never emits it (nor the
# __EVENTTARGET/__EVENTARGUMENT fields), so pagination is dead even in a real browser tab
# until those exist. We inject the stock ASP.NET implementation before driving the pager;
# it is idempotent and re-applied after each postback reloads the page.
_INJECT_DOPOSTBACK = """() => {
  var f = document.forms[0];
  function ensure(id){ var e=document.getElementById(id);
    if(!e){ e=document.createElement('input'); e.type='hidden'; e.id=id; e.name=id; f.appendChild(e); }
    return e; }
  ensure('__EVENTTARGET'); ensure('__EVENTARGUMENT');
  if (typeof window.__doPostBack !== 'function') {
    window.__doPostBack = function(t,a){
      document.getElementById('__EVENTTARGET').value = t;
      document.getElementById('__EVENTARGUMENT').value = a;
      f.submit();
    };
  }
}"""


def _browser_list_pages(limit: int | None = None) -> list[str]:
    """Drive the ASP.NET pager with a headless browser; return each page's grid HTML.

    Prospero advances only under a real browser, and only once __doPostBack is injected
    (the page ships a pager that calls it without ever defining it). We load the ACTIVE
    search page and page through, capturing the rendered HTML after each postback.
    """
    from playwright.sync_api import sync_playwright  # optional dep, see pyproject [browser]

    htmls: list[str] = []
    collected, prev_first, page_no = 0, None, 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.set_default_timeout(30_000)
            page.goto(LIST_URL, wait_until="networkidle")
            for _ in range(MAX_PAGES):
                page.wait_for_selector("div.form-result")
                page.evaluate(_INJECT_DOPOSTBACK)  # each postback reloads, so re-inject
                html = page.content()
                first = _first_folder(html)
                if not first or first == prev_first:
                    break  # empty page, or the grid did not advance
                htmls.append(html)
                prev_first = first
                collected += len(parse_list(html))
                if limit and collected >= limit:
                    break
                page_no += 1
                if not _browser_goto_page(page, page_no, first):
                    break  # no further page
        finally:
            browser.close()
    return htmls


def _browser_goto_page(page, page_no: int, prev_first: str) -> bool:
    """Jump to `page_no` via the site's pagination(N); True once the grid's first folder changes."""
    # The full-page postback destroys the JS execution context, so evaluate() may raise.
    with contextlib.suppress(Exception):
        page.evaluate("(n) => pagination(n)", page_no)
    try:
        page.wait_for_function(
            "(prev) => { const e = document.querySelector('div.form-result .search_folderNo');"
            " return e && e.textContent.trim().toUpperCase() !== prev; }",
            arg=prev_first,
            timeout=20_000,
        )
    except Exception:  # noqa: BLE001 - a timeout means the last page had no successor
        return False
    return True


def _first_folder(html: str) -> str | None:
    """Return the first result's folder number on a page (used as a page-changed signal)."""
    el = BeautifulSoup(html, "lxml").select_one("div.form-result .search_folderNo")
    return el.get_text(strip=True).upper() if el else None


def _advertised_pages(html: str) -> int:
    """Highest page number the pager exposes via its pagination(N) controls (0 if none)."""
    nums = [int(n) for n in re.findall(r"pagination\((\d+)\)", html)]
    return max(nums) if nums else 0


def _warn_incomplete(html: str, pages_fetched: int) -> None:
    total = _advertised_pages(html)
    if total > pages_fetched:
        print(
            f"victoria: retrieved {pages_fetched} of at least {total} ACTIVE list pages. "
            f"The Prospero pager rejects scripted postbacks, so later pages were skipped "
            f"(needs a browser-driven fallback, see references/prospero_tracker.md).",
            file=sys.stderr,
        )


def _merge_detail(row: dict, detail: dict) -> None:
    """Fold detail-page enrichment into a list-derived row (fields already parsed)."""
    addresses = detail.get("addresses") or []
    if addresses:
        row["address"] = addresses[0]
        if len(addresses) > 1:
            # Preserve every address for provenance without re-feeding them to the parser
            # (a leading street number could otherwise be misread as a unit count).
            row["raw_text"] = f"{row['raw_text']}\n\nAddresses: {'; '.join(addresses)}"
    if detail.get("milestones"):
        row["milestones"] = detail["milestones"]
    if detail.get("documents"):
        row["documents"] = detail["documents"]
    if detail.get("status"):
        row["status"] = detail["status"]
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6


# --------------------------------------------------------------------------- #
# 4. PDF-fallback source selection (for low-signal rows)
# --------------------------------------------------------------------------- #
# When a Victoria row scores low (little/no usable prose on the detail page), we look for
# a document to run through the Ollama PDF extractor. The rule (see prospero_tracker.md):
#   1. If a RELATED application is ACTIVE and exposes a Details link, use THAT
#      application's documents (the live application carries the real materials).
#   2. Otherwise use THIS page's own documents.
# Within the chosen document set, prefer the most recent "letter to mayor/council"; if
# there is none, fall back to the most recent document of any kind.
def documents(soup, base_url: str) -> list[dict]:
    """Return this page's own documents as [{title, url}] (excludes related-app docs)."""
    out, seen = [], set()
    for a in soup.select("a[href*='FileDownload']"):
        if a.find_parent(class_="related-project"):
            continue  # a related application's own attachment, not this page's
        href = urljoin(base_url, a["href"])
        if href in seen:
            continue
        seen.add(href)
        out.append({"title": a.get_text(" ", strip=True), "url": href})
    return out


def _active_related_details_url(soup) -> str | None:
    """Details URL of the first ACTIVE related application, or None."""
    for block in soup.select("div.related-project"):
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        if not re.search(r"Status:\s*ACTIVE\b", text, re.IGNORECASE):
            continue
        candidates = [block.get("onclick") or ""]
        candidates += [e.get("onclick") or e.get("href") or "" for e in block.select("*")]
        for attr in candidates:
            m = FOLDER_RE.search(attr)
            if m:
                return f"{DETAIL_BASE}?folderNumber={m.group(1).upper()}"
    return None


def _is_council_letter(title: str) -> bool:
    t = (title or "").lower()
    return "letter" in t and ("council" in t or "mayor" in t)


def _most_recent(docs: list[dict]) -> dict | None:
    """Pick the newest document: by date parsed from the title, else the last listed."""
    if not docs:
        return None
    dated = [(d, _find_date(d["title"])) for d in docs]
    with_date = [(when, d) for d, when in dated if when]
    if with_date:
        return max(with_date, key=lambda x: x[0])[1]
    return docs[-1]  # undated: the tracker appends newer documents last


def _pick_document(docs: list[dict]) -> dict | None:
    """Prefer the most recent council/mayor letter; else the most recent document."""
    return _most_recent([d for d in docs if _is_council_letter(d["title"])]) or _most_recent(docs)


def select_pdf_source(detail_html: str, url: str, fetch) -> dict | None:
    """Choose the PDF to extract for a low-signal row: {source_url, document{title,url}}.

    `fetch(u) -> html` retrieves the related application's page when one is followed
    (injected so this stays testable without a network call).
    """
    soup = BeautifulSoup(detail_html, "lxml")
    related_url = _active_related_details_url(soup)
    if related_url:
        docs = documents(BeautifulSoup(fetch(related_url), "lxml"), related_url)
        source_url = related_url
    else:
        docs = documents(soup, url)
        source_url = url
    doc = _pick_document(docs)
    return {"source_url": source_url, "document": doc} if doc else None


def extract_via_pdf(detail_html: str, url: str, session=None) -> dict | None:
    """Download the selected PDF and run the Ollama extractor; return permit fields or None.

    This is the fallback pass for low-signal rows. It needs the [pdf] extra and a running
    Ollama server (see references/ollama_pdf_extraction.md); the source-selection logic is
    unit-tested separately via select_pdf_source.
    """
    import tempfile

    from bc_dev_permits.modeling import predict

    session = session or _build_session()
    src = select_pdf_source(detail_html, url, lambda u: _cached_get(session, u))
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


# --------------------------------------------------------------------------- #
# 5. Batch enrichment: fill low-signal rows from their PDFs (needs Ollama)
# --------------------------------------------------------------------------- #
# Fields carried on the model result that are metadata, not permit columns.
_PDF_META = {"confidence", "extraction_method", "pdf_source_url", "pdf_document", "occupancies"}


def _needs_pdf(row: dict) -> bool:
    """Return True when the page gave us little to go on (a PDF-enrichment candidate)."""
    return bool(row.get("needs_pdf_extraction")) or (row.get("extraction_confidence") or 0) <= 0.4


def _merge_pdf_fields(row: dict, fields: dict) -> int:
    """Fill only the row's MISSING permit fields from the model output; return count filled.

    Deterministic HTML values already on the row are never overwritten - the project rule
    is that deterministic values take precedence over model-extracted ones.
    """
    filled = 0
    for key, val in (fields or {}).items():
        if key in _PDF_META or val in (None, [], {}, "unknown"):
            continue
        if row.get(key) in (None, [], {}, "unknown"):
            row[key] = val
            filled += 1
    return filled


def _record_pdf_provenance(row: dict, fields: dict) -> None:
    """Note the PDF source, mark its document extracted, and force review of LLM values."""
    row["extraction_method"] = f"html+{fields.get('extraction_method', 'ollama')}"
    row["needs_review"] = True  # model-derived fields are always human-checked
    row["extraction_confidence"] = features.score_confidence(row)
    doc = fields.get("pdf_document") or {}
    if doc.get("url"):
        docs = row.setdefault("documents", [])
        existing = next((d for d in docs if d.get("url") == doc["url"]), None)
        if existing:
            existing["extracted"] = True
        else:
            docs.append(
                {
                    "url": doc["url"],
                    "title": doc.get("title"),
                    "doc_role": "ollama_source",
                    "extracted": True,
                }
            )


def enrich_via_pdf(rows, session=None, use_cache: bool = True, limit: int | None = None) -> int:
    """Fill each low-signal row from its best PDF via Ollama (in place). Return count enriched.

    Processes the needs_pdf_extraction / low-confidence queue: for each candidate it selects
    the right document (select_pdf_source), runs the Ollama extractor, fills the row's gaps,
    and marks the source document extracted. Network / Ollama / PDF errors on one row are
    logged and skipped so the batch keeps going.
    """
    session = session or _build_session()
    enriched = 0
    for row in rows:
        if not _needs_pdf(row) or (limit and enriched >= limit):
            continue
        try:
            detail_html = _cached_get(session, row["source_url"], use_cache)
            fields = extract_via_pdf(detail_html, row["source_url"], session=session)
        except (OSError, ValueError, RuntimeError) as exc:  # network / pdf / ollama
            print(f"victoria: pdf-enrich skipped {row.get('permit_id')}: {exc}", file=sys.stderr)
            continue
        if fields and _merge_pdf_fields(row, fields):
            _record_pdf_provenance(row, fields)
            enriched += 1
    return enriched


# --------------------------------------------------------------------------- #
# session + cache (mirrors north_van; adds POST for the pager)
# --------------------------------------------------------------------------- #
def _build_session():
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
        allowed_methods=("GET", "POST"),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _cached_get(session, url: str, use_cache: bool = True) -> str:
    path = os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest()[:16] + ".html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _card_folder(card) -> str | None:
    m = FOLDER_RE.search(card.get("onclick") or "")
    if m:
        return m.group(1).upper()
    tag = card.select_one(".search_folderNo")
    return tag.get_text(strip=True).upper() if tag else None


def _card_text(card, cls: str) -> str:
    el = card.select_one(f".{cls}")
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _norm_address(s: str | None) -> str | None:
    """Collapse the wide whitespace the tracker pads addresses with ('512    PEMBROKE ST')."""
    if not s:
        return None
    addr = re.sub(r"\s+", " ", s).strip()
    return addr or None


def _find_date(text: str) -> str | None:
    m = re.search(r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})", text or "")
    return _parse_date(m.group(1)) if m else None


def _parse_date(s: str) -> str | None:
    s = re.sub(r"\bSept\.?\b", "Sep", s.strip())  # "Sept 18, 2020" -> strptime-friendly
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None
