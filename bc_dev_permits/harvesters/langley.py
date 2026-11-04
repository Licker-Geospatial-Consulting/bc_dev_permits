"""Township of Langley harvester - Prospero "OurCity" permit tracker.

Flow:  Search.aspx ACTIVE list (paged)  ->  per-folder Details.aspx  ->  dev_permit rows.

Langley runs the same Prospero grid as Victoria (see references/prospero_tracker.md): the
ACTIVE list rows carry the folder number, application type, address, and the full Purpose
sentence, so the CORE fields are parsed straight from the list; the detail page then
ENRICHES each row with its full address list, Task Progress milestones, Documents, and the
Related Permits and Applications block.

Document selection for the Claude-read PDF pass follows the order the user specified:
  1. the application's OWN documents (newest first), then
  2. the documents of each ACTIVE related application (rezoning first), newest first.
So the related rezoning/variance folder is the fallback used only when the application's own
documents are absent or not parsable - `select_pdf_sources` returns the full ordered list and
the Claude-read enrichment reads them in order until one yields usable fields. The HTML-vs-PDF
conflict mechanism is the shared one (`pdf_enrich.apply_claude_pdf`, as in Victoria).

Rule (shared): everything visible in the HTML is parsed DETERMINISTICALLY here via
bc_dev_permits.features. The PDF pass is Claude-read in-session (tools/langley_enrich.py).

Public API:
    parse_list(list_html, base_url)      -> list[dict]
    parse_detail(detail_html, url)       -> dict
    harvest(session=None, limit=None, use_cache=True) -> list[dict]   (live; needs network)
    select_pdf_sources(detail_html, url, fetch) -> list[dict]          (ordered PDF candidates)
"""

from __future__ import annotations

import contextlib
from datetime import datetime
import hashlib
import os
import re
import sys
import time

from bs4 import BeautifulSoup

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import pdf_enrich

# Shared Claude-read merge: deterministic HTML is never overwritten and an HTML-vs-PDF
# disagreement is logged to row['conflicts'] (the Victoria conflict mechanism). Used by
# tools/langley_enrich.py to apply the in-session PDF reads.
apply_claude_pdf = pdf_enrich.apply_claude_pdf

LIST_URL = config.LANGLEY_LIST_URL
DETAIL_BASE = config.LANGLEY_DETAIL_BASE
MUNICIPALITY = "langley"
# Folder numbers double as permit ids: AL (Agricultural Land Reserve), DP (Development
# Permit), RZ (Rezoning), DV (Development Variance Permit); others (SD, etc.) likely.
FOLDER_RE = re.compile(r"folderNumber=([A-Za-z]{2,4}\d{3,6})", re.IGNORECASE)
MIN_PROSE_CHARS = 40
MAX_PAGES = 50  # safety cap for the postback pager walk
# Related-application type priority: the rezoning carries the master materials, so try it
# before a variance/other when falling back for documents.
_REL_PRIORITY = ("rezon", "development permit", "agricultural", "subdivision")
USER_AGENT = config.USER_AGENT
TIMEOUT = config.HTTP_TIMEOUT
RETRIES = config.HTTP_RETRIES
CACHE_DIR = str(config.HTTP_CACHE_DIR)
CACHE_TTL = config.HTTP_CACHE_TTL


# --------------------------------------------------------------------------- #
# 1. List parsing (core fields come from here)
# --------------------------------------------------------------------------- #
def parse_list(list_html: str, base_url: str = DETAIL_BASE) -> list[dict]:
    """Parse every list result row into a dev_permit row (core fields + parsed prose)."""
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
        "permit_type": permit_type,
        "status": "ACTIVE",  # list is the ACTIVE set; detail confirms/overrides
        "raw_text": purpose or None,
        "extraction_method": "html",
        "milestones": [{"milestone": "Application Received", "milestone_date": app_date}]
        if app_date
        else [],
        "documents": [],
        "related_permits": [],
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": False,
        "extraction_confidence": 0.0,
    }
    _apply_prose(row, purpose, permit_type)
    return row


def _apply_prose(row: dict, purpose: str | None, permit_type: str | None) -> None:
    """Run the deterministic field parse over the Purpose prose, or mark the row low-signal."""
    if purpose and len(purpose) >= MIN_PROSE_CHARS:
        row.update(features.extract_all(purpose))
        if permit_type:  # the tracker states the type explicitly; trust it over the prose guess
            row["permit_type"] = permit_type
        row["is_parsed"] = True
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
        row["needs_review"] = True


# --------------------------------------------------------------------------- #
# 2. Detail-page enrichment
# --------------------------------------------------------------------------- #
def parse_detail(detail_html: str, url: str) -> dict:
    """Return {purpose, permit_type, status, addresses, milestones, documents, related}."""
    soup = BeautifulSoup(detail_html, "lxml")
    return {
        "purpose": _label(soup, "PurposeLabel"),
        "permit_type": _label(soup, "ApplicationTypeLabel"),
        "status": (_label(soup, "ApplicationFolderStatus") or "").upper() or None,
        "addresses": _addresses(soup),
        "milestones": _milestones(soup),
        "documents": _documents(soup, url),
        "related": _related_apps(soup),
    }


def _label(soup, suffix: str) -> str | None:
    el = soup.find(id=lambda x: x and x.endswith(suffix))
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() or None if el else None


def _addresses(soup) -> list[str]:
    label = soup.find(id=lambda x: x and x.endswith("AddressesLabel"))
    if not label:
        return []
    out, seen = [], set()
    for line in label.get_text("\n", strip=True).split("\n"):
        addr = _norm_address(line)
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def _milestones(soup) -> list[dict]:
    container = soup.find(id=lambda x: x and x.endswith("taskContainer"))
    scope = container or soup
    out, seen = [], set()
    for tile in scope.select(".completed-task, .featured-task"):
        text = re.sub(r"\s+", " ", tile.get_text(" ", strip=True))
        name = _task_name(text)
        date = _task_date(text)
        key = (name, date)
        if name and key not in seen:
            seen.add(key)
            out.append({"milestone": name, "milestone_date": date})
    # Drop an undated tile (e.g. the "... PASSED" featured-task) when the same milestone
    # already appears with a date.
    dated = {m["milestone"] for m in out if m["milestone_date"]}
    return [m for m in out if m["milestone_date"] or m["milestone"] not in dated]


def _task_name(text: str) -> str:
    """Return the task label before the 'Start/Completed Date' columns (or a trailing status word)."""
    head = re.split(r"\s*(?:Start Date|Completed Date|Status)\s*:", text, maxsplit=1)[0]
    head = re.sub(r"\b(PASSED|FAILED|ACTIVE|PENDING|COMPLETE[D]?)\b.*$", "", head)
    return head.strip()


def _task_date(text: str) -> str | None:
    """Prefer the Completed Date; else the Start Date; else any date in the tile."""
    for key in ("Completed Date", "Start Date"):
        m = re.search(rf"{key}:\s*([A-Za-z0-9,\s-]+?\d{{4}})", text)
        if m and (d := _parse_date(m.group(1).strip())):
            return d
    return _find_date(text)


def _documents(soup, base_url: str) -> list[dict]:
    """Return this application's own documents as [{title, url}] (related-app docs excluded)."""
    container = soup.find(id=lambda x: x and x.endswith("documentsContainer"))
    scope = container or soup
    out, seen = [], set()
    for a in scope.select("a[href*='FileDownload']"):
        if a.find_parent(class_="related-project"):
            continue  # a related application's attachment, not this page's own
        href = _abs(base_url, a["href"])
        if href in seen:
            continue
        seen.add(href)
        out.append({"title": a.get_text(" ", strip=True) or None, "url": href})
    return out


def _related_apps(soup) -> list[dict]:
    """Related Permits and Applications as [{permit_id, permit_type, status, url}]."""
    out, seen = [], set()
    for block in soup.select(".related-permits-container .form-result, .related-project"):
        m = FOLDER_RE.search(block.get("onclick") or "") or FOLDER_RE.search(str(block))
        if not m:
            continue
        folder = m.group(1).upper()
        if folder in seen:
            continue
        seen.add(folder)
        text = re.sub(r"\s+", " ", block.get_text(" ", strip=True))
        status = (
            "ACTIVE"
            if re.search(r"Status:\s*ACTIVE\b", text, re.IGNORECASE)
            else ("ARCHIVED" if re.search(r"Status:\s*ARCHIVED\b", text, re.IGNORECASE) else None)
        )
        ptype = _related_type(block)
        out.append(
            {
                "permit_id": folder,
                "permit_type": ptype,
                "status": status,
                "url": f"{DETAIL_BASE}?folderNumber={folder}",
            }
        )
    return out


def _related_type(block) -> str | None:
    """Return the application-type cell of a related block (2nd column of the heavy-font row)."""
    row = block.select_one(".related-permits-row.heavy-font")
    cols = row.select("[class*='col-']") if row else []
    if len(cols) >= 2:
        return re.sub(r"\s+", " ", cols[1].get_text(" ", strip=True)).strip() or None
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

    The list pager is the same stateful ASP.NET AJAX UpdatePanel as Victoria's, so it is
    walked with a headless browser (Playwright, the ``[browser]`` extra); without Playwright
    it degrades to the first page and warns. Detail pages are plain GETs.
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

    enriched = []
    for row in rows:
        detail_html = _cached_get(session, row["source_url"], use_cache)
        _merge_detail(row, parse_detail(detail_html, row["source_url"]))
        # Keep the ACTIVE set only; a detail status that is set and not ACTIVE is archived.
        if row["status"] in (None, "ACTIVE"):
            enriched.append(row)
    _inherit_class_from_related(enriched)
    # De-duplicate by civic address (shared rule, step 6): one physical site can file several
    # applications (a rezoning, its development permit, a variance, a subdivision). The strongest
    # record at an address is kept and the rest fold into it as related_permits, with milestones and
    # raw_text merged; genuinely distinct developments at one address stay separate.
    consolidate.consolidate_by_address(enriched)
    return enriched


def _inherit_class_from_related(rows: list[dict]) -> None:
    """Fill a still-unknown development_class from a related permit at the same project.

    A variance, plan amendment or subdivision folder often states no building use of its own
    ("vary the parking requirement", "amend the Community Plan") yet belongs to a development
    whose use is already classified on its related Development Permit or Rezoning. Those sibling
    folders are linked through ``related_permits``, so an unknown-class row deterministically
    inherits the class (and occupancy list) of a related folder that is classified. Iterated to a
    fixpoint so a chain of related folders resolves regardless of harvest order; deterministic HTML
    classification on the row's own prose always wins (only "unknown" rows are touched).
    """
    by_id = {r["permit_id"]: r for r in rows}

    def unknown(r: dict) -> bool:
        return (r.get("development_class") or "unknown") == "unknown"

    changed = True
    while changed:
        changed = False
        for row in rows:
            if not unknown(row):
                continue
            for rel in row.get("related_permits") or []:
                sib = by_id.get(rel.get("permit_id"))
                if sib is not None and not unknown(sib):
                    row["development_class"] = sib["development_class"]
                    row["occupancy_types"] = list(sib.get("occupancy_types") or [])
                    changed = True
                    break


def _merge_detail(row: dict, detail: dict) -> None:
    """Fold detail-page enrichment into a list-derived row."""
    # The detail Purpose is authoritative; re-parse if it is fuller than the list prose.
    purpose = detail.get("purpose")
    if purpose and len(purpose) > len(row.get("raw_text") or ""):
        row["raw_text"] = purpose
        _apply_prose(row, purpose, detail.get("permit_type") or row.get("permit_type"))
    if detail.get("permit_type"):
        row["permit_type"] = detail["permit_type"]
    if detail.get("status"):
        row["status"] = detail["status"]

    addresses = detail.get("addresses") or []
    if addresses:
        row["address"] = addresses[0]
        if len(addresses) > 1:
            # Keep every address as a structured field (so an HTML-vs-PDF address check can match
            # any of them) and in raw_text, without re-feeding them to the parser (a leading street
            # number could be misread as a unit count).
            row["addresses"] = addresses
            row["raw_text"] = f"{row['raw_text']}\n\nAddresses: {'; '.join(addresses)}"
    if detail.get("milestones"):
        row["milestones"] = detail["milestones"]
    if detail.get("documents"):
        row["documents"] = detail["documents"]

    related = detail.get("related") or []
    if related:
        row["related_permits"] = related
        note = "; ".join(f"{r['permit_id']} ({r['permit_type']}, {r['status']})" for r in related)
        row["raw_text"] = f"{row['raw_text']}\n\nRelated applications: {note}"

    # Every document-bearing row (its own documents, or an ACTIVE related application's) is a
    # Claude-read PDF candidate: the PDFs carry the richer fields (parking, unit mix, floor
    # area, ...) the Purpose prose lacks, so enrichment runs for ALL permits, not just the
    # low-signal ones (deterministic HTML still wins on merge).
    has_docs = bool(row["documents"]) or any(r["status"] == "ACTIVE" for r in related)
    if has_docs:
        row["needs_pdf_extraction"] = True

    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6


# --------------------------------------------------------------------------- #
# 4. PDF-source selection (ordered: own documents, then ACTIVE related applications)
# --------------------------------------------------------------------------- #
def select_pdf_sources(detail_html: str, url: str, fetch) -> list[dict]:
    """Ordered PDF candidates for a low-signal row: [{source_url, document{title,url}}].

    Order (the user's rule): this application's own documents first (newest first), then the
    documents of each ACTIVE related application (rezoning first), newest first. The related
    application is thus the fallback - read in order, stop at the first PDF that yields fields.
    `fetch(u) -> html` retrieves a related application's detail page (injected for testability).
    """
    soup = BeautifulSoup(detail_html, "lxml")
    out = []
    for doc in _recent_first(_documents(soup, url)):
        out.append({"source_url": url, "document": doc})
    for rel in _ordered_related(_related_apps(soup)):
        with contextlib.suppress(Exception):  # a related page that cannot be fetched is skipped
            rsoup = BeautifulSoup(fetch(rel["url"]), "lxml")
            for doc in _recent_first(_documents(rsoup, rel["url"])):
                out.append({"source_url": rel["url"], "document": doc})
    # De-duplicate by document URL, preserving the chosen order.
    seen, uniq = set(), []
    for cand in out:
        key = cand["document"]["url"]
        if key not in seen:
            seen.add(key)
            uniq.append(cand)
    return uniq


def _ordered_related(related: list[dict]) -> list[dict]:
    """ACTIVE related apps, rezoning first (that folder holds the master materials)."""
    active = [r for r in related if r.get("status") == "ACTIVE"]

    def rank(r):
        t = (r.get("permit_type") or "").lower()
        return next((i for i, kw in enumerate(_REL_PRIORITY) if kw in t), len(_REL_PRIORITY))

    return sorted(active, key=rank)


def _recent_first(docs: list[dict]) -> list[dict]:
    """Newest first: dated documents by title date (desc), then undated newest-last reversed."""
    dated = [(d, _find_date(d.get("title") or "")) for d in docs]
    with_date = sorted(((when, d) for d, when in dated if when), key=lambda x: x[0], reverse=True)
    without = [d for d, when in dated if not when]
    return [d for _, d in with_date] + list(reversed(without))


# --------------------------------------------------------------------------- #
# list pagination (identical Prospero pager to Victoria)
# --------------------------------------------------------------------------- #
def _list_pages(session, limit: int | None, use_browser: bool) -> list[str]:
    if use_browser:
        try:
            return _browser_list_pages(limit)
        except ImportError:
            print(
                "langley: Playwright not installed; falling back to the first list page only. "
                "Install the [browser] extra and run 'playwright install chromium' for full "
                "pagination.",
                file=sys.stderr,
            )
    return [_cached_get(session, LIST_URL, use_cache=True)]


# Prospero's pager calls __doPostBack but never emits it; inject the stock implementation
# before driving the pager (idempotent, re-applied after each postback reloads the page).
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
    """Drive the ASP.NET pager with a headless browser; return each page's grid HTML."""
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
                page.evaluate(_INJECT_DOPOSTBACK)
                html = page.content()
                first = _first_folder(html)
                if not first or first == prev_first:
                    break
                htmls.append(html)
                prev_first = first
                collected += len(parse_list(html))
                if limit and collected >= limit:
                    break
                page_no += 1
                if not _browser_goto_page(page, page_no, first):
                    break
        finally:
            browser.close()
    return htmls


def _browser_goto_page(page, page_no: int, prev_first: str) -> bool:
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
    el = BeautifulSoup(html, "lxml").select_one("div.form-result .search_folderNo")
    return el.get_text(strip=True).upper() if el else None


# --------------------------------------------------------------------------- #
# session + cache
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


def _abs(base_url: str, href: str) -> str:
    """Resolve a FileDownload/Details href against the Prospero folder base."""
    if href.lower().startswith("http"):
        return href
    return f"{DETAIL_BASE.rsplit('/', 1)[0]}/{href.lstrip('./').split('/')[-1]}"


def _norm_address(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", s).strip() or None


def _find_date(text: str) -> str | None:
    m = re.search(r"([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})", text or "")
    return _parse_date(m.group(1)) if m else None


def _parse_date(s: str) -> str | None:
    s = re.sub(r"\bSept\.?\b", "Sep", s.strip())
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None
