"""City of White Rock harvester - ArcGIS Experience Builder over a hosted FeatureServer.

The "Development Application Information Portal" (an ArcGIS Experience app) is a UI over one
FeatureServer (see references/arcgis_webmaps.md), so the harvester queries the service directly
instead of driving the map:

  layer 1  Development Applications - one polygon per application folder (the dev_permit rows)
  table 3  Milestones              - process milestones keyed by FOLDER_NUMBER (relationship 0)

Each feature's PURPOSE is the prose that carries the building program, parsed with the shared
deterministic parser (bc_dev_permits.features) - the same path as Coquitlam and Port Moody. The
Milestones table is pulled in bulk and joined back on FOLDER_NUMBER, giving each application its
full milestone history on top of the map record's own dates.

Attribute -> schema mapping (layer 1):
  FILE_NUMBER        -> permit_id (e.g. "26-039")
  CIVIC_ADDRESS      -> address (first line; the "WHITE ROCK BC <postal>" tail is dropped). The
                        layer's separate `Address` field is empty for every record, so the civic
                        address is read from CIVIC_ADDRESS.
  TYPE               -> permit_type (e.g. "ZONING AMENDMENT")
  Folder_Status      -> status (RECEIVED / IN-PROGRESS / COMPLETED)
  SUBJECT + PURPOSE  -> raw_text; PURPOSE is parsed into the building program
  Application_Date   -> "Application Received" milestone (epoch ms)
  FOLDER_NUMBER      -> join key to the Milestones table (relationship 0)
  geometry (polygon) -> latitude/longitude (ring centroid)

Manager, Applicant_Name, MostRecent_UserStamp and the created/last_edited user fields are staff /
personal names; the schema has no column for them and we do not surface PII, so they are never
requested or stored (see CLAUDE.md data handling).
"""

from __future__ import annotations

import re

from bc_dev_permits import config, consolidate, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "white_rock"
APP_URL = config.WHITE_ROCK_APP_URL
SERVICE_URL = config.WHITE_ROCK_SERVICE_URL
APPLICATIONS_LAYER = f"{SERVICE_URL}/1"
MILESTONES_TABLE = f"{SERVICE_URL}/3"

# Request only the fields we map; omit Manager / Applicant_Name and the created/edited user fields
# (PII / staff names) and the SHAPE__* measurement fields.
_APP_FIELDS = ("OBJECTID,FILE_NUMBER,FOLDER_NUMBER,CIVIC_ADDRESS,TYPE,SUBJECT,PURPOSE,"
               "Folder_Status,Application_Date,Folder_Completion_Date")
_MILESTONE_FIELDS = ("FOLDER_NUMBER,Milestone,Milestone_Status,Milestone_Start_Date,"
                     "Milestone_Completion_Date")
MIN_PROSE_CHARS = 25  # below this a PURPOSE carries no usable field text


def _clean(value) -> str | None:
    """Collapse whitespace; '' -> None."""
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _address_line(raw) -> str | None:
    """First line of CIVIC_ADDRESS (the civic address), dropping the city/postal tail."""
    if not raw:
        return None
    first = str(raw).split("\r\n")[0].split("\n")[0]
    return _clean(first)


def _raw_text(subject: str | None, purpose: str | None) -> str | None:
    """Store the map's own text (the only prose White Rock publishes)."""
    return "\n\n".join(p for p in (subject, purpose) if p) or None


def _milestones(attrs: dict, extra: list[dict]) -> list[dict]:
    """Application dates from the map record plus the related Milestones-table rows.

    Deduplicated by (name, date) and ordered by date.
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


def group_milestones(milestone_features: list[dict]) -> dict[str, list[dict]]:
    """Group table-3 rows into {FOLDER_NUMBER: [{milestone, milestone_date}, ...]}."""
    out: dict[str, list[dict]] = {}
    for f in milestone_features:
        a = f.get("attributes") or {}
        folder = _clean(a.get("FOLDER_NUMBER"))
        name = _clean(a.get("Milestone"))
        if not folder or not name:
            continue
        date = (ag.epoch_ms_to_date(a.get("Milestone_Completion_Date"))
                or ag.epoch_ms_to_date(a.get("Milestone_Start_Date")))
        entry = {"milestone": name, "milestone_date": date}
        if date and entry not in out.setdefault(folder, []):
            out[folder].append(entry)
    return out


def _fetch_milestones(session, folders: list[str], use_cache: bool) -> dict[str, list[dict]]:
    """Bulk-fetch Milestones for the harvested folders, in IN-clause chunks."""
    feats: list[dict] = []
    chunk_size = 80  # keep each IN-clause GET URL well under server length limits
    for i in range(0, len(folders), chunk_size):
        values = ",".join("'" + f.replace("'", "''") + "'" for f in folders[i:i + chunk_size])
        feats += ag.query_all(
            session, MILESTONES_TABLE, f"FOLDER_NUMBER IN ({values})", _MILESTONE_FIELDS,
            "white_rock_milestones", use_cache=use_cache)
    return group_milestones(feats)


def row_from_feature(feature: dict, milestone_rows: list[dict]) -> dict | None:
    """Map one application feature (+ its milestone rows) to a dev_permit row.

    permit_type / status / address come from the structured attributes; the building program is
    parsed from the PURPOSE prose.
    """
    attrs = feature.get("attributes") or {}
    file_number = _clean(attrs.get("FILE_NUMBER"))
    folder = _clean(attrs.get("FOLDER_NUMBER"))
    if not file_number and not folder:
        return None
    lat, lon = ag.centroid(feature.get("geometry"))
    subject = _clean(attrs.get("SUBJECT"))
    purpose = _clean(attrs.get("PURPOSE"))
    # PURPOSE is the map's only prose; parse it for whatever fields it carries (often thin).
    prose_source = purpose if purpose and len(purpose) >= MIN_PROSE_CHARS else ""
    prose = features.extract_all(prose_source)

    row = {
        "municipality": MUNICIPALITY,
        "permit_id": file_number or folder,
        "source_url": APP_URL,
        "development_name": None,
        "address": _address_line(attrs.get("CIVIC_ADDRESS")),
        "latitude": lat,
        "longitude": lon,
        "permit_type": _clean(attrs.get("TYPE")),
        "development_class": prose["development_class"],
        "occupancy_types": prose["occupancy_types"],
        "occupancies": prose["occupancies"],
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
        "height_m": prose["height_m"],
        "zoning_density": prose["zoning_density"],
        "raw_text": _raw_text(subject, purpose),
        "extraction_method": "arcgis",
        "milestones": _milestones(attrs, milestone_rows),
        "documents": [],
        "is_parsed": bool(prose_source),
        "needs_pdf_extraction": False,
    }
    row["extraction_confidence"] = features.score_confidence(row)
    row["needs_review"] = (row["extraction_confidence"] < 0.6
                           or row["development_class"] in (None, "unknown"))
    return row


def dedupe_by_folder(features_list: list[dict]) -> list[dict]:
    """One feature per FOLDER_NUMBER - the richest (with geometry, longest PURPOSE)."""
    best: dict[str, dict] = {}

    def richness(feat: dict) -> tuple[int, int]:
        attrs = feat.get("attributes") or {}
        has_geom = 1 if feat.get("geometry") else 0
        return has_geom, len(str(attrs.get("PURPOSE") or ""))

    for feat in features_list:
        folder = _clean((feat.get("attributes") or {}).get("FOLDER_NUMBER"))
        if not folder:
            continue
        if folder not in best or richness(feat) > richness(best[folder]):
            best[folder] = feat
    return list(best.values())


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every White Rock development application from the FeatureServer (live)."""
    session = session or ag.build_session()
    feats = ag.query_all(session, APPLICATIONS_LAYER, "1=1", _APP_FIELDS, "white_rock_apps",
                         return_geometry=True, use_cache=use_cache)
    applications = dedupe_by_folder(feats)
    if limit:
        applications = applications[:limit]

    folders = [_clean((f.get("attributes") or {}).get("FOLDER_NUMBER")) for f in applications]
    milestones_by_folder = _fetch_milestones(session, [f for f in folders if f], use_cache)

    rows = []
    for feat in applications:
        folder = _clean((feat.get("attributes") or {}).get("FOLDER_NUMBER"))
        row = row_from_feature(feat, milestones_by_folder.get(folder, []))
        if row:
            rows.append(row)

    consolidate.consolidate_by_address(rows)
    return rows
