"""City of Coquitlam harvester - ArcGIS Experience Builder over a FeatureServer.

The "Development Information Portal" (an ArcGIS Experience app) is just a UI over one
FeatureServer layer; each clickable polygon is a feature whose attributes carry the
application's prose and metadata. So we skip the map and query the service directly
(see references/arcgis_webmaps.md), then parse each feature's PROJECT_DESCRIPTION with
the shared deterministic parser (bc_dev_permits.features) - the same path as Port Moody.

Attribute -> schema mapping (layer 0 'Development_Information'):
  PROJECT_NUMBER      -> permit_id (e.g. "23-078")
  ADDRESS             -> address
  PROJECT_STATUS      -> status (e.g. "Active Application", "Approved")
  PROJECT_DESCRIPTION -> raw_text, parsed into the permit fields
  SUBMISSION_DATE     -> "Application Received" milestone (epoch ms)
  geometry (polygon)  -> latitude/longitude (ring centroid)

The APPLICANT attribute is a personal name; the schema has no applicant column and we do
not surface PII, so it is deliberately not requested or stored (see CLAUDE.md data handling).
"""

from __future__ import annotations

from bc_dev_permits import config, features
from bc_dev_permits.harvesters import arcgis_common as ag

MUNICIPALITY = "coquitlam"
# ArcGIS Experience Builder app (human-facing source) and the FeatureServer layer behind it.
APP_URL = config.COQUITLAM_APP_URL
LAYER_URL = config.COQUITLAM_SERVICE_URL
# Request only the fields we map; omit APPLICANT (PII) and the SHAPE__* measurement fields.
_OUT_FIELDS = "OBJECTID,PROJECT_NUMBER,PROJECT_DESCRIPTION,PROJECT_STATUS,SUBMISSION_DATE,ADDRESS"
MIN_PROSE_CHARS = 25  # below this a description carries no usable field text


def _row_from_feature(feature: dict) -> dict | None:
    """Map one ArcGIS feature to a dev_permit row, parsing its description into fields."""
    attrs = feature.get("attributes") or {}
    number = (attrs.get("PROJECT_NUMBER") or "").strip()
    desc = (attrs.get("PROJECT_DESCRIPTION") or "").strip()
    if not number and not desc:
        return None
    lat, lon = ag.centroid(feature.get("geometry"))
    received = ag.epoch_ms_to_date(attrs.get("SUBMISSION_DATE"))
    row = {
        "municipality": MUNICIPALITY,
        "permit_id": number or None,
        "source_url": APP_URL,
        "development_name": None,
        "address": (attrs.get("ADDRESS") or "").strip() or None,
        "latitude": lat,
        "longitude": lon,
        "status": (attrs.get("PROJECT_STATUS") or "").strip() or None,
        "raw_text": desc or None,  # PROJECT_DESCRIPTION only (no APPLICANT / PII)
        "extraction_method": "arcgis",
        "milestones": [{"milestone": "Application Received", "milestone_date": received}] if received else [],
        "documents": [],
        "is_parsed": False,
        "needs_pdf_extraction": False,
        "needs_review": True,
        "extraction_confidence": 0.0,
    }
    if len(desc) >= MIN_PROSE_CHARS:
        row.update(features.extract_all(desc))
        row["is_parsed"] = True
        row["extraction_confidence"] = features.score_confidence(row)
        row["needs_review"] = row["extraction_confidence"] < 0.6
    else:
        row["development_class"] = "unknown"
        row["occupancy_types"] = []
    return row


def parse_features(data: dict) -> list[dict]:
    """Map a FeatureServer /query response into dev_permit rows."""
    rows = [_row_from_feature(f) for f in (data.get("features") or [])]
    return [r for r in rows if r]


def harvest(session=None, limit: int | None = None, use_cache: bool = True) -> list[dict]:
    """Harvest every development application from the Coquitlam FeatureServer (live)."""
    session = session or ag.build_session()
    feats = ag.query_all(session, LAYER_URL, "1=1", _OUT_FIELDS, "coquitlam",
                         return_geometry=True, use_cache=use_cache)
    rows = parse_features({"features": feats})
    return rows[:limit] if limit else rows
