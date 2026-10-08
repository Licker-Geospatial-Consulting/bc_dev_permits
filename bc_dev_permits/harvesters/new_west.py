"""City of New Westminster harvester - ArcGIS "Projects on the Go" + Be Heard engagement prose.

New Westminster publishes development applications through an ArcGIS Experience Builder app
("Projects on the Go", POG). The app embeds Dashboards over one hosted FeatureServer layer, so the
harvester queries that layer directly (see references/arcgis_webmaps.md) rather than scraping the
app. Each project row already consolidates a site's several application files into one feature.

Attribute -> schema mapping (POG FeatureServer/0):
  Files     -> permit_id (the application numbers, e.g. "RZ000204, DP000860")
  Project_Address    -> address           Project_Type -> development_class + permit-type hint
  Project_Description -> raw_text, parsed  Status_Edit  -> status (the readable status)
  Last_Updated (epoch ms) -> "Last Updated" milestone   geometry -> latitude/longitude (centroid)
  Hyperlink -> the Be Heard New West engagement link (enrichment, when present)

Project_Planner (a staff name) is PII with no schema column, so it is never requested or stored.

Some projects carry a **Be Heard New West** link whose page has richer prose than the terse
Project_Description (floor space ratio, parking and bicycle stall counts). The rule, per the
source: if a project has a link, render and parse whatever attributes its page states; if it has
no link, or the link resolves to a "page not found", do not parse further - the map row stands on
its own. The engagement site is a JavaScript single-page app, so the page is rendered with a
headless browser (Playwright, the [browser] extra) and cached to disk.
"""

from __future__ import annotations

import re

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "new_west"
SERVICE_URL = config.NEW_WEST_SERVICE_URL
APP_URL = config.NEW_WEST_APP_URL
_OUT_FIELDS = ("Item,Files,Project_Address,Project_Type,Project_Description,Status_Edit,"
               "Last_Updated,Hyperlink")
MIN_PROSE_CHARS = 20

# Project_Type -> development_class. New West's categories map straight onto the schema class.
_CLASS_BY_TYPE = {
    "residential - above 6 units": "residential",
    "residential - 6 units and less": "residential",
    "mixed use": "mixed",
    "commercial": "commercial",
    "institutional": "institutional",
    "industrial": "industrial",
}
_OCC_BY_CLASS = {
    "residential": ["residential"], "commercial": ["commercial"], "industrial": ["industrial"],
    "institutional": ["institutional"], "mixed": ["residential", "commercial"], "unknown": [],
}
# Application-file prefixes -> permit type. A project's Files string carries several (a Rezoning, a
# Development Permit, a Heritage application); the distinct recognized types become permit_type.
_TYPE_BY_PREFIX = {
    "RZ": "Rezoning", "OCP": "OCP Amendment",
    "DP": "Development Permit", "DPU": "Development Permit", "DPS": "Development Permit",
    "DPQ": "Development Permit", "DVP": "Development Variance Permit",
    "HER": "Heritage", "HRA": "Heritage Revitalization Agreement",
    "PAR": "Pre-Application Review", "SDP": "Special Development Permit",
    "TUP": "Temporary Use Permit",
}
_FILE_TOKEN = re.compile(r"[A-Za-z]+\d+")


def _clean(value) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _file_numbers(files: str | None) -> list[str]:
    """Return the application file numbers in a project's Files string, in order."""
    return list(dict.fromkeys(m.group(0).upper() for m in _FILE_TOKEN.finditer(files or "")))


def _permit_type(file_nos: list[str]) -> str | None:
    """Map the distinct file-number prefixes to a combined permit type."""
    seen: list[str] = []
    for fn in file_nos:
        label = _TYPE_BY_PREFIX.get(re.match(r"[A-Z]+", fn).group())
        if label and label not in seen:
            seen.append(label)
    return ", ".join(seen) or None


def _development_class(project_type: str | None, prose_class: str) -> str:
    return _CLASS_BY_TYPE.get((project_type or "").strip().lower()) or prose_class


def _row_from_feature(feature: dict) -> dict | None:
    """Map one POG feature to a dev_permit row, parsing its description into fields."""
    attrs = feature.get("attributes") or {}
    file_nos = _file_numbers(attrs.get("Files"))
    permit_id = ", ".join(file_nos) or (f"POG-{attrs.get('Item')}" if attrs.get("Item") else None)
    if not permit_id:
        return None
    lat, lon = ag.centroid(feature.get("geometry"))
    desc = _clean(attrs.get("Project_Description")) or ""
    prose = features.extract_all(desc if len(desc) >= MIN_PROSE_CHARS else "")
    dev_class = _development_class(attrs.get("Project_Type"), prose["development_class"])

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": permit_id,
        "source_url": APP_URL,
        "development_name": None,
        "address": _clean(attrs.get("Project_Address")),
        "latitude": lat,
        "longitude": lon,
        "permit_type": _permit_type(file_nos),
        "development_class": dev_class,
        "occupancy_types": _OCC_BY_CLASS.get(dev_class, []),
        "status": _clean(attrs.get("Status_Edit")),
        "number_of_stories": prose["number_of_stories"],
        "units_total": prose["units_total"],
        "unit_mix": prose["unit_mix"],
        "rental_or_strata": prose["rental_or_strata"],
        "rental_subtype": prose["rental_subtype"],
        "rental_mix": prose["rental_mix"],
        "parking_vehicle_stalls": prose["parking_vehicle_stalls"],
        "parking_bike_stalls": prose["parking_bike_stalls"],
        "parking_notes": prose["parking_notes"],
        "floor_area": prose["floor_area"],
        "floor_area_unit": prose["floor_area_unit"],
        "zoning_density": prose["zoning_density"],
        "height_m": prose["height_m"],
        "raw_text": desc or None,
        "extraction_method": "arcgis",
        "milestones": _milestones(attrs),
        "documents": [],
        "is_parsed": bool(desc),
        "needs_pdf_extraction": False,
        "_beheard_url": _clean(attrs.get("Hyperlink")),
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = row["extraction_confidence"] < 0.6 or dev_class == "unknown"
    return row


def _milestones(attrs: dict) -> list[dict]:
    updated = ag.epoch_ms_to_date(attrs.get("Last_Updated"))
    return [{"milestone": "Last Updated", "milestone_date": updated}] if updated else []


def parse_features(features_list: list[dict]) -> list[dict]:
    """Map POG /query features into dev_permit rows (no network)."""
    rows = [_row_from_feature(f) for f in features_list]
    return [r for r in rows if r]


# --------------------------------------------------------------------------- #
# Be Heard New West engagement cross-reference (richer prose than Project_Description)
# --------------------------------------------------------------------------- #
# Fields the engagement prose may add; a value it parses overlays the map row, an empty one keeps
# whatever the map gave (so the terse Project_Description is never lost).
_BEHEARD_FIELDS = ("units_total", "number_of_stories", "unit_mix", "zoning_density", "height_m",
                   "floor_area", "floor_area_unit", "parking_vehicle_stalls",
                   "parking_bike_stalls", "parking_notes", "rental_or_strata", "rental_subtype",
                   "rental_mix")


def _beheard_description(text: str) -> str | None:
    """Pull the project narrative out of a rendered Be Heard page's body text.

    The page wraps the proposal in site chrome (nav, cookie banner) and trailing engagement
    sections; the narrative sits under "What's being proposed?" between the project heading and the
    "How can I engage?" / "Documents" / "Phases" sections.
    """
    body = text
    start = re.search(r"back to folder|follow\s*\(\d+\)", body, re.IGNORECASE)
    if start:
        body = body[start.end():]
    end = re.search(r"how can i engage|project contact|\bphases\b|\bdocuments\b", body, re.IGNORECASE)
    if end:
        body = body[: end.start()]
    proposed = re.search(r"what'?s being proposed\??\s*(.*)", body, re.IGNORECASE | re.DOTALL)
    desc = (proposed.group(1) if proposed else body).strip()
    return re.sub(r"\s+", " ", desc).strip() or None


def enrich_from_beheard(rows: list[dict], use_cache: bool = True) -> int:
    """Overlay richer fields from each project's Be Heard page. Returns the count enriched.

    Only rows with a link are considered, and only pages that actually exist (a real project page's
    title starts with "Project:"; a missing one shows the generic platform title) are parsed - a
    dead link leaves the map row untouched.
    """
    enriched = 0
    for row in rows:
        url = row.pop("_beheard_url", None)
        if not url:
            continue
        page = _beheard_fetch(url, use_cache=use_cache)
        if not page or not (page.get("title") or "").strip().lower().startswith("project"):
            continue  # no link target, or a "page not found": stop parsing this one
        desc = _beheard_description(page.get("text") or "")
        if not desc:
            continue
        rich = features.extract_all(desc)
        for field in _BEHEARD_FIELDS:
            if features.has_value(rich.get(field)):
                row[field] = rich[field]
        row["raw_text"] = "\n\n".join(p for p in (row.get("raw_text"), desc) if p)
        row["enrichment_url"] = url
        row["extraction_method"] = "arcgis+beheard"
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
        enriched += 1
    # Drop the private matching key from any row that had no link.
    for row in rows:
        row.pop("_beheard_url", None)
    return enriched


def _beheard_fetch(url: str, use_cache: bool = True) -> dict | None:
    """Render a Be Heard page once (title + body text), backed by the TTL file cache."""
    import hashlib
    import json
    import os
    import time

    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(ag.CACHE_DIR, f"new_west_bh_{key}.json")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    page = _render(url)
    if page is not None:
        os.makedirs(ag.CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(page, fh, ensure_ascii=False)
    return page


def _render(url: str) -> dict | None:
    """Render one JavaScript-rendered Be Heard page to {title, text}, or None on failure."""
    from playwright.sync_api import sync_playwright  # optional dep, see pyproject [browser]

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=config.USER_AGENT)
                page.goto(url.strip(), wait_until="networkidle", timeout=60_000)
                page.wait_for_timeout(1_500)
                return {"title": page.title(), "text": page.inner_text("body")}
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 - a dead link or render failure just skips enrichment
        return None


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every New Westminster project from the POG layer, enriching the linked ones."""
    session = session or ag.build_session()
    feats = ag.query_all(session, SERVICE_URL, "1=1", _OUT_FIELDS, "new_west",
                         return_geometry=True, use_cache=use_cache)
    rows = parse_features(feats)
    if limit:
        rows = rows[:limit]
    enrich_from_beheard(rows, use_cache=use_cache)
    # General rule: collapse any duplicate records sharing one civic address into a single record.
    consolidate.consolidate_by_address(rows)
    return rows
