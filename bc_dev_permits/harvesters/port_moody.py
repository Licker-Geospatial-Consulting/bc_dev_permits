"""City of Port Moody harvester - ArcGIS Web AppViewer, cross-referenced to Engage Port Moody.

Port Moody's "Development Applications" map is an ArcGIS Web AppViewer over one hosted
FeatureServer (see references/arcgis_webmaps.md):

  layer 0  DevelopmentApplicationsView  - one polygon per application folder (dev_permit rows)
  table 1  Milestones                   - process milestones keyed by FOLDER_NUMBER

The map record carries an address, folder/project number, type, status, and dates, but its
Purpose text is usually thin or empty for the large rezoning/OCP files - the ones with real
building programs. Those programs are published instead on the city's engagement site,
Engage Port Moody (https://engage.portmoody.ca/projects), as a project page per site. So for
each application we look for an Engage project at the same civic address and, when one
matches, parse its description for the development fields (units, storeys, parking, tenure,
class) that the map lacks. Engage prose is parsed by the same deterministic parser as every
other source (bc_dev_permits.features); nothing here calls a model.

Attribute -> schema mapping (layer 0):
  Folder_Number           -> permit_id (e.g. "ORD00008")
  Address (first line)     -> address ("PORT MOODY BC <postal>" tail dropped)
  Type                     -> permit_type (e.g. "MINOR DEVELOPMENT PERMIT")
  Folder_Status            -> status
  Subject / Purpose        -> raw_text, parsed for fields when it carries prose
  Application_Date (epoch) -> "Application Received" milestone
  geometry (polygon)       -> latitude/longitude (ring centroid)

The Applicant and File_Manager attributes are personal / staff names; the schema has no
column for them and we do not surface PII, so they are never requested or stored
(see CLAUDE.md data handling).
"""

from __future__ import annotations

import hashlib
import os
import re
import time

from bc_dev_permits import config, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "port_moody"
APP_URL = config.PORT_MOODY_APP_URL
SERVICE_URL = config.PORT_MOODY_SERVICE_URL
APPLICATIONS_LAYER = f"{SERVICE_URL}/0"
MILESTONES_TABLE = f"{SERVICE_URL}/1"
ENGAGE_PROJECTS_URL = config.PORT_MOODY_ENGAGE_PROJECTS_URL
ENGAGE_BASE = config.PORT_MOODY_ENGAGE_BASE

# Request only the fields we map; omit Applicant and File_Manager (PII / staff names).
_APP_FIELDS = ("Folder_Number,Address,Type,Project_Number,Subject,Purpose,"
               "Folder_Status,Application_Date,Folder_Completion_Date")
MIN_PROSE_CHARS = 25  # below this a Purpose carries no usable field text

# Street-type suffixes dropped when picking an address's distinctive name token. "ST" is also a
# Saint prefix (St Johns, St George), so it is handled positionally, not by this set alone.
_STREET_TYPES = frozenset({
    "ST", "STREET", "AVE", "AV", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "BLVD", "BOULEVARD",
    "WAY", "CRT", "COURT", "CT", "CRES", "CRESCENT", "CIRCLE", "CIR", "LANE", "LN", "PL",
    "PLACE", "PLZ", "PLAZA", "HWY", "HIGHWAY", "GATE", "GREEN", "GROVE", "MEWS", "BAY",
    "DIVERSION", "CONNECTOR", "TERRACE", "TERR", "ROW", "WALK", "WYND", "CLOSE",
})
# Generic tokens that never identify a street on their own.
_GENERIC_TOKENS = _STREET_TYPES | {"AND", "THE", "OF", "A", "PORTION", "BLOCK", "UNIT"}
_MIN_KEY_LEN = 4  # a distinctive street token (electronic, spring, johns) is at least this long
_CIVIC_TOLERANCE = 30  # a map civic number this close to an Engage number counts as the same site

_OCC_BY_CLASS = {
    "residential": ["residential"],
    "commercial": ["commercial"],
    "industrial": ["industrial"],
    "institutional": ["institutional"],
    "mixed": ["residential", "commercial"],
    "unknown": [],
}


def _clean(value) -> str | None:
    """Collapse whitespace; '' -> None."""
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _address_line(raw) -> str | None:
    """First line of the ArcGIS Address (the civic address), dropping the city/postal tail."""
    if not raw:
        return None
    first = str(raw).split("\r\n")[0].split("\n")[0]
    return _clean(first)


# --------------------------------------------------------------------------- #
# Address <-> Engage slug matching
# --------------------------------------------------------------------------- #
def _civic_and_key(address_line: str | None) -> tuple[int | None, str | None]:
    """Parse '(civic number, distinctive street token)' from a civic address line.

    "80 ELECTRONIC AVE"       -> (80, "electronic")
    "2805 ST JOHNS STREET"    -> (2805, "johns")   # Saint prefix is not the street name
    "A205B-205 MOUNT ROYAL DR" -> (205, "royal")
    " HENRY ST"               -> (None, "henry")   # no civic number present
    """
    if not address_line:
        return None, None
    tokens = address_line.upper().split()
    civic = None
    name_start = 0
    for i, tok in enumerate(tokens):
        m = re.search(r"\d+", tok)
        if m:
            civic = int(m.group())
            name_start = i + 1
            break
    name_tokens = [t for t in tokens[name_start:] if t.isalpha()]
    # Drop a trailing street-type suffix (AVE, STREET, DR); keep the name words before it.
    while name_tokens and name_tokens[-1] in _STREET_TYPES:
        name_tokens.pop()
    key = name_tokens[-1].lower() if name_tokens else None
    if key and len(key) < _MIN_KEY_LEN:
        key = None
    return civic, key


def _slug_signature(slug: str) -> tuple[set[str], list[int], list[tuple[int, int]]]:
    """From an Engage slug, return (name tokens, standalone numbers, numeric ranges).

    "3060-3092-spring-street-and-80-85-electronic-avenue-..." ->
      names  {spring, street, electronic, avenue, and, ...}
      nums   [3060, 3092, 80, 85]
      ranges [(3060, 3092), (80, 85)]   # adjacent "a-b" numbers read as an address range
    """
    tokens = [t for t in slug.strip("/").split("-") if t]
    names = {t.lower() for t in tokens if t.isalpha() and len(t) >= 2}
    nums = [int(t) for t in tokens if t.isdigit()]
    ranges: list[tuple[int, int]] = []
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if a.isdigit() and b.isdigit() and int(a) < int(b) < int(a) + 200:
            ranges.append((int(a), int(b)))
    return names, nums, ranges


def _civic_distance(civic: int, nums: list[int], ranges: list[tuple[int, int]]) -> int | None:
    """Smallest gap between a civic number and an Engage project's numbers (0 = exact / in range)."""
    if any(lo <= civic <= hi for lo, hi in ranges):
        return 0
    if not nums:
        return None
    return min(abs(civic - n) for n in nums)


def match_engage_slug(address_line: str | None, index: dict[str, dict]) -> str | None:
    """Return the Engage slug whose project is at the same civic address, or None.

    Requires the address's distinctive street token to appear in the slug and the civic
    number to fall on (or within _CIVIC_TOLERANCE of) one of the slug's numbers.
    """
    civic, key = _civic_and_key(address_line)
    if civic is None or not key:
        return None
    best_slug, best_dist = None, None
    for slug, sig in index.items():
        if key not in sig["names"]:
            continue
        dist = _civic_distance(civic, sig["nums"], sig["ranges"])
        if dist is None or dist > _CIVIC_TOLERANCE:
            continue
        if best_dist is None or dist < best_dist:
            best_slug, best_dist = slug, dist
    return best_slug


# --------------------------------------------------------------------------- #
# Engage Port Moody (static HTML)
# --------------------------------------------------------------------------- #
def _cached_get_html(session, url: str, cache_prefix: str, use_cache: bool = True) -> str:
    """GET url as text, backed by the same TTL file cache the ArcGIS transport uses."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = os.path.join(ag.CACHE_DIR, f"{cache_prefix}_{key}.html")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    text = session.get(url, timeout=config.HTTP_TIMEOUT).text
    os.makedirs(ag.CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def engage_index(session, use_cache: bool = True) -> dict[str, dict]:
    """Scrape /projects into {slug: {'title', 'names', 'nums', 'ranges'}} for address-bearing slugs.

    Only slugs that carry a civic number (an address-based project, not a budget/plan page) are
    kept, since matching is by street number and name.
    """
    from bs4 import BeautifulSoup

    html = _cached_get_html(session, ENGAGE_PROJECTS_URL, "port_moody_engage_list", use_cache)
    soup = BeautifulSoup(html, "lxml")
    index: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not (href.startswith("/") and href.count("/") == 1 and "?" not in href):
            continue
        slug = href.strip("/")
        if not slug or not any(ch.isdigit() for ch in slug) or slug in index:
            continue
        names, nums, ranges = _slug_signature(slug)
        if not nums:
            continue
        index[slug] = {"title": _clean(a.get_text(" ", strip=True)), "names": names,
                       "nums": nums, "ranges": ranges}
    return index


def engage_description(session, slug: str, use_cache: bool = True) -> str | None:
    """Return the project page's description prose (the #project_description_text region)."""
    from bs4 import BeautifulSoup

    url = f"{ENGAGE_BASE}/{slug}"
    html = _cached_get_html(session, url, "port_moody_engage_page", use_cache)
    root = BeautifulSoup(html, "lxml").select_one("#project_description_text")
    if root is None:
        return None
    text = _description_text(root)
    return _clean(text) if text else None


def _description_text(root) -> str:
    """One clean copy of the description body from a #project_description_text region.

    EngagementHQ renders the body twice (a `.truncated-description` preview and a hidden
    `.full-description` copy) alongside the page title and social-share links. Parsing the
    whole region would double every count (e.g. "48 apartments" read as 96), so take just the
    full body copy; if the split is absent, drop the title/share noise and take what remains.
    """
    node = root.select_one(".full-description") or root.select_one(".truncated-description")
    if node is None:
        for junk in root.select("h1, .SocialSharing"):
            junk.decompose()
        node = root
    return re.sub(r"\n{2,}", "\n", node.get_text("\n", strip=True))


# Rich fields we trust from Engage prose. occupancy_types is deliberately not overlaid (Engage
# parking prose misparses into an "industrial" occupancy); occupancy is derived from the final
# development_class instead. unit_mix is overlaid but filtered to genuine dwelling keys (see
# _dwelling_mix), so a parking misparse like {"commercial": 181} never lands as a unit mix.
_ENGAGE_OVERLAY_FIELDS = (
    "units_total", "number_of_stories", "parking_vehicle_stalls", "parking_bike_stalls",
    "parking_notes", "floor_area", "floor_area_unit", "rental_or_strata", "rental_subtype",
    "zoning_density",
)
# Keys that represent an actual dwelling split (bedroom counts or structural dwelling types).
# Use/tenure words (commercial, office, strata, rental, visitor) are excluded: they are either a
# parking-prose misparse or already captured by development_class / rental_or_strata.
_DWELLING_MIX_KEYS = frozenset({
    "studio", "suite", "townhouse", "apartment", "principal", "lock-off", "secondary-suite",
    "detached",
})


def _dwelling_mix(mix: dict | None) -> dict | None:
    """Keep only genuine dwelling-mix keys (bedroom counts or dwelling types), else None."""
    if not mix:
        return None
    kept = {k: v for k, v in mix.items() if k.endswith("-bed") or k in _DWELLING_MIX_KEYS}
    return kept or None


def _overlay_engage(row: dict, engage_fields: dict) -> None:
    """Fill the trusted rich fields from Engage where the map record left them empty."""
    for field in _ENGAGE_OVERLAY_FIELDS:
        value = engage_fields.get(field)
        if features.has_value(value) and not features.has_value(row.get(field)):
            row[field] = value
    mix = _dwelling_mix(engage_fields.get("unit_mix"))
    if mix and not features.has_value(row.get("unit_mix")):
        row["unit_mix"] = mix
    # development_class: prefer a concrete class from Engage over an unknown from the map.
    if row.get("development_class") in (None, "unknown") and engage_fields.get("development_class") not in (None, "unknown"):
        row["development_class"] = engage_fields["development_class"]


# --------------------------------------------------------------------------- #
# Row assembly
# --------------------------------------------------------------------------- #
def _milestones(attrs: dict, extra: list[dict]) -> list[dict]:
    """Application dates from the map record plus any milestone rows from table 1.

    Deduplicated by (name, date) and ordered by date: a master folder spans several parcels,
    so the Milestones table repeats each milestone once per related feature.
    """
    out = []
    received = ag.epoch_ms_to_date(attrs.get("Application_Date"))
    if received:
        out.append({"milestone": "Application Received", "milestone_date": received})
    out.extend(extra)
    completed = ag.epoch_ms_to_date(attrs.get("Folder_Completion_Date"))
    if completed:
        out.append({"milestone": "Folder Completed", "milestone_date": completed})
    return _dedup_milestones(out)


def _dedup_milestones(milestones: list[dict]) -> list[dict]:
    """Collapse (name, date) duplicates and order by date."""
    seen = set()
    unique = []
    for m in milestones:
        key = (m["milestone"], m["milestone_date"])
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return sorted(unique, key=lambda m: m["milestone_date"])


def _raw_text(subject: str | None, purpose: str | None, engage_url: str | None,
              engage_desc: str | None) -> str | None:
    """Store the map's own text plus, when matched, the Engage source and its description."""
    parts = [p for p in (subject, purpose) if p]
    if engage_url:
        parts.append(f"Engage: {engage_url}")
    if engage_desc:
        parts.append(engage_desc)
    return "\n\n".join(parts) or None


def row_from_feature(feature: dict, milestone_rows: list[dict]) -> dict | None:
    """Map one application feature (+ its milestone rows) to a base dev_permit row."""
    attrs = feature.get("attributes") or {}
    folder = _clean(attrs.get("Folder_Number"))
    if not folder:
        return None
    lat, lon = ag.centroid(feature.get("geometry"))
    subject = _clean(attrs.get("Subject"))
    purpose = _clean(attrs.get("Purpose"))
    # The Purpose is the map's only prose; parse it for whatever fields it carries (often none).
    prose_source = purpose if purpose and len(purpose) >= MIN_PROSE_CHARS else ""
    prose = features.extract_all(prose_source)

    return {
        "municipality": MUNICIPALITY,
        "permit_id": folder,
        "source_url": APP_URL,
        "development_name": None,
        "address": _address_line(attrs.get("Address")),
        "latitude": lat,
        "longitude": lon,
        "permit_type": _clean(attrs.get("Type")),
        "development_class": prose["development_class"],
        "occupancy_types": [],  # set from the final class after any Engage overlay
        "status": _clean(attrs.get("Folder_Status")),
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
        "raw_text": _raw_text(subject, purpose, None, None),
        "extraction_method": "arcgis",
        "milestones": _milestones(attrs, milestone_rows),
        "documents": [],
        "is_parsed": bool(prose_source),
        "needs_pdf_extraction": False,
        "_subject": subject,  # kept for raw_text rebuild after enrichment; stripped before return
        "_purpose": purpose,
    }


def finalize(row: dict) -> dict:
    """Derive occupancy from the final class, score confidence, and drop scratch keys."""
    occ = _OCC_BY_CLASS.get(row.get("development_class") or "unknown", [])
    row["occupancy_types"] = occ
    # When Engage stated a per-use floor area (e.g. 885 m2 commercial), carry it as a
    # structured occupancies list so dev_permit_occupancy keeps the per-use area (load.py
    # prefers this over the bare occupancy_types).
    areas = row.pop("_engage_areas", None)
    if areas:
        row["occupancies"] = [{"occupancy": o, **areas.get(o, {})} for o in occ]
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = (
        row["extraction_confidence"] < 0.6
        or row.get("development_class") in (None, "unknown")
        or row["extraction_method"] != "arcgis"  # Engage-enriched rows get a human check
    )
    row.pop("_subject", None)
    row.pop("_purpose", None)
    return row


def dedupe_by_folder(features_list: list[dict]) -> list[dict]:
    """One feature per Folder_Number - the richest (longest Purpose, with geometry)."""
    best: dict[str, dict] = {}

    def richness(feat: dict) -> tuple[int, int]:
        attrs = feat.get("attributes") or {}
        has_geom = 1 if feat.get("geometry") else 0
        return has_geom, len(str(attrs.get("Purpose") or ""))

    for feat in features_list:
        folder = _clean((feat.get("attributes") or {}).get("Folder_Number"))
        if not folder:
            continue
        if folder not in best or richness(feat) > richness(best[folder]):
            best[folder] = feat
    return list(best.values())


def parse_applications(app_features: list[dict], milestones_by_folder: dict[str, list[dict]]) -> list[dict]:
    """Map application features (+ grouped milestones) into base dev_permit rows (no network).

    Rows are not yet finalized: Engage enrichment may still change development_class, so
    occupancy and confidence are set later by finalize(). Deduplicates features by folder.
    """
    rows = []
    for feat in dedupe_by_folder(app_features):
        folder = _clean((feat.get("attributes") or {}).get("Folder_Number"))
        row = row_from_feature(feat, milestones_by_folder.get(folder, []))
        if row:
            rows.append(row)
    return rows


def group_milestones(milestone_features: list[dict]) -> dict[str, list[dict]]:
    """Group table-1 rows into {FOLDER_NUMBER: [{milestone, milestone_date}, ...]}."""
    out: dict[str, list[dict]] = {}
    for f in milestone_features:
        a = f.get("attributes") or {}
        folder = _clean(a.get("FOLDER_NUMBER"))
        name = _clean(a.get("Milestone"))
        if not folder or not name:
            continue
        date = (ag.epoch_ms_to_date(a.get("Milestone_Completion_Date"))
                or ag.epoch_ms_to_date(a.get("Milestone_Start_Date")))
        if date and {"milestone": name, "milestone_date": date} not in out.setdefault(folder, []):
            out[folder].append({"milestone": name, "milestone_date": date})
    return out


def _fetch_milestones(session, folders: list[str], use_cache: bool) -> dict[str, list[dict]]:
    """Bulk-fetch Milestones for the harvested folders, in IN-clause chunks."""
    feats: list[dict] = []
    chunk_size = 80  # keep each IN-clause GET URL well under server length limits
    for i in range(0, len(folders), chunk_size):
        values = ",".join("'" + f.replace("'", "''") + "'" for f in folders[i:i + chunk_size])
        feats += ag.query_all(
            session, MILESTONES_TABLE, f"FOLDER_NUMBER IN ({values})",
            "FOLDER_NUMBER,Milestone,Milestone_Status,Milestone_Start_Date,Milestone_Completion_Date",
            "port_moody_milestones", use_cache=use_cache)
    return group_milestones(feats)


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest Port Moody applications, enriching matched ones from Engage Port Moody."""
    session = session or ag.build_session()
    feats = ag.query_all(session, APPLICATIONS_LAYER, "1=1", _APP_FIELDS, "port_moody_apps",
                         return_geometry=True, use_cache=use_cache)
    applications = dedupe_by_folder(feats)
    if limit:
        applications = applications[:limit]

    folders = [_clean((f.get("attributes") or {}).get("Folder_Number")) for f in applications]
    milestones_by_folder = _fetch_milestones(session, [f for f in folders if f], use_cache)

    rows = parse_applications(applications, milestones_by_folder)
    enrich_from_engage(session, rows, use_cache=use_cache)
    return [finalize(r) for r in rows]


def apply_engage(row: dict, slug: str, description: str | None) -> bool:
    """Overlay one Engage project's parsed fields onto a row (pure; no network).

    Returns True when the row was enriched. The map's own Subject/Purpose and the Engage
    source URL + description are preserved in raw_text so fields can be re-extracted later.
    """
    if not description:
        return False
    _overlay_engage(row, features.extract_all(description))
    # Keep any per-use floor areas (e.g. 885 m2 commercial) so finalize() can attach them.
    areas = features.floor_area_by_use(description)
    if areas:
        row["_engage_areas"] = {u: {"floor_area": d["floor_area"], "floor_area_unit": d["floor_area_unit"]}
                                for u, d in areas.items()}
    engage_url = f"{ENGAGE_BASE}/{slug}"
    row["enrichment_url"] = engage_url  # the Engage page the rich fields were fetched from
    row["raw_text"] = _raw_text(row.get("_subject"), row.get("_purpose"), engage_url, description)
    row["extraction_method"] = "arcgis+engage"
    row["is_parsed"] = True
    return True


# Permit-id prefixes ranked by how canonical they are for a project: the OCP/rezoning master
# outranks a development/variance permit, which outranks a subdivision or temporary-use permit.
_PRIMARY_PREFIX_RANK = {"ORD": 5, "OCP": 5, "REZ": 4, "DP": 3, "DVP": 2, "MDP": 2, "TUP": 1, "SA": 1}


def _engage_app_number(description: str) -> str | None:
    """Return the folder the Engage page names as its application, e.g. 'Application #ORD00006'."""
    m = re.search(r"Application\s*#\s*([A-Za-z]{2,4}\d{3,6})", description)
    return m.group(1).upper() if m else None


def _pick_primary(group: list[dict], description: str) -> dict:
    """Choose the row that best represents the shared project.

    The Engage page usually names its own application number (authoritative); otherwise the
    most canonical permit type wins, then a geocoded row, then the one with the most text.
    """
    app_no = _engage_app_number(description)

    def key(row: dict) -> tuple:
        pid = (row.get("permit_id") or "").upper()
        prefix = re.match(r"[A-Z]+", pid)
        rank = _PRIMARY_PREFIX_RANK.get(prefix.group() if prefix else "", 0)
        is_app = 1 if app_no and pid == app_no else 0
        has_geom = 1 if row.get("latitude") is not None else 0
        return (is_app, rank, has_geom, len(row.get("raw_text") or ""))

    return max(group, key=key)


def _merge_related(primary: dict, secondaries: list[dict]) -> None:
    """Fold sibling applications at the same project into the primary (keep it geocodable).

    The duplicates' identity, type, address, status and milestones are preserved on the
    primary - as a related_permits list and an appended raw_text block - instead of being
    stored as separate rows that repeat the same Engage program.
    """
    related = [{
        "permit_id": s.get("permit_id"),
        "permit_type": s.get("permit_type"),
        "address": s.get("address"),
        "status": s.get("status"),
        "purpose": s.get("_purpose"),
    } for s in secondaries]
    primary["related_permits"] = related

    # Keep the project geocodable: if the primary folder has no polygon, borrow a sibling's
    # centroid (same site, so close enough for a project marker).
    if primary.get("latitude") is None:
        donor = next((s for s in secondaries if s.get("latitude") is not None), None)
        if donor:
            primary["latitude"] = donor["latitude"]
            primary["longitude"] = donor["longitude"]

    merged = list(primary.get("milestones") or [])
    for s in secondaries:
        merged.extend(s.get("milestones") or [])
    primary["milestones"] = _dedup_milestones(merged)

    lines = ["Related applications at this project:"]
    for r in related:
        bits = [b for b in (r["permit_id"], r["permit_type"], r["address"]) if b]
        if r["purpose"]:
            bits.append(r["purpose"])
        lines.append(" - " + " | ".join(bits))
    primary["raw_text"] = "\n\n".join(p for p in (primary.get("raw_text"), "\n".join(lines)) if p)


def consolidate_group(group: list[dict], slug: str, description: str | None) -> tuple[dict | None, list[dict]]:
    """Enrich a project group's primary and fold its siblings in (pure; no network).

    Returns (primary, secondaries): the enriched representative row, and the sibling rows now
    folded into it that the caller should drop. ``(None, [])`` when the page was unavailable.
    """
    if not description:
        return None, []
    primary = _pick_primary(group, description)
    apply_engage(primary, slug, description)
    secondaries = [r for r in group if r is not primary]
    if secondaries:
        _merge_related(primary, secondaries)
    return primary, secondaries


def enrich_from_engage(session, rows: list[dict], use_cache: bool = True) -> tuple[int, int]:
    """Enrich rows from Engage and consolidate sibling applications of one project.

    Rows matching the same Engage project are grouped: the primary carries the enriched
    program once, the rest are folded into it (related_permits + merged milestones) and
    removed, so one geocodable record represents the project. Returns (enriched, merged).
    Mutates `rows` in place.
    """
    index = engage_index(session, use_cache=use_cache)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        slug = match_engage_slug(row.get("address"), index)
        if slug:
            groups.setdefault(slug, []).append(row)

    descriptions: dict[str, str | None] = {}  # fetch each project page once
    enriched, merged = 0, 0
    dropped: set[int] = set()
    for slug, group in groups.items():
        if slug not in descriptions:
            descriptions[slug] = engage_description(session, slug, use_cache=use_cache)
        primary, secondaries = consolidate_group(group, slug, descriptions[slug])
        if primary is None:
            continue  # page unavailable: leave the group as plain map rows
        enriched += 1
        dropped.update(id(r) for r in secondaries)
        merged += len(secondaries)

    if dropped:
        rows[:] = [r for r in rows if id(r) not in dropped]
    return enriched, merged
