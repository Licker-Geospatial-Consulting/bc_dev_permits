"""City of Delta harvester - Let's Talk Delta (EngagementHQ) building development applications.

Delta publishes development applications on its engagement portal, letstalk.delta.ca (an
EngagementHQ / "Hive" site, the same platform as Engage Port Moody). The /projects page lists every
project as a card carrying a data-project-category. This harvester takes the "Development" category
and keeps only the actual BUILDING development applications - the ones whose "Application Process"
timeline includes a "Building Permit" stage - so a rezoning-only file, a road / utility project, or
a policy page (SSMUH, the OCP update, a grant program) is excluded.

Each kept project page is server-rendered, so plain HTTP + BeautifulSoup reads it (no JS):
  - a description widget labelled "Location:", "File Number:", "Applicant:" + the purpose prose
  - an "Application Process" timeline (.timeline-item: a status, a milestone name, an optional date)
  - a Document Library of download_file PDFs (architectural / landscape plans, reports, ...)

Attribute -> schema mapping:
  File Number   -> permit_id (e.g. LU009764; falls back to the slug, then the latest timeline LU #)
  Location      -> address
  card / <h1>   -> development_name
  purpose prose -> development_class + program (storeys, units, height, FSR), parsed by
                   features.extract_all after Delta-specific cleaning: the prose embeds the civic
                   address and writes revisions as "from A to B", which the generic parser misreads
                   (an address civic number as a unit / stall count, the OLD value of a revision), so
                   the address is removed and each "from A to B <unit>" is reduced to "B <unit>".
  timeline      -> milestones (status + date)
  download_file -> documents (architectural / landscape drawings, staff reports, notices, ...)

The richer program detail (parking, unit mix, exact floor area) lives in the application PDFs, which
are stored as documents; a row missing a valuable field is flagged needs_pdf_extraction for the
Claude-read PDF pass (architectural plans / New Application Report - see tools/delta_enrich.py).

The Applicant label is often a company, but the schema has no applicant column and we do not surface
personal names, so it is not stored as a field (see CLAUDE.md data handling); it survives only inside
the description raw_text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "delta"
PROJECTS_URL = config.DELTA_PROJECTS_URL
BASE = config.DELTA_BASE
CACHE_DIR = str(config.HTTP_CACHE_DIR)

MIN_PROSE_CHARS = 25
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}

# Document role from the library title (checked in order; first match wins).
_DOC_ROLES = (
    ("staff_report", ("application report", "staff report")),
    ("drawings", ("architectural", "landscape", "site plan", "elevation", "rendering", "boards")),
    ("notice", ("notice", "newspaper ad", "public information", "display board", "meeting")),
    ("application", ("arborist", "traffic", "survey", "topography", "subdivision", "geotech",
                     "servicing", "stormwater", "report", "study", "letter")),
)


def _clean(value) -> str | None:
    """Collapse whitespace; '' -> None."""
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


# --------------------------------------------------------------------------- #
# HTTP (shared TTL file cache, same transport the ArcGIS harvesters use)
# --------------------------------------------------------------------------- #
def _cached_get(session, url: str, cache_prefix: str, use_cache: bool = True) -> str:
    """GET url as HTML text, backed by the shared TTL file cache."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"{cache_prefix}_{key}.html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #
def development_candidates(soup) -> list[dict]:
    """Return the 'Development'-category project cards as {slug, name} (the harvest candidates)."""
    out = []
    for card in soup.select(".project.card"):
        try:
            categories = json.loads(card.get("data-project-category") or "[]")
        except ValueError:
            categories = []
        if "Development" not in categories:
            continue
        link = card.find("a", href=re.compile(r"letstalk\.delta\.ca/"))
        slug = link["href"].rstrip("/").split("/")[-1] if link else None
        name = _clean(card.get("data-project-name"))
        if slug:
            out.append({"slug": slug, "name": name})
    return out


# --------------------------------------------------------------------------- #
# Project page
# --------------------------------------------------------------------------- #
def has_building_permit(soup) -> bool:
    """Return True when the Application Process timeline has a 'Building Permit' stage (the filter)."""
    for ti in soup.select(".timeline-item"):
        name = _timeline_name(ti)
        if name and re.fullmatch(r"building permit", name.strip(), re.IGNORECASE):
            return True
    return False


def _timeline_name(ti) -> str | None:
    """Return the milestone name of a timeline item (the first non-screen-reader paragraph)."""
    for p in ti.find_all("p"):
        if "sr-only" in (p.get("class") or []):
            continue
        text = _clean(p.get_text(" ", strip=True))
        if text:
            return text
    return None


def _timeline_status(ti) -> str | None:
    """Complete / active / incomplete, read from the screen-reader 'Timeline item N - <status>'."""
    sr = ti.find("p", class_="sr-only")
    if not sr:
        return None
    m = re.search(r"-\s*(complete|active|incomplete)\b", sr.get_text(" ", strip=True), re.IGNORECASE)
    return m.group(1).lower() if m else None


def _parse_date(text: str | None) -> str | None:
    """Parse the first 'Month D, YYYY' in a timeline date cell -> 'YYYY-MM-DD'."""
    if not text:
        return None
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"


def milestones(soup) -> list[dict]:
    """One milestone per timeline item that carries a date, in page (chronological) order."""
    out = []
    for ti in soup.select(".timeline-item"):
        name = _timeline_name(ti)
        if not name:
            continue
        date_cell = ti.find("div", class_="text-muted")
        date = _parse_date(date_cell.get_text(" ", strip=True) if date_cell else None)
        if date:
            out.append({"milestone": name, "milestone_date": date})
    return out


# A revised application restates its program as several "... Application Overview:" sections. The
# INTRO (everything before the first overview / "Additional information") states the headline once;
# the "(Revised) Application Overview" bullet block lists the detailed stats. The stale "Original
# Application Overview" is never parsed - it would reintroduce the old figures.
_OVERVIEW_BOUNDARY = re.compile(
    r"(?i)\b(?:Revised Application Overview|Updated Application Overview|Original Application Overview|"
    r"Application Overview|Project Overview|Additional information on)\b")
_OVERVIEW_SECTION = re.compile(
    r"(?i)((?:Revised |Updated |Project )?Application Overview[:\s].*?)(?=Original Application Overview|$)")


# A project page can split its description across several hive-block-content blocks - the Location /
# File Number labels in one, the program prose in another - so every such block is read. The rezoning
# / variance blocks ("... includes applications for ...", "Development Variance Permit to: ...") and
# the public-feedback blocks are dropped from the PARSED text: they restate figures (an amenity area
# reduced from 270 to 198 m2, a lot size reduced to 1,498 m2) that would misparse as the program.
_NON_PROGRAM_BLOCK = re.compile(
    r"(?i)^\s*(?:This (?:development|rezoning) proposal includes applications for|"
    r"A public (?:information|open house) meeting|An online survey (?:was|is)|Below is a summary|"
    r"How Can You|The feedback (?:received|we)|Development Variance Permit to:)")
_PROGRAM_SIGNAL = re.compile(
    r"(?i)\bstorey|\bstor(?:ies|y)\b|\bunits?\b|apartment|townhouse|dwelling|floor space|\bFSR\b|"
    r"parking|mixed[- ]use|File Number|Location:")
# The block that states the proposal directly (as opposed to a feedback summary that also discusses
# units / parking). Preferred over bare signal density so a feedback block never wins.
_PROPOSAL_MARK = re.compile(
    r"(?i)the purpose of this application|the (?:City|Corporation) of Delta has received|"
    r"key aspects of the proposal|the applicant (?:is proposing|proposes)|"
    r"application is to (?:permit|allow)|proposal is (?:to|for|a )")


def description_blocks(soup) -> tuple[str, str]:
    """Return (program text, full description text) from the page's hive-block-content blocks.

    A page can split its description across several blocks - the proposal in one, the rezoning /
    variance requests, the OCP-consistency context and the public feedback in others, and sometimes
    the Location / File Number labels in a block outside <main>. The PROGRAM text is the single
    highest-signal proposal block in <main> (most storey / unit / FSR / parking mentions), after
    dropping the variance and feedback blocks: taking one block keeps a context block's "mixed-use
    NC designation" or a feedback block's parking debate from skewing the program. The full text is
    every hive-block-content block, kept only for the Location / File Number labels and raw_text.
    Only hive-block-content blocks are read, so the Document Library, the timeline and the staff
    contact's personal name in the sidebar are never pulled in.
    """
    main_blocks = soup.select("main .hive-block-content") or soup.select(".hive-block-content")
    scored = []
    for b in main_blocks:
        text = re.sub(r"\s+", " ", b.get_text(" ", strip=True))
        if not text or _NON_PROGRAM_BLOCK.search(text):
            continue
        scored.append((len(_PROGRAM_SIGNAL.findall(text)), len(text), text))
    # Prefer a block that states the proposal; fall back to the highest signal density.
    pool = [s for s in scored if _PROPOSAL_MARK.search(s[2])] or scored
    program = max(pool, key=lambda s: (s[0], s[1]))[2] if pool else ""
    full = " ".join(re.sub(r"\s+", " ", b.get_text(" ", strip=True))
                    for b in soup.select(".hive-block-content") if b.get_text(strip=True))
    return program, full


def description_fields(soup) -> dict:
    """Pull Location / File Number + the program and full description text out of the blocks."""
    program_text, full_text = description_blocks(soup)
    if not (program_text or full_text):
        return {}
    # Location / File Number come from the block that carries the application labels (the one with
    # "File Number"), never a council-meeting venue's "Location:" in a feedback block.
    block_texts = [re.sub(r"\s+", " ", b.get_text(" ", strip=True))
                   for b in soup.select(".hive-block-content") if b.get_text(strip=True)]
    label = next((t for t in block_texts if re.search(r"File (?:Number|No)\.?:?\s*[A-Z]", t)),
                 program_text or full_text)
    location = re.search(
        r"Location:\s*(.+?)\s*(?:File (?:Number|No)\.?:?|Applicant:|The (?:City|Corporation)|$)", label)
    file_no = re.search(r"File (?:Number|No)\.?:?\s*([A-Z]{1,3}\d{3,7})", label)
    addr = _clean(location.group(1)) if location else None
    return {
        "text": _clean(full_text or program_text),
        "program": _clean(program_text or full_text),
        "location": addr if addr and re.search(r"\d", addr) else None,  # an address has a civic number
        "file_number": file_no.group(1) if file_no else None,
    }


def _clean_prose(text: str, location: str | None) -> str:
    """Strip the civic address and resolve 'from A to B' revisions before deterministic parsing.

    Delta's prose embeds the civic address ("the properties at 1146, 1156 and 1170 53A Street") and
    describes a revised application as a set of "from A to B" changes. The generic extractor would
    read an address civic number as a unit / stall count and take the OLD value of a revision, so the
    address text is removed and each "from A to B <unit>" is reduced to "B <unit>".
    """
    if location:
        text = text.replace(location, " ")
    # Drop a "the properties/site/lands at <addresses>" clause up to the verb that follows it.
    text = re.sub(r"(?i)\b(?:propert(?:y|ies)|site|lands?|lots?)\s+(?:located\s+)?at\s+"
                  r"[\d,\s\w]+?(?=\bis\b|\bare\b|\bto\b|\.|,\s+the\b)", " ", text)
    # A revision "from A to B [unit]" keeps only the final value B.
    text = re.sub(r"(?i)\bfrom\s+[\d.,]+\s*(?:m|metres|units?|storeys?|suites?)?\s+to\s+([\d.,]+)",
                  r"to \1", text)
    # A removed quantity ("eliminate 13 lock-off suites") is not part of the proposed mix.
    text = re.sub(r"(?i)\b(?:eliminate|remove|delete)\s+\d+\s+[\w-]+\s+(?:suites?|units?)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Delta states the FSR in several orders - "FSR of 1.36", "floor space ratio to 1.36", and
# "2.95 Floor Space Ratio (FSR)" (number first). A real FSR is a small ratio, so a value above this
# cap is a misread (e.g. "Floor Space Ratio (FSR) 303 residential parking spaces" -> 303) and dropped.
_MAX_FSR = 20.0
_FSR_AFTER = re.compile(r"(?:FSR|floor space ratio)\s*(?:of|is|to|:|=|\(FSR\))?\s*(\d+\.\d+)", re.IGNORECASE)
_FSR_BEFORE = re.compile(r"(\d+\.\d+)\s*(?:Floor Space Ratio|FSR)\b", re.IGNORECASE)


def _delta_fsr(text: str) -> float | None:
    """Return a plausible FSR from any of Delta's phrasings, or None."""
    for pattern in (_FSR_AFTER, _FSR_BEFORE):
        for m in pattern.finditer(text):
            value = float(m.group(1))
            if 0 < value <= _MAX_FSR:
                return value
    return None


def program_fields(text: str | None, location: str | None) -> dict:
    """Parse the headline from the intro and the detailed stats from the Overview bullets.

    The intro (before the first overview) states units / storeys / height / FSR / class once, so it
    parses cleanly; the "(Revised) Application Overview" block adds the parking and floor-area stats.
    Splitting them avoids the generic extractor summing a figure that the page restates.
    """
    text = text or ""
    intro = _clean_prose(_OVERVIEW_BOUNDARY.split(text, maxsplit=1)[0], location)
    merged = features.extract_all(intro if len(intro) >= MIN_PROSE_CHARS else "")
    m = _OVERVIEW_SECTION.search(text)
    overview = _clean_prose(m.group(1), location) if m else ""
    detail = features.extract_all(overview) if len(overview) >= MIN_PROSE_CHARS else {}
    for f in ("parking_vehicle_stalls", "parking_bike_stalls", "parking_notes",
              "floor_area", "floor_area_unit"):
        if not features.has_value(merged.get(f)) and features.has_value(detail.get(f)):
            merged[f] = detail[f]
    if merged.get("development_class") in (None, "unknown") and \
            detail.get("development_class") not in (None, "unknown"):
        merged["development_class"] = detail["development_class"]
        merged["occupancy_types"] = detail.get("occupancy_types") or merged.get("occupancy_types")
        merged["occupancies"] = detail.get("occupancies") or merged.get("occupancies")
    # FSR, from any Delta phrasing over the whole (cleaned) description, overrides the generic parse
    # (which mishandles the number-first form); drop an implausible generic FSR when none is found.
    fsr = _delta_fsr(_clean_prose(text, location))
    if fsr is not None:
        merged["zoning_density"] = f"FSR {fsr:g}"
    elif merged.get("zoning_density"):
        generic = re.search(r"\d+(?:\.\d+)?", merged["zoning_density"])
        if generic and float(generic.group()) > _MAX_FSR:
            merged["zoning_density"] = None
    return merged


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
def _doc_role(title: str) -> str:
    low = title.lower()
    for role, needles in _DOC_ROLES:
        if any(n in low for n in needles):
            return role
    return "other"


def documents(soup) -> list[dict]:
    """Document Library PDFs: {url, title, doc_role}, de-duplicated by download-file id."""
    out, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/download_file/\d+/\d+")):
        href = a["href"]
        file_id = re.search(r"/download_file/(\d+)/", href).group(1)
        if file_id in seen:
            continue
        seen.add(file_id)
        title = _clean(a.get_text(" ", strip=True)) or ""
        # The library appends " PDF (12.3 MB)" to the title; keep only the human title.
        title = re.sub(r"\s*PDF\s*\([^)]*\)\s*$", "", title).strip()
        url = href if href.startswith("http") else BASE + href
        out.append({"url": url, "title": title or None, "doc_role": _doc_role(title)})
    return out


# --------------------------------------------------------------------------- #
# Row assembly
# --------------------------------------------------------------------------- #
# A valuable field that, when missing, makes the application a candidate for the Claude-read PDF pass.
_PDF_WORTHY = ("parking_vehicle_stalls", "floor_area", "unit_mix")


def parse_project(soup, slug: str, name: str | None) -> dict | None:
    """Map a building-application project page to a dev_permit row (no network)."""
    desc = description_fields(soup)
    permit_id = desc.get("file_number") or _permit_id_fallback(soup, slug)
    name = name or _clean(_page_title(soup))
    parsed = program_fields(desc.get("program"), desc.get("location"))

    docs = documents(soup)
    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": f"{BASE}/{slug}",
        "development_name": name,
        "address": desc.get("location") or _address_from_name(name),
        "latitude": None,
        "longitude": None,
        "permit_type": "Development Application",
        "development_class": parsed["development_class"],
        "occupancy_types": parsed["occupancy_types"],
        "occupancies": parsed["occupancies"],
        "status": _status_from_timeline(soup),
        "number_of_stories": parsed["number_of_stories"],
        "units_total": parsed["units_total"],
        "unit_mix": parsed["unit_mix"],
        "rental_or_strata": parsed["rental_or_strata"],
        "rental_subtype": parsed["rental_subtype"],
        "rental_mix": parsed["rental_mix"],
        "parking_vehicle_stalls": parsed["parking_vehicle_stalls"],
        "parking_bike_stalls": parsed["parking_bike_stalls"],
        "parking_notes": parsed["parking_notes"],
        "floor_area": parsed["floor_area"],
        "floor_area_unit": parsed["floor_area_unit"],
        "height_m": parsed["height_m"],
        "zoning_density": parsed["zoning_density"],
        "raw_text": desc.get("text"),
        "extraction_method": "html",
        "milestones": milestones(soup),
        "documents": docs,
        "is_parsed": bool(desc.get("text")),
    }
    # The application PDFs carry the parking / unit mix / exact floor area; flag the row for the
    # Claude-read PDF pass when a valuable field is missing and a drawing / report PDF exists.
    pdf_docs = any(d["doc_role"] in ("drawings", "staff_report") for d in docs)
    row["needs_pdf_extraction"] = pdf_docs and any(
        not features.has_value(row.get(f)) for f in _PDF_WORTHY)
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = (row["extraction_confidence"] < 0.6
                           or row["development_class"] in (None, "unknown"))
    return row


_NAME_ADDR = re.compile(
    r"\b(\d{2,6}\s+[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Way|Crescent|Cres|Place|Pl|Boulevard|Blvd|"
    r"Lane|Ln|Court|Crt|Trunk Road))\b")


def _address_from_name(name: str | None) -> str | None:
    """Return a civic 'number street' embedded in the project name (the fallback address).

    Used when the page has no Location line (e.g. 'Ladner Landing II - Apartment on 5737 Ladner
    Trunk Road' -> '5737 Ladner Trunk Road').
    """
    if not name:
        return None
    m = _NAME_ADDR.search(name)
    return m.group(1) if m else None


def _page_title(soup) -> str | None:
    for h1 in soup.find_all("h1"):
        text = _clean(h1.get_text(" ", strip=True))
        if text and "document library" not in text.lower():
            return text
    return None


def _permit_id_fallback(soup, slug: str) -> str | None:
    """Use the slug as the file number (lu009764 -> LU009764), else the latest LU # in the timeline."""
    if re.fullmatch(r"[a-z]{1,3}\d{3,7}", slug):
        return slug.upper()
    nums = re.findall(r"\b([A-Z]{1,3}\d{3,7})\b",
                      " ".join(_timeline_name(ti) or "" for ti in soup.select(".timeline-item")))
    return nums[-1] if nums else None


def _status_from_timeline(soup) -> str | None:
    """Status = the name of the active stage, else the last completed stage."""
    items = soup.select(".timeline-item")
    active = next((_timeline_name(ti) for ti in items if _timeline_status(ti) == "active"), None)
    if active:
        return active
    completed = [_timeline_name(ti) for ti in items if _timeline_status(ti) == "complete"]
    return completed[-1] if completed else None


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest Delta's building development applications from Let's Talk Delta.

    Lists the Development-category projects, keeps those whose timeline has a Building Permit stage,
    parses each page, and collapses duplicate civic addresses before returning.
    """
    session = session or ag.build_session()
    listing = _cached_get(session, PROJECTS_URL, "delta_projects", use_cache)
    candidates = development_candidates(_soup(listing))
    if limit:
        candidates = candidates[:limit]

    rows = []
    for cand in candidates:
        try:
            html = _cached_get(session, f"{BASE}/{cand['slug']}", "delta_project", use_cache)
        except OSError:
            continue
        soup = _soup(html)
        if not has_building_permit(soup):
            continue
        row = parse_project(soup, cand["slug"], cand["name"])
        if row:
            rows.append(row)
    consolidate.consolidate_by_address(rows)
    return rows


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml")
