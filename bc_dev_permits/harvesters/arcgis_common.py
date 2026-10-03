"""Shared plumbing for ArcGIS REST harvesters (Coquitlam, Maple Ridge, ...).

These apps (ArcGIS Experience Builder, VertiGIS Studio Web, Web AppViewer) are all UIs
over ArcGIS FeatureServer/MapServer services, so the retrieval mechanics - a retrying
session, a cached paged /query, polygon centroids, epoch-date conversion - are identical.
Keep municipality-specific field mapping in each harvester; keep the transport here.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
import time
from typing import TYPE_CHECKING

from bc_dev_permits import config

if TYPE_CHECKING:
    import requests

CACHE_DIR = str(config.HTTP_CACHE_DIR)


def build_session() -> requests.Session:
    """Return a requests Session that retries transient timeouts / 5xx with backoff."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.headers.setdefault("User-Agent", config.USER_AGENT)
    retry = Retry(total=config.HTTP_RETRIES, connect=config.HTTP_RETRIES, read=config.HTTP_RETRIES,
                  backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def cached_get_json(session, url: str, params: dict, cache_prefix: str, use_cache: bool = True) -> dict:
    """GET a JSON endpoint, backed by a TTL file cache keyed by url+params."""
    key = hashlib.sha256((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:16]
    path = os.path.join(CACHE_DIR, f"{cache_prefix}_{key}.json")
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < config.HTTP_CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    text = session.get(url, params=params, timeout=config.HTTP_TIMEOUT).text
    data = json.loads(text)  # parse before caching so an error page is never cached as a page
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return data


def query_all(session, layer_url: str, where: str, out_fields: str, cache_prefix: str,  # noqa: PLR0913
              *, return_geometry: bool = False, out_sr: int = 4326, page_size: int = 2000,
              use_cache: bool = True) -> list[dict]:
    """Page a FeatureServer/MapServer layer's /query and return every matching feature."""
    features: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where, "outFields": out_fields, "f": "json",
            "returnGeometry": "true" if return_geometry else "false",
            "resultOffset": str(offset), "resultRecordCount": str(page_size),
            "orderByFields": "OBJECTID",
        }
        if return_geometry:
            params["outSR"] = str(out_sr)
        data = cached_get_json(session, f"{layer_url}/query", params, cache_prefix, use_cache)
        batch = data.get("features") or []
        features.extend(batch)
        # Advance by what the server actually returned (it may cap a page below page_size);
        # keep going only while it signals more records remain.
        offset += len(batch)
        if not batch or not data.get("exceededTransferLimit"):
            break
    return features


def centroid(geometry: dict | None) -> tuple[float | None, float | None]:
    """Average of a polygon's ring vertices -> (latitude, longitude), or (None, None).

    ArcGIS rings are closed (the last vertex repeats the first), so that duplicate is
    dropped before averaging or it would weight one corner twice.
    """
    pts = []
    for ring in (geometry or {}).get("rings") or []:
        vertices = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        pts.extend(vertices)
    if not pts:
        return None, None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return round(lat, 6), round(lon, 6)


def epoch_ms_to_date(ms) -> str | None:
    """ArcGIS epoch milliseconds -> 'YYYY-MM-DD', or None."""
    if ms in (None, ""):
        return None
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).strftime("%Y-%m-%d")
